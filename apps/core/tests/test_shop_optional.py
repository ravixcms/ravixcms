"""فروشگاه اختیاری است: با RAVIX_SHOP_ENABLED=false هسته (سایت، پنل، API، نقشهٔ سایت) بدون فروشگاه کار می‌کند.
اجرا: RAVIX_SHOP_ENABLED=false python manage.py test apps.core.tests.test_shop_optional"""
import unittest

from django.apps import apps
from django.conf import settings
from django.test import Client
from django.urls import NoReverseMatch, reverse

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from django.test import TestCase


@unittest.skipIf(settings.SHOP_ENABLED, "فقط در حالت فروشگاه خاموش")
class ShopDisabledTests(TestCase):
    def test_app_not_installed_and_routes_absent(self):
        self.assertFalse(apps.is_installed("apps.shop"))
        for name in ("shop:list", "shop_panel:orders", "shop_panel:products"):
            with self.assertRaises(NoReverseMatch):
                reverse(name)
        self.assertEqual(Client().get("/fa/shop/").status_code, 404)
        self.assertEqual(Client().get("/api/v1/shop/products/").status_code, 404)

    def test_core_still_works(self):
        c = Client()
        self.assertEqual(c.get("/healthz").status_code, 200)
        self.assertEqual(c.get("/fa/sitemap.xml").status_code, 200)
        self.assertNotIn("/fa/shop/", c.get("/fa/sitemap.xml").content.decode())
        admin = make_user("boss", Role.ADMIN)
        c.force_login(admin)
        self.assertEqual(c.get(reverse("dashboard:home")).status_code, 200)
        self.assertEqual(c.get("/api/v1/me/").status_code, 200)

    def test_no_shop_tables_or_nav(self):
        from django.db import connection

        self.assertNotIn("shop_product", connection.introspection.table_names())
        c = Client()
        c.force_login(make_user("boss2", Role.ADMIN))
        self.assertNotIn("shop_panel", c.get(reverse("dashboard:home")).content.decode())


@unittest.skipUnless(settings.SHOP_ENABLED, "فقط در حالت فروشگاه روشن")
class ShopEnabledTests(TestCase):
    def test_routes_present(self):
        self.assertTrue(apps.is_installed("apps.shop"))
        self.assertEqual(reverse("shop:list"), "/fa/shop/")
