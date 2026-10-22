import unittest

from django.conf import settings

if not settings.SHOP_ENABLED:  # ماژول فروشگاه خاموش است (RAVIN_SHOP_ENABLED=false)؛ این تست‌ها موضوعیت ندارند
    raise unittest.SkipTest("فروشگاه غیرفعال است")

from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.shop.models import Order, Product
from apps.shop.tests.helpers import ShopTestCase
from apps.sites.access import accessible_sites, can_access_site, create_site
from apps.sites.models import Site, SiteDomain, SiteMember


@override_settings(RAVIN_MULTISITE_ENABLED=True, ALLOWED_HOSTS=["a.test", "b.test", "c.test", "testserver"])
class IsolationTests(ShopTestCase):
    def setUp(self):
        super().setUp()
        self.a_owner = make_user("aowner", Role.ADMIN)
        self.b_owner = make_user("bowner", Role.ADMIN)
        self.site_a = create_site("A", "a", self.a_owner)
        self.site_b = create_site("B", "b", self.b_owner)
        SiteDomain.objects.create(site=self.site_a, domain="a.test")
        SiteDomain.objects.create(site=self.site_b, domain="b.test")
        self.pa = Product.objects.create(site=self.site_a, name="محصول الف", slug="pa", price=100, stock=3)
        self.pb = Product.objects.create(site=self.site_b, name="محصول ب", slug="pb", price=200, stock=3)

    def get(self, host, path, client=None):
        return (client or Client()).get(path, HTTP_HOST=host)

    def order_on(self, host, product, client=None):
        c = client or Client()
        c.post("/shop/cart/add/", {"product": product.pk, "qty": 1}, HTTP_HOST=host)
        r = c.post("/shop/checkout/", self.checkout_data(), HTTP_HOST=host)
        return c, r

    # --- عمومی
    def test_public_catalog_is_per_domain(self):
        ra, rb = self.get("a.test", "/shop/"), self.get("b.test", "/shop/")
        self.assertContains(ra, "محصول الف")
        self.assertNotContains(ra, "محصول ب")
        self.assertContains(rb, "محصول ب")
        self.assertNotContains(rb, "محصول الف")
        self.assertEqual(self.get("b.test", "/shop/p/pa/").status_code, 404)
        self.assertEqual(self.get("a.test", "/shop/p/pa/").status_code, 200)

    def test_unknown_or_inactive_domain_has_no_shop(self):
        self.assertEqual(self.get("c.test", "/shop/").status_code, 404)
        Site.objects.filter(pk=self.site_a.pk).update(is_active=False)
        self.assertEqual(self.get("a.test", "/shop/").status_code, 404)

    def test_cart_cannot_add_other_sites_product(self):
        c = Client()
        r = c.post("/shop/cart/add/", {"product": self.pa.pk, "qty": 1}, HTTP_HOST="b.test")
        self.assertEqual(r.status_code, 404)

    def test_cart_is_per_site(self):
        c = Client()
        c.post("/shop/cart/add/", {"product": self.pa.pk, "qty": 1}, HTTP_HOST="a.test")
        self.assertContains(c.get("/shop/cart/", HTTP_HOST="a.test"), "محصول الف")
        self.assertNotContains(c.get("/shop/cart/", HTTP_HOST="b.test"), "محصول الف")

    def test_order_and_payment_not_reachable_from_other_domain(self):
        c, r = self.order_on("a.test", self.pa)
        order = Order.objects.get()
        self.assertEqual(order.site, self.site_a)
        self.assertEqual(self.get("a.test", order.get_public_url()).status_code, 200)
        self.assertEqual(self.get("b.test", order.get_public_url()).status_code, 404)  # توکن درست ولی سایت دیگر
        ref = order.payments.get().reference
        self.assertEqual(self.get("b.test", f"/shop/pay/callback/{ref}/").status_code, 404)
        self.assertEqual(self.get("b.test", f"/shop/pay/sandbox/{ref}/").status_code, 404)

    def test_sitemap_and_api_per_domain(self):
        sa = self.get("a.test", "/sitemap.xml").content.decode()
        self.assertIn("/shop/p/pa/", sa)
        self.assertNotIn("/shop/p/pb/", sa)
        self.assertEqual([p["slug"] for p in self.get("b.test", "/api/v1/shop/products/").json()["results"]], ["pb"])

    # --- پنل
    def login(self, user):
        c = Client()
        c.force_login(user)
        return c

    def test_panel_lists_only_active_site_data(self):
        self.order_on("a.test", self.pa)
        self.order_on("b.test", self.pb)
        ca, cb = self.login(self.a_owner), self.login(self.b_owner)
        self.assertEqual(ca.get(reverse("shop_panel:orders")).context["page_obj"].paginator.count, 1)
        self.assertContains(ca.get(reverse("shop_panel:products")), "محصول الف")
        self.assertNotContains(ca.get(reverse("shop_panel:products")), "محصول ب")
        self.assertEqual(cb.get("/api/v1/shop/orders/").json()["count"], 1)

    def test_cross_site_object_access_denied_by_pk(self):
        self.order_on("b.test", self.pb)
        ob = Order.objects.get(site=self.site_b)
        ca = self.login(self.a_owner)
        self.assertEqual(ca.get(reverse("shop_panel:order", args=[ob.pk])).status_code, 404)
        self.assertEqual(ca.post(reverse("shop_panel:order", args=[ob.pk]), {"status": "cancelled"}).status_code, 404)
        self.assertEqual(ca.get(reverse("shop_panel:product_edit", args=[self.pb.pk])).status_code, 404)
        self.assertEqual(ca.post(reverse("shop_panel:product_delete", args=[self.pb.pk])).status_code, 404)
        self.assertTrue(Product.objects.filter(pk=self.pb.pk).exists())
        self.assertEqual(Order.objects.get(pk=ob.pk).status, "pending_payment")

    def test_cannot_switch_to_foreign_site(self):
        ca = self.login(self.a_owner)
        self.assertEqual(ca.post(reverse("sites:switch"), {"site": self.site_b.pk}).status_code, 403)
        self.assertNotIn("ravin_active_site", ca.session)
        self.assertContains(ca.get(reverse("shop_panel:products")), "محصول الف")

    def test_session_value_is_revalidated(self):
        ca = self.login(self.a_owner)
        s = ca.session
        s["ravin_active_site"] = self.site_b.pk  # دست‌کاری نشست
        s.save()
        self.assertNotContains(ca.get(reverse("shop_panel:products")), "محصول ب")

    def test_membership_grants_access_and_switching(self):
        staff = make_user("staff1", Role.ADMIN)
        c = self.login(staff)
        self.assertEqual(c.get(reverse("shop_panel:products")).status_code, 403)  # عضو هیچ سایتی نیست
        SiteMember.objects.create(site=self.site_a, user=staff, role="staff")
        SiteMember.objects.create(site=self.site_b, user=staff, role="staff")
        self.assertEqual(c.get(reverse("shop_panel:products")).status_code, 200)
        c.post(reverse("sites:switch"), {"site": self.site_b.pk})
        self.assertContains(c.get(reverse("shop_panel:products")), "محصول ب")

    def test_capability_still_required_even_if_member(self):
        SiteMember.objects.create(site=self.site_a, user=self.author, role="manager")
        c = self.login(self.author)
        self.assertEqual(c.get(reverse("shop_panel:orders")).status_code, 403)

    def test_superadmin_sees_all_sites(self):
        self.assertEqual(accessible_sites(self.superadmin).count(), Site.objects.filter(is_active=True).count())
        c = self.login(self.superadmin)
        self.assertEqual(c.post(reverse("sites:switch"), {"site": self.site_b.pk}).status_code, 302)

    def test_sites_admin_only_for_superadmin(self):
        for user, code in ((self.a_owner, 403), (self.author, 403), (self.superadmin, 200)):
            self.assertEqual(self.login(user).get(reverse("sites:list")).status_code, code)
        # مالک سایت هم نمی‌تواند عضو/دامنهٔ سایت را عوض کند
        self.assertEqual(self.login(self.a_owner).post(reverse("sites:detail", args=[self.site_a.pk]), {"action": "add_domain", "domain": "evil.test"}).status_code, 403)

    def test_domain_management(self):
        c = self.login(self.superadmin)
        url = reverse("sites:detail", args=[self.site_a.pk])
        c.post(url, {"action": "add_domain", "domain": "NEW.Example.com"})
        self.assertTrue(SiteDomain.objects.filter(domain="new.example.com", site=self.site_a).exists())
        c.post(url, {"action": "add_domain", "domain": "b.test"})  # تکراری
        self.assertEqual(SiteDomain.objects.filter(domain="b.test").count(), 1)
        c.post(url, {"action": "add_domain", "domain": "bad domain!"})
        self.assertFalse(SiteDomain.objects.filter(domain__contains="bad").exists())

    def test_owner_cannot_be_removed(self):
        c = self.login(self.superadmin)
        m = SiteMember.objects.get(site=self.site_a, user=self.a_owner)
        c.post(reverse("sites:detail", args=[self.site_a.pk]), {"action": "remove_member", "id": m.pk})
        self.assertTrue(SiteMember.objects.filter(pk=m.pk).exists())

    def test_slug_unique_per_site_not_global(self):
        Product.objects.create(site=self.site_b, name="هم‌نام", slug="pa", price=1, stock=1)  # slug تکراری در سایت دیگر مجاز
        self.assertContains(self.get("b.test", "/shop/p/pa/"), "هم‌نام")
        self.assertContains(self.get("a.test", "/shop/p/pa/"), "محصول الف")


class SingleSiteModeTests(ShopTestCase):
    def test_host_ignored_when_multisite_disabled(self):
        extra = Site.objects.create(name="X", slug="x")
        SiteDomain.objects.create(site=extra, domain="x.test")
        with override_settings(ALLOWED_HOSTS=["x.test", "testserver"]):
            self.assertContains(Client().get("/shop/", HTTP_HOST="x.test"), "کتاب آزمایشی")

    def test_default_site_exists_and_unique(self):
        self.assertEqual(Site.objects.filter(is_default=True).count(), 1)
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            Site.objects.create(name="dup", slug="dup", is_default=True)

    def test_non_super_admin_only_default_site(self):
        other = create_site("O", "o", self.admin)
        self.assertFalse(can_access_site(self.admin, other))
        self.assertTrue(can_access_site(self.admin, self.site))
        self.assertFalse(can_access_site(self.author, None))
