import re
from datetime import timedelta
from unittest import mock

from django.db import connection
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.shop import gateways, services
from apps.shop.models import Order, Payment, Product, SandboxTransaction
from apps.shop.tests.helpers import ShopTestCase
from apps.sites.models import Site


class PublicShopTests(ShopTestCase):
    def test_list_and_detail(self):
        Product.objects.create(site=self.site, name="مخفی", slug="hidden", price=1, stock=1, is_active=False)
        r = Client().get("/shop/")
        self.assertContains(r, "کتاب آزمایشی")
        self.assertNotContains(r, "مخفی")
        r = Client().get("/shop/p/book/")
        self.assertContains(r, "۱۵۰٬۰۰۰")
        html = r.content.decode()
        self.assertIn("application/ld+json", html)
        self.assertIn('"@type": "Product"', html.replace('"@type":"Product"', '"@type": "Product"'))
        self.assertEqual(Client().get("/shop/p/hidden/").status_code, 404)
        self.assertIn("no-store", r["Cache-Control"])

    def test_pagination(self):
        for i in range(15):
            Product.objects.create(site=self.site, name=f"م{i}", slug=f"m{i}", price=10, stock=1)
        with override_settings(SHOP_PAGE_SIZE=5):
            r = Client().get("/shop/?page=2")
            self.assertContains(r, "صفحه ۲")
            self.assertContains(r, 'rel="prev"')

    def test_cart_flow_and_server_side_prices(self):
        c = Client()
        self.add_to_cart(c, qty=2)
        self.assertContains(c.get("/shop/cart/"), "۳۰۰٬۰۰۰")
        self.product.price = 200000
        self.product.save()
        self.assertContains(c.get("/shop/cart/"), "۴۰۰٬۰۰۰")  # قیمت همیشه از دیتابیس
        c.post("/shop/cart/update/", {f"qty_{self.product.pk}": "0"})
        self.assertContains(c.get("/shop/cart/"), "خالی")

    def test_cart_quantity_clamped_and_garbage_rejected(self):
        c = Client()
        c.post("/shop/cart/add/", {"product": self.product.pk, "qty": 99999})
        self.assertEqual(list(c.session["shop_cart_%d" % self.site.pk].values()), [20])
        self.assertEqual(c.post("/shop/cart/add/", {"product": "abc", "qty": 1}).status_code, 404)
        self.assertEqual(c.post("/shop/cart/add/", {"product": 99999, "qty": 1}).status_code, 404)

    def test_add_to_cart_requires_csrf(self):
        c = Client(enforce_csrf_checks=True)
        self.assertEqual(c.post("/shop/cart/add/", {"product": self.product.pk, "qty": 1}).status_code, 403)

    def test_empty_cart_checkout_redirects(self):
        self.assertEqual(Client().get("/shop/checkout/").status_code, 302)

    def test_checkout_validation(self):
        c = Client()
        self.add_to_cart(c)
        for bad in ({"phone": "123"}, {"customer_name": "ab"}, {"address": "کوتاه"}, {"email": "bad"}):
            r = c.post("/shop/checkout/", self.checkout_data(**bad))
            self.assertEqual(r.status_code, 400, bad)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 5)

    def test_persian_digits_phone_accepted(self):
        c = Client()
        self.add_to_cart(c)
        r = c.post("/shop/checkout/", self.checkout_data(phone="۰۹۱۲۳۴۵۶۷۸۹"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Order.objects.get().phone, "09123456789")


class PaymentSecurityTests(ShopTestCase):
    def test_successful_payment_marks_paid_via_server_verification(self):
        c, r = self.place_order(qty=2)
        order = Order.objects.get()
        self.assertEqual((order.status, order.total), (Order.Status.PENDING, 300000))
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 3)  # رزرو
        ref, resp = self.pay(c, r)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertIsNotNone(order.paid_at)
        self.assertEqual(Payment.objects.get().status, Payment.Status.SUCCEEDED)
        self.assertContains(resp, "پرداخت شما با موفقیت تأیید شد")

    def test_callback_without_payment_does_not_mark_paid(self):
        # مهاجم مستقیم callback را بدون پرداخت در بانک صدا می‌زند
        c, r = self.place_order()
        ref = re.search(r"/shop/pay/sandbox/([0-9a-f]+)/", r["Location"]).group(1)
        for extra in ("", "?status=OK", "?Status=OK&Authority=x", "?paid=1"):
            c.get(f"/shop/pay/callback/{ref}/{extra}")
        order = Order.objects.get()
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(Payment.objects.get().status, Payment.Status.INITIATED)  # بانک هنوز پرداختی نگزارش کرده؛ مشتری ممکن است در صفحهٔ بانک باشد

    def test_failed_payment_keeps_order_unpaid_and_can_retry(self):
        c, r = self.place_order()
        ref, resp = self.pay(c, r, action="fail")
        order = Order.objects.get()
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertContains(resp, "هنوز تأیید نشده")
        r2 = c.post(reverse("shop:order_pay", args=[order.number, order.token]))
        ref2, resp2 = self.pay(c, r2)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertEqual(Payment.objects.count(), 2)

    def test_amount_mismatch_is_rejected(self):
        c, r = self.place_order()
        ref = re.search(r"/shop/pay/sandbox/([0-9a-f]+)/", r["Location"]).group(1)
        SandboxTransaction.objects.filter(reference=ref).update(state="paid", amount=1)  # بانک مبلغ دیگری گرفته
        c.get(f"/shop/pay/callback/{ref}/")
        self.assertEqual(Order.objects.get().status, Order.Status.PENDING)
        self.assertEqual(Payment.objects.get().status, Payment.Status.REVIEW)  # پولی با مبلغ دیگر گرفته شده ← بررسی دستی، نه «ناموفق»

    def test_finalize_is_idempotent(self):
        c, r = self.place_order()
        ref, _ = self.pay(c, r)
        paid_at = Order.objects.get().paid_at
        services.finalize_payment(ref)
        services.finalize_payment(ref)
        self.assertEqual(Order.objects.get().paid_at, paid_at)
        self.assertEqual(Payment.objects.filter(status="succeeded").count(), 1)

    def test_late_payment_on_cancelled_order_needs_review(self):
        c, r = self.place_order()
        ref = re.search(r"/shop/pay/sandbox/([0-9a-f]+)/", r["Location"]).group(1)
        Order.objects.update(created_at=timezone.now() - timedelta(hours=2))
        self.assertEqual(services.expire_pending(), 1)
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 5)  # موجودی برگشت
        SandboxTransaction.objects.filter(reference=ref).update(state="paid")
        c.get(f"/shop/pay/callback/{ref}/")
        self.assertEqual(Order.objects.get().status, Order.Status.CANCELLED)
        self.assertEqual(Payment.objects.get().status, Payment.Status.REVIEW)

    def test_sandbox_gateway_refused_in_production_mode(self):
        c = Client()
        self.add_to_cart(c)
        with override_settings(DEBUG=False, SHOP_ALLOW_SANDBOX_IN_PRODUCTION=False):
            self.assertEqual(c.get("/shop/checkout/").status_code, 200)
            self.assertContains(c.get("/shop/checkout/"), "درگاه آزمایشی در محیط تولید مجاز نیست")
            r = c.post("/shop/checkout/", self.checkout_data())
            self.assertEqual(r.status_code, 400)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 5)

    def test_unknown_gateway_unavailable(self):
        with override_settings(SHOP_PAYMENT_GATEWAY="zarinpal"), self.assertRaises(gateways.GatewayUnavailable):
            gateways.get_gateway()

    def test_gateway_verify_error_leaves_payment_initiated(self):
        c, r = self.place_order()
        ref = re.search(r"/shop/pay/sandbox/([0-9a-f]+)/", r["Location"]).group(1)
        with mock.patch.object(gateways.SandboxGateway, "verify_transaction", side_effect=gateways.GatewayNetworkError("down")):
            c.get(f"/shop/pay/callback/{ref}/")
        self.assertEqual(Payment.objects.get().status, Payment.Status.INITIATED)
        self.assertEqual(Order.objects.get().status, Order.Status.PENDING)

    def test_sandbox_page_hidden_when_sandbox_disallowed(self):
        c, r = self.place_order()
        with override_settings(DEBUG=False, SHOP_ALLOW_SANDBOX_IN_PRODUCTION=False):
            self.assertEqual(c.get(r["Location"]).status_code, 404)

    def test_order_page_requires_token(self):
        c, r = self.place_order()
        o = Order.objects.get()
        self.assertEqual(Client().get(o.get_public_url()).status_code, 200)
        self.assertEqual(Client().get(f"/shop/order/{o.number}/{'0' * 40}/").status_code, 404)
        self.assertEqual(Client().get(f"/shop/order/{o.number}/x/").status_code, 404)


class StockTests(ShopTestCase):
    def test_cannot_oversell(self):
        c1, c2 = Client(), Client()
        self.add_to_cart(c1, qty=5)
        self.add_to_cart(c2, qty=5)
        self.product.stock = 5
        self.product.save()
        self.assertEqual(c1.post("/shop/checkout/", self.checkout_data()).status_code, 302)
        r = c2.post("/shop/checkout/", self.checkout_data())  # موجودی تمام شده
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "موجودی", status_code=400)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 0)

    def test_failed_order_creation_rolls_back_reservation(self):
        p2 = Product.objects.create(site=self.site, name="دوم", slug="two", price=10, stock=0)
        lines = [{"product": self.product, "qty": 2}, {"product": p2, "qty": 1}]
        with self.assertRaises(services.StockError):
            services.create_order(self.site, lines, {"customer_name": "x", "phone": "0", "address": "y"})
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 5)
        self.assertFalse(Order.objects.exists())

    def test_cancel_restores_stock(self):
        c, r = self.place_order(qty=2)
        order = Order.objects.get()
        services.change_status(order, Order.Status.CANCELLED)
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 5)
        with self.assertRaises(services.ShopError):
            services.change_status(order, Order.Status.PAID)  # بازگشت از لغو ممنوع

    def test_order_keeps_snapshot_after_product_change_or_delete(self):
        c, r = self.place_order()
        self.pay(c, r)
        self.product.name, self.product.price = "نام جدید", 1
        self.product.save()
        item = Order.objects.get().items.get()
        self.assertEqual((item.name, item.unit_price), ("کتاب آزمایشی", 150000))
        self.product.delete()
        self.assertEqual(Order.objects.get().items.get().name, "کتاب آزمایشی")

    def test_expire_command(self):
        from django.core.management import call_command

        self.place_order()
        Order.objects.update(created_at=timezone.now() - timedelta(hours=3))
        call_command("shop_expire_orders", stdout=mock.MagicMock())
        self.assertEqual(Order.objects.get().status, Order.Status.CANCELLED)


class PanelTests(ShopTestCase):
    def test_permissions(self):
        for user, code in ((None, 302), (self.author, 403), (self.admin, 200), (self.superadmin, 200)):
            c = Client()
            if user:
                c.force_login(user)
            for name in ("shop_panel:products", "shop_panel:orders", "shop_panel:product_create"):
                self.assertEqual(c.get(reverse(name)).status_code, code, (user, name))

    def test_product_crud_and_validation(self):
        c = Client()
        c.force_login(self.admin)
        r = c.post(reverse("shop_panel:product_create"), {"name": "جدید", "slug": "", "description": "d", "price": 5000, "stock": 3, "track_stock": "on", "is_active": "on"})
        self.assertEqual(r.status_code, 302)
        p = Product.objects.get(name="جدید")
        self.assertEqual((p.site, p.slug), (self.site, "جدید"))
        for bad in ({"price": 0}, {"price": -5}, {"price": "x"}, {"stock": -1}, {"name": ""}):
            data = {"name": "ب", "slug": "bb", "price": 10, "stock": 1, **bad}
            self.assertEqual(c.post(reverse("shop_panel:product_create"), data).status_code, 400, bad)
        dup = c.post(reverse("shop_panel:product_create"), {"name": "کتاب", "slug": "book", "price": 10, "stock": 1})
        self.assertEqual(dup.status_code, 400)
        c.post(reverse("shop_panel:product_delete", args=[p.pk]))
        self.assertFalse(Product.objects.filter(pk=p.pk).exists())

    def test_posted_site_field_is_ignored(self):
        other = Site.objects.create(name="B", slug="b")
        c = Client()
        c.force_login(self.admin)
        c.post(reverse("shop_panel:product_create"), {"name": "x", "slug": "x1", "price": 10, "stock": 1, "site": other.pk})
        self.assertEqual(Product.objects.get(slug="x1").site, self.site)

    def test_image_upload_validated_and_optimized(self):
        import io

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        c = Client()
        c.force_login(self.admin)
        buf = io.BytesIO()
        Image.new("RGB", (3000, 2000), (10, 120, 200)).save(buf, "JPEG", quality=100)
        good = SimpleUploadedFile("a.jpg", buf.getvalue(), "image/jpeg")
        r = c.post(reverse("shop_panel:product_create"), {"name": "تصویر", "slug": "img", "price": 10, "stock": 1, "image": good})
        self.assertEqual(r.status_code, 302, r.content[:300])
        p = Product.objects.get(slug="img")
        with Image.open(p.image.path) as im:
            self.assertLessEqual(max(im.size), 1600)
        self.addCleanup(lambda: p.image.delete(save=False))
        evil = SimpleUploadedFile("a.jpg", b"<?php system($_GET[0]); ?>", "image/jpeg")
        r = c.post(reverse("shop_panel:product_create"), {"name": "بد", "slug": "evil", "price": 10, "stock": 1, "image": evil})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Product.objects.filter(slug="evil").exists())

    def test_order_status_workflow(self):
        c0, r = self.place_order()
        self.pay(c0, r)
        o = Order.objects.get()
        c = Client()
        c.force_login(self.admin)
        self.assertContains(c.get(reverse("shop_panel:order", args=[o.pk])), "پرداخت‌شده")
        c.post(reverse("shop_panel:order", args=[o.pk]), {"status": "completed"})  # پرش ممنوع
        o.refresh_from_db()
        self.assertEqual(o.status, "paid")
        for st in ("processing", "shipped", "completed"):
            c.post(reverse("shop_panel:order", args=[o.pk]), {"status": st})
        o.refresh_from_db()
        self.assertEqual(o.status, "completed")

    def test_cannot_fake_payment_from_panel(self):
        c0, r = self.place_order()
        o = Order.objects.get()
        c = Client()
        c.force_login(self.superadmin)
        c.post(reverse("shop_panel:order", args=[o.pk]), {"status": "paid"})
        c.post(reverse("shop_panel:order", args=[o.pk]), {"status": "processing"})
        o.refresh_from_db()
        self.assertEqual(o.status, "pending_payment")

    def test_panel_posts_require_csrf(self):
        o = Order.objects.create(site=self.site, total=1, customer_name="x", phone="0", address="y")
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.admin)
        self.assertEqual(c.post(reverse("shop_panel:order", args=[o.pk]), {"status": "cancelled"}).status_code, 403)
        self.assertEqual(c.post(reverse("shop_panel:product_delete", args=[self.product.pk])).status_code, 403)

    def test_api(self):
        r = Client().get("/api/v1/shop/products/")
        self.assertEqual(r.json()["count"], 1)
        self.assertEqual(Client().get("/api/v1/shop/orders/").status_code, 403)
        c = Client()
        c.force_login(self.author)
        self.assertEqual(c.get("/api/v1/shop/orders/").status_code, 403)
        c.force_login(self.admin)
        self.assertEqual(c.get("/api/v1/shop/orders/").status_code, 200)


class SeoTests(ShopTestCase):
    def test_sitemap_and_robots(self):
        sm = Client().get("/sitemap.xml").content.decode()
        self.assertIn("/shop/p/book/", sm)
        self.assertIn("/shop/</loc>", sm)
        Product.objects.update(is_active=False)
        self.assertNotIn("/shop/p/book/", Client().get("/sitemap.xml").content.decode())
        rb = Client().get("/robots.txt").content.decode()
        for path in ("/shop/cart/", "/shop/checkout/", "/shop/pay/"):
            self.assertIn(f"Disallow: {path}", rb)

    def test_meta_and_noindex(self):
        html = Client().get("/shop/p/book/").content.decode()
        self.assertIn('<link rel="canonical"', html)
        self.assertIn('name="description"', html)
        self.assertIn("noindex", Client().get("/shop/cart/").content.decode())

    def test_queries_constant_on_list(self):
        # MaintenanceMiddleware حداکثر هر ۱ ثانیه یک پرس‌وجو (UpdateLock) می‌زند؛ با کندی اجرا (مثلاً زیر coverage) ناپایدار می‌شد.
        from unittest import mock

        from apps.updater import middleware as maintenance

        with mock.patch.dict(maintenance._cache, {"at": float("inf"), "on": False}):
            self._check_queries_constant_on_list()

    def _check_queries_constant_on_list(self):
        for i in range(10):
            Product.objects.create(site=self.site, name=f"م{i}", slug=f"m{i}", price=10, stock=1)
        c = Client()
        c.get("/shop/")
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as a:
            c.get("/shop/")
        for i in range(10, 20):
            Product.objects.create(site=self.site, name=f"م{i}", slug=f"m{i}", price=10, stock=1)
        with CaptureQueriesContext(connection) as b:
            c.get("/shop/")
        self.assertEqual(len(a), len(b))

    def test_healthz(self):
        r = Client().get("/healthz")
        self.assertEqual((r.status_code, r.json()), (200, {"status": "ok"}))
