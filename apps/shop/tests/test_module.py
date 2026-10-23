"""تست‌های ماژول فروشگاه: محصول فیزیکی/دیجیتال، قیمت و تخفیف، موجودی، سفارش، تاریخچه، دسترسی، مهمان/کاربر، درگاه و API."""
import re
import io
import threading
from datetime import timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.shop import gateways, services
from apps.shop.models import Order, OrderEvent, OrderItem, Payment, Product, ProductCategory
from apps.sites.models import Site

from .helpers import ShopTestCase


def digital(site, **kw):
    f = SimpleUploadedFile("guide.pdf", b"%PDF-1.4 secret-content", "application/pdf")
    return Product.objects.create(site=site, name="راهنمای دیجیتال", slug="guide", price=50000, kind=Product.Kind.DIGITAL, track_stock=False, digital_file=f, **kw)


class PriceAndDiscountTests(ShopTestCase):
    def test_discount_math_and_floor(self):
        p = Product(price=1000, discount_percent=25)
        self.assertEqual((p.discount_amount, p.final_price), (250, 750))
        p = Product(price=1000, discount_percent=90)
        self.assertEqual(p.final_price, 100)
        self.assertEqual(Product(price=1, discount_percent=90).final_price, 1)  # هرگز صفر یا منفی نمی‌شود
        self.assertFalse(Product(price=1000, discount_percent=0).has_discount)

    def test_cart_total_is_server_computed_ignoring_client_prices(self):
        self.product.discount_percent = 10
        self.product.save()
        c = Client()
        c.post("/fa/shop/cart/add/", {"product": self.product.pk, "qty": 2, "price": 1, "total": 1, "unit_price": 1})
        lines, total = services.cart_lines(self._req(c), self.site)
        self.assertEqual(total, 135000 * 2)
        resp = c.get("/fa/shop/cart/")
        self.assertContains(resp, "۲۷۰")

    def _req(self, client):
        from django.test import RequestFactory

        req = RequestFactory().get("/fa/")
        req.session = client.session
        return req

    def test_order_snapshot_survives_price_change(self):
        self.product.discount_percent = 20
        self.product.save()
        _, r = self.place_order(qty=2)
        order = Order.objects.get()
        self.assertEqual((order.subtotal, order.discount_total, order.total), (300000, 60000, 240000))
        item = order.items.get()
        self.assertEqual((item.list_price, item.unit_discount, item.unit_price), (150000, 30000, 120000))
        self.product.price = 999
        self.product.save()
        order.refresh_from_db()
        self.assertEqual(order.total, 240000)

    def test_price_changed_between_cart_and_order_uses_fresh_price(self):
        c = Client()
        self.add_to_cart(c)
        self.product.price = 200000
        self.product.save()
        c.post("/fa/shop/checkout/", self.checkout_data())
        self.assertEqual(Order.objects.get().total, 200000)

    def test_invalid_discount_rejected_in_panel(self):
        c = Client()
        c.force_login(self.admin)
        r = c.post(reverse("shop_panel:product_create"), {"name": "ت", "slug": "t1", "price": 10, "stock": 1, "track_stock": "on", "discount_percent": 95})
        self.assertEqual(r.status_code, 400)


class StockTests(ShopTestCase):
    def test_cannot_order_more_than_stock(self):
        c = Client()
        self.add_to_cart(c, qty=9)
        r = c.post("/fa/shop/checkout/", self.checkout_data())
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Order.objects.exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)

    def test_stock_reserved_and_restored_on_cancel(self):
        self.place_order(qty=3)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 2)
        services.change_status(Order.objects.get(), Order.Status.CANCELLED, actor=self.admin)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)

    def test_refund_restores_stock_only_before_shipping(self):
        self.place_order(qty=2)
        o = Order.objects.get()
        o.status = Order.Status.PAID
        o.save()
        services.change_status(o, Order.Status.REFUNDED, actor=self.admin)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)
        # ارسال‌شده ← استرداد موجودی را برنمی‌گرداند (کالا بیرون رفته)
        self.place_order(qty=1)
        o2 = Order.objects.exclude(pk=o.pk).get()
        o2.status = Order.Status.SHIPPED
        o2.save()
        before = Product.objects.get(pk=self.product.pk).stock
        services.change_status(o2, Order.Status.REFUNDED, actor=self.admin)
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, before)

    def test_inactive_product_cannot_be_ordered(self):
        c = Client()
        self.add_to_cart(c)
        self.product.is_active = False
        self.product.save()
        self.assertEqual(c.post("/fa/shop/checkout/", self.checkout_data()).status_code, 302)  # سبد خالی ← بازگشت به سبد
        self.assertFalse(Order.objects.exists())

    def test_untracked_stock_is_unlimited(self):
        p = Product.objects.create(site=self.site, name="خدمت", slug="svc", price=10, stock=0, track_stock=False)
        self.assertTrue(p.in_stock)
        c = Client()
        self.add_to_cart(c, p, qty=7)
        self.assertEqual(c.post("/fa/shop/checkout/", self.checkout_data()).status_code, 302)
        self.assertEqual(Order.objects.get().items.get().quantity, 7)
        self.assertEqual(Product.objects.get(pk=p.pk).stock, 0)

    def test_expire_restores_stock_and_logs(self):
        self.place_order(qty=2)
        Order.objects.update(created_at=timezone.now() - timedelta(hours=5))
        self.assertEqual(services.expire_pending(), 1)
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 5)
        self.assertTrue(OrderEvent.objects.filter(to_status="cancelled", message__contains="لغو خودکار").exists())


class OversellRaceTests(TransactionTestCase):
    """دو خریدار همزمان برای آخرین عدد؛ فقط یکی موفق می‌شود."""

    def test_only_one_wins_last_unit(self):
        if connection.vendor == "sqlite":
            self.skipTest("SQLite همزمانیِ واقعی ندارد؛ منطق با PostgreSQL تست شود. مسیر ترتیبی در تست دیگر پوشش دارد.")
        # TransactionTestCase پس از تست قبلی جدول‌ها را خالی می‌کند و سایت پیش‌فرضِ مهاجرت از بین رفته است
        site, _ = Site.objects.get_or_create(is_default=True, defaults={"name": "پیش‌فرض", "slug": "default"})
        p = Product.objects.create(site=site, name="آخرین", slug="last", price=10, stock=1)
        results = []

        def buy():
            try:
                lines = [{"product": p, "qty": 1}]
                services.create_order(site, lines, {"customer_name": "x y z", "phone": "09123456789", "email": "", "address": "a" * 12, "note": ""})
                results.append("ok")
            except services.StockError:
                results.append("stock")
            finally:
                connection.close()

        ts = [threading.Thread(target=buy) for _ in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sorted(results), ["ok", "stock"])


class SequentialOversellTests(ShopTestCase):
    def test_second_buyer_of_last_unit_fails(self):
        p = Product.objects.create(site=self.site, name="آخرین", slug="last", price=10, stock=1)
        a, b = Client(), Client()
        self.add_to_cart(a, p)
        self.add_to_cart(b, p)
        self.assertEqual(a.post("/fa/shop/checkout/", self.checkout_data()).status_code, 302)
        self.assertEqual(b.post("/fa/shop/checkout/", self.checkout_data()).status_code, 400)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Product.objects.get(pk=p.pk).stock, 0)


class OrderLifecycleTests(ShopTestCase):
    def test_unique_numbers_and_initial_event(self):
        for _ in range(3):
            self.place_order()
        nums = list(Order.objects.values_list("number", flat=True))
        self.assertEqual(len(set(nums)), 3)
        for o in Order.objects.all():
            self.assertEqual(o.status, Order.Status.PENDING)
            self.assertEqual(o.events.first().kind, OrderEvent.Kind.CREATED)

    def test_full_paid_flow_records_history(self):
        c, r = self.place_order()
        _, page = self.pay(c, r)
        o = Order.objects.get()
        self.assertEqual(o.status, Order.Status.PAID)
        kinds = list(o.events.values_list("kind", flat=True))
        self.assertEqual(kinds, ["created", "status"])
        self.assertEqual(o.payments.get().events.filter(kind="verify", to_status="succeeded").count(), 1)
        self.assertTrue(o.events.filter(from_status="pending_payment", to_status="paid").exists())

    def test_transitions(self):
        self.place_order()
        o = Order.objects.get()
        for bad in (Order.Status.PAID, Order.Status.REFUNDED, Order.Status.COMPLETED, "bogus"):
            with self.assertRaises(services.ShopError):
                services.change_status(o, bad)
        o.status = Order.Status.PAID
        o.save()
        with self.assertRaises(services.ShopError):  # سفارش فیزیکی بدون ارسال کامل نمی‌شود
            services.change_status(o, Order.Status.COMPLETED)
        services.change_status(o, Order.Status.PROCESSING, actor=self.admin, note="در حال بسته‌بندی")
        ev = o.events.filter(to_status="processing").get()
        self.assertEqual((ev.actor, ev.message), (self.admin, "در حال بسته‌بندی"))
        for s in (Order.Status.SHIPPED, Order.Status.COMPLETED, Order.Status.REFUNDED):
            services.change_status(o, s, actor=self.admin)
        with self.assertRaises(services.ShopError):
            services.change_status(o, Order.Status.CANCELLED)
        self.assertIn("برگشت وجه", o.events.filter(to_status="refunded").get().message)

    def test_cancelled_is_terminal(self):
        self.place_order()
        o = Order.objects.get()
        services.change_status(o, Order.Status.CANCELLED)
        for s in Order.Status.values:
            with self.assertRaises(services.ShopError):
                services.change_status(o, s)

    def test_amount_mismatch_never_marks_paid(self):
        c, r = self.place_order()
        o = Order.objects.get()
        with mock.patch.object(gateways.SandboxGateway, "verify_transaction", return_value=gateways.VerifyResult.paid(1)):
            ref = Payment.objects.get().reference
            services.finalize_payment(ref)
        o.refresh_from_db()
        self.assertEqual(o.status, Order.Status.PENDING)
        self.assertEqual(Payment.objects.get().status, Payment.Status.REVIEW)

    def test_failed_payment_leaves_pending_and_retry_possible(self):
        c, r = self.place_order()
        self.pay(c, r, action="fail")
        o = Order.objects.get()
        self.assertEqual(o.status, Order.Status.PENDING)
        resp = c.post(reverse("shop:order_pay", args=[o.number, o.token]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(o.payments.count(), 2)


class DigitalProductTests(ShopTestCase):
    def setUp(self):
        super().setUp()
        self.dp = digital(self.site)
        import shutil
        import tempfile

        self._tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp, True)

    def _buy(self):
        c = Client()
        self.add_to_cart(c, self.dp, qty=5)
        r = c.post("/fa/shop/checkout/", self.checkout_data(address=""))
        return c, r

    def test_digital_only_needs_no_address_and_qty_forced_to_one(self):
        c, r = self._buy()
        o = Order.objects.get()
        self.assertFalse(o.needs_shipping)
        self.assertEqual(o.items.get().quantity, 1)
        self.assertEqual(o.total, 50000)

    def test_mixed_cart_requires_address(self):
        c = Client()
        self.add_to_cart(c, self.dp)
        self.add_to_cart(c)
        self.assertEqual(c.post("/fa/shop/checkout/", self.checkout_data(address="")).status_code, 400)
        self.assertEqual(c.post("/fa/shop/checkout/", self.checkout_data()).status_code, 302)
        self.assertTrue(Order.objects.get().needs_shipping)

    def test_download_only_after_payment(self):
        c, r = self._buy()
        o = Order.objects.get()
        item = o.items.get()
        url = reverse("shop:download", args=[o.number, o.token, item.pk])
        self.assertEqual(Client().get(url).status_code, 404)  # پرداخت‌نشده
        self.pay(c, r)
        resp = Client().get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b"".join(resp.streaming_content), b"%PDF-1.4 secret-content")
        self.assertIn("attachment", resp["Content-Disposition"])
        self.assertEqual(resp["Cache-Control"], "private, no-store")

    def test_download_needs_valid_token_and_limit(self):
        c, r = self._buy()
        self.pay(c, r)
        o = Order.objects.get()
        item = o.items.get()
        self.assertEqual(Client().get(reverse("shop:download", args=[o.number, "0" * 40, item.pk])).status_code, 404)
        with override_settings(SHOP_DIGITAL_DOWNLOAD_LIMIT=2):
            url = reverse("shop:download", args=[o.number, o.token, item.pk])
            self.assertEqual(Client().get(url).status_code, 200)
            self.assertEqual(Client().get(url).status_code, 200)
            self.assertEqual(Client().get(url).status_code, 404)
        self.assertEqual(OrderEvent.objects.filter(kind="download").count(), 2)

    def test_download_blocked_after_refund(self):
        c, r = self._buy()
        self.pay(c, r)
        o = Order.objects.get()
        services.change_status(o, Order.Status.REFUNDED, actor=self.admin)
        self.assertEqual(Client().get(reverse("shop:download", args=[o.number, o.token, o.items.get().pk])).status_code, 404)

    def test_file_never_exposed_via_public_url_or_api(self):
        self.assertEqual(Client().get("/fa/shop/p/guide/").status_code, 200)
        body = Client().get("/fa/shop/p/guide/").content.decode()
        self.assertNotIn("guide", re.sub(r"/[a-z-]+/shop/p/guide/", "", body).replace("راهنمای", ""))  # the URL itself (and its hreflang copies) is fine
        self.assertNotIn("secret", body)
        api = Client().get("/api/v1/shop/products/guide/").content.decode()
        self.assertNotIn("digital_file", api)
        with self.assertRaises(ValueError):
            self.dp.digital_file.url

    def test_digital_file_validation_in_panel(self):
        c = Client()
        c.force_login(self.admin)
        base = {"name": "د", "slug": "dd", "kind": "digital", "price": 10, "is_active": "on"}
        bad = SimpleUploadedFile("evil.exe", b"MZ", "application/octet-stream")
        self.assertEqual(c.post(reverse("shop_panel:product_create"), {**base, "digital_file": bad}).status_code, 400)
        self.assertEqual(c.post(reverse("shop_panel:product_create"), base).status_code, 400)  # فعال بدون فایل
        ok = SimpleUploadedFile("a.pdf", b"%PDF", "application/pdf")
        self.assertEqual(c.post(reverse("shop_panel:product_create"), {**base, "digital_file": ok}).status_code, 302)
        self.assertFalse(Product.objects.get(slug="dd").track_stock)


class GuestAndUserTests(ShopTestCase):
    def test_guest_order_has_no_user(self):
        self.place_order()
        self.assertIsNone(Order.objects.get().user)

    def test_logged_in_order_linked_and_listed(self):
        u = make_user("buyer", Role.USER)
        c = Client()
        c.force_login(u)
        self.place_order(client=c)
        o = Order.objects.get()
        self.assertEqual(o.user, u)
        self.assertContains(c.get("/fa/shop/my-orders/"), o.number)
        other = Client()
        other.force_login(make_user("other", Role.USER))
        self.assertNotContains(other.get("/fa/shop/my-orders/"), o.number)

    def test_my_orders_requires_login(self):
        r = Client().get("/fa/shop/my-orders/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("login", r["Location"])

    @override_settings(SHOP_ALLOW_GUEST_CHECKOUT=False)
    def test_guest_checkout_can_be_disabled(self):
        c = Client()
        self.add_to_cart(c)
        self.assertEqual(c.post("/fa/shop/checkout/", self.checkout_data()).status_code, 400)
        self.assertFalse(Order.objects.exists())
        with self.assertRaises(services.ShopError):
            services.create_order(self.site, [{"product": self.product, "qty": 1}], self.checkout_data(), None)
        u = make_user("m", Role.USER)
        c.force_login(u)
        self.assertEqual(c.post("/fa/shop/checkout/", self.checkout_data()).status_code, 302)


class SearchAndCategoryTests(ShopTestCase):
    def setUp(self):
        super().setUp()
        self.cat = ProductCategory.objects.create(site=self.site, name="کتاب‌ها", slug="books")
        self.product.category = self.cat
        self.product.save()
        Product.objects.create(site=self.site, name="مداد", slug="pencil", price=10, stock=9)
        digital(self.site)

    def names(self, qs):
        return list(qs.values_list("slug", flat=True))

    def test_search_and_filters(self):
        base = Product.objects.filter(site=self.site)
        self.assertEqual(self.names(services.search_products(base, q="مداد")), ["pencil"])
        self.assertEqual(self.names(services.search_products(base, category="books")), ["book"])
        self.assertEqual(self.names(services.search_products(base, q="کتاب")), ["book"])  # نام دسته هم جست‌وجو می‌شود
        self.assertEqual(self.names(services.search_products(base, kind="digital")), ["guide"])
        self.assertEqual(self.names(services.search_products(base, sort="price_asc"))[0], "pencil")
        self.assertEqual(self.names(services.search_products(base, sort="price_desc"))[0], "book")
        self.assertEqual(len(services.search_products(base, sort="'; drop table", kind="evil")), 3)

    def test_public_search_page_and_noindex(self):
        r = Client().get("/fa/shop/?q=مداد")
        self.assertContains(r, "مداد")
        self.assertNotContains(r, "کتاب آزمایشی")
        self.assertIn("noindex", r.content.decode())
        self.assertNotIn("noindex", Client().get("/fa/shop/").content.decode().split("<title>")[0] + "")

    def test_search_query_is_safe(self):
        for q in ("%", "_", "<script>", "a" * 500, "' OR 1=1 --"):
            self.assertEqual(Client().get("/fa/shop/", {"q": q}).status_code, 200)
        self.assertNotContains(Client().get("/fa/shop/", {"q": "<script>alert(1)</script>"}), "<script>alert(1)</script>")

    def test_category_isolated_per_site_in_panel_form(self):
        other = Site.objects.create(name="B", slug="b")
        foreign = ProductCategory.objects.create(site=other, name="غریبه", slug="x")
        c = Client()
        c.force_login(self.admin)
        r = c.post(reverse("shop_panel:product_create"), {"name": "ن", "slug": "n1", "price": 10, "stock": 1, "track_stock": "on", "category": foreign.pk})
        self.assertEqual(r.status_code, 400)

    def test_category_crud_and_delete_keeps_products(self):
        c = Client()
        c.force_login(self.admin)
        self.assertEqual(c.post(reverse("shop_panel:category_create"), {"name": "لوازم", "slug": "", "order": 1}).status_code, 302)
        self.assertEqual(c.post(reverse("shop_panel:category_create"), {"name": "تکراری", "slug": "books", "order": 1}).status_code, 400)
        c.post(reverse("shop_panel:category_delete", args=[self.cat.pk]))
        self.product.refresh_from_db()
        self.assertIsNone(self.product.category)


class GalleryTests(ShopTestCase):
    def test_gallery_add_delete(self):
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (50, 50), (1, 2, 3)).save(buf, "PNG")
        c = Client()
        c.force_login(self.admin)
        r = c.post(reverse("shop_panel:gallery_add", args=[self.product.pk]), {"image": SimpleUploadedFile("g.png", buf.getvalue(), "image/png"), "alt": "نما", "order": 0})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.product.gallery.count(), 1)
        bad = SimpleUploadedFile("g.png", b"not-an-image", "image/png")
        c.post(reverse("shop_panel:gallery_add", args=[self.product.pk]), {"image": bad, "alt": "", "order": 0})
        self.assertEqual(self.product.gallery.count(), 1)
        self.assertEqual(Client().get("/api/v1/shop/products/book/").json()["images"][0]["alt"], "نما")
        c.post(reverse("shop_panel:gallery_delete", args=[self.product.pk, self.product.gallery.get().pk]))
        self.assertEqual(self.product.gallery.count(), 0)

    def test_gallery_other_site_product_is_404(self):
        other = Site.objects.create(name="B", slug="b")
        p = Product.objects.create(site=other, name="دیگر", slug="o", price=1, stock=1)
        c = Client()
        c.force_login(self.admin)
        self.assertEqual(c.post(reverse("shop_panel:gallery_add", args=[p.pk]), {}).status_code, 404)


class PermissionTests(ShopTestCase):
    def test_panel_requires_capability(self):
        self.place_order()
        o = Order.objects.get()
        urls = [reverse("shop_panel:orders"), reverse("shop_panel:order", args=[o.pk]), reverse("shop_panel:products"), reverse("shop_panel:categories"),
                reverse("shop_panel:category_create"), reverse("shop_panel:product_create")]
        anon = Client()
        for u in urls:
            self.assertEqual(anon.get(u).status_code, 302, u)
        for user in (self.author, make_user("plain", Role.USER)):
            c = Client()
            c.force_login(user)
            for u in urls:
                self.assertEqual(c.get(u).status_code, 403, u)
            self.assertEqual(c.post(reverse("shop_panel:order", args=[o.pk]), {"status": "cancelled"}).status_code, 403)
        self.assertEqual(Order.objects.get().status, Order.Status.PENDING)

    def test_state_changing_panel_endpoints_reject_get(self):
        c = Client()
        c.force_login(self.admin)
        self.assertEqual(c.get(reverse("shop_panel:product_delete", args=[self.product.pk])).status_code, 405)
        self.assertEqual(c.get(reverse("shop_panel:category_delete", args=[1])).status_code, 405)

    def test_csrf_enforced_on_status_change(self):
        self.place_order()
        o = Order.objects.get()
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.admin)
        self.assertEqual(c.post(reverse("shop_panel:order", args=[o.pk]), {"status": "cancelled"}).status_code, 403)

    def test_admin_status_change_with_note_and_history_page(self):
        self.place_order()
        o = Order.objects.get()
        c = Client()
        c.force_login(self.admin)
        c.post(reverse("shop_panel:order", args=[o.pk]), {"status": "cancelled", "note": "درخواست مشتری"})
        c.post(reverse("shop_panel:order", args=[o.pk]), {"action": "note", "note": "تماس گرفته شد"})
        page = c.get(reverse("shop_panel:order", args=[o.pk])).content.decode()
        self.assertIn("درخواست مشتری", page)
        self.assertIn("تماس گرفته شد", page)
        self.assertIn(self.admin.display_name, page)

    def test_order_list_filters_guest_vs_user(self):
        u = make_user("buyer", Role.USER)
        uc = Client()
        uc.force_login(u)
        self.place_order(client=uc)
        self.place_order()
        c = Client()
        c.force_login(self.admin)
        self.assertEqual(c.get(reverse("shop_panel:orders"), {"who": "guest"}).context["page_obj"].paginator.count, 1)
        self.assertEqual(c.get(reverse("shop_panel:orders"), {"who": "user"}).context["page_obj"].paginator.count, 1)
        self.assertEqual(c.get(reverse("shop_panel:orders")).context["page_obj"].paginator.count, 2)

    def test_customer_order_page_needs_token_and_hides_internal_notes(self):
        self.place_order()
        o = Order.objects.get()
        services.record_event(o, OrderEvent.Kind.NOTE, message="یادداشت محرمانه", actor=self.admin)
        page = Client().get(o.get_public_url())
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "یادداشت محرمانه")
        self.assertEqual(Client().get(f"/fa/shop/order/{o.number}/{'0' * 40}/").status_code, 404)


class ApiTests(ShopTestCase):
    def test_public_catalog_api_fields(self):
        self.product.discount_percent = 10
        self.product.save()
        data = Client().get("/api/v1/shop/products/").json()
        item = data["results"][0]
        self.assertEqual((item["price"], item["final_price"], item["discount_amount"]), (150000, 135000, 15000))
        for hidden in ("digital_file", "site", "cost"):
            self.assertNotIn(hidden, item)
        self.assertEqual(Client().get("/api/v1/shop/products/?q=nothing-here").json()["count"], 0)

    def test_orders_api_permissions(self):
        self.place_order()
        o = Order.objects.get()
        self.assertIn(Client().get("/api/v1/shop/orders/").status_code, (401, 403))
        c = Client()
        c.force_login(self.author)
        self.assertEqual(c.get("/api/v1/shop/orders/").status_code, 403)
        c.force_login(self.admin)
        self.assertEqual(c.get("/api/v1/shop/orders/").json()["count"], 1)
        detail = c.get(f"/api/v1/shop/orders/{o.number}/").json()
        self.assertEqual(detail["events"][0]["kind"], "created")
        self.assertNotIn("token", detail)


class GatewayExtensibilityTests(ShopTestCase):
    def tearDown(self):
        gateways.GATEWAYS.pop("fakepay", None)

    def test_register_custom_gateway_full_flow(self):
        class FakePay(gateways.PaymentGateway):
            def create_transaction(self, ctx):
                return gateways.CreateResult(redirect=gateways.Redirect(url=f"/fa/shop/pay/callback/{ctx.reference}/"), gateway_ref="FP1")

            def verify_transaction(self, ctx, params):
                return gateways.VerifyResult.paid(ctx.amount, gateway_ref="FP1")

        gateways.register_gateway("fakepay", FakePay)
        from apps.shop.models import GatewayConfig

        GatewayConfig.objects.create(site=self.site, gateway="fakepay", enabled=True)
        with override_settings(SHOP_PAYMENT_GATEWAY="fakepay"):
            c = Client()
            self.add_to_cart(c)
            r = c.post("/fa/shop/checkout/", self.checkout_data())
            self.assertEqual(r.status_code, 302)
            c.get(r["Location"])
        o = Order.objects.get()
        self.assertEqual((o.status, o.payments.get().gateway, o.payments.get().gateway_ref), (Order.Status.PAID, "fakepay", "FP1"))

    def test_register_rules(self):
        class Dummy(gateways.BaseGateway):
            pass

        for bad in ("X", "ab", "has space", ""):
            with self.assertRaises(ValueError):
                gateways.register_gateway(bad, Dummy)
        with self.assertRaises(ValueError):
            gateways.register_gateway("okname", object)
        with self.assertRaises(ValueError):
            gateways.register_gateway("sandbox", Dummy)  # بازنویسی درگاه موجود ممنوع

    def test_plugin_hook_can_add_but_not_override(self):
        class Extra(gateways.BaseGateway):
            name = "plug_gw"

        def fake_filter(name, value, **ctx):
            return {**value, "plug_gw": Extra, "sandbox": Extra, "Bad Name": Extra, "notcls": 5}

        with mock.patch("apps.plugins.hooks.apply_filter", fake_filter):
            av = gateways.available_gateways()
        self.assertIn("plug_gw", av)
        self.assertIs(av["sandbox"], gateways.SandboxGateway)
        self.assertNotIn("Bad Name", av)
        self.assertNotIn("notcls", av)

    def test_unknown_gateway_blocks_checkout_without_order(self):
        with override_settings(SHOP_PAYMENT_GATEWAY="nonexistent"):
            c = Client()
            self.add_to_cart(c)
            self.assertEqual(c.post("/fa/shop/checkout/", self.checkout_data()).status_code, 400)
        self.assertFalse(Order.objects.exists())

    def test_hook_registered(self):
        from apps.plugins.hooks_registry import HOOKS

        self.assertEqual(HOOKS["shop.gateways"], "filter")


class ListQueryTests(ShopTestCase):
    def test_list_queries_constant_with_category_and_discount(self):
        cat = ProductCategory.objects.create(site=self.site, name="ک", slug="k")
        for i in range(10):
            Product.objects.create(site=self.site, name=f"م{i}", slug=f"m{i}", price=10, stock=1, category=cat, discount_percent=10)
        c = Client()
        c.get("/fa/shop/")
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as a:
            c.get("/fa/shop/")
        for i in range(10, 20):
            Product.objects.create(site=self.site, name=f"م{i}", slug=f"m{i}", price=10, stock=1, category=cat, discount_percent=10)
        with CaptureQueriesContext(connection) as b:
            c.get("/fa/shop/")
        self.assertEqual(len(a), len(b))
