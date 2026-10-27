"""Built-in modules work with the shop installed or not (RAVIX_SHOP_ENABLED=false is the server lock)."""
from django.conf import settings
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.core import modules


class BuiltinModulePageTests(TestCase):
    def setUp(self):
        self.root = make_user("root", Role.SUPER_ADMIN)
        self.c = Client()
        self.c.force_login(self.root)

    def test_plugins_page_lists_the_shop_card(self):
        page = self.c.get(reverse("plugins:list"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Ravix Shop")

    def test_server_lock_follows_the_install_state(self):
        self.assertEqual(modules.server_locked("shop"), not settings.SHOP_ENABLED)
        if not settings.SHOP_ENABLED:
            self.assertFalse(modules.is_enabled("shop"))
            self.assertContains(self.c.get(reverse("plugins:list")), "RAVIX_SHOP_ENABLED=false")
            self.c.post(reverse("plugins:module_toggle", args=["shop"]), {"enable": "1"})
            self.assertFalse(modules.is_enabled("shop"))

    def test_no_shop_routes_without_the_module(self):
        if settings.SHOP_ENABLED:
            self.skipTest("shop installed")
        self.assertEqual(Client().get("/fa/shop/").status_code, 404)
        self.assertEqual(Client().get("/api/v1/shop/products/").status_code, 404)
        self.assertEqual(self.c.get("/panel/shop/products/").status_code, 404)
