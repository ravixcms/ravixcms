"""Ravix Shop as a switchable built-in module: activate/deactivate from Plugins, hidden routes/menus/API/hooks, kept data, server lock."""
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.core import modules
from apps.core.models import ModuleState
from apps.core.navigation import get_nav_items
from apps.shop.models import GatewayConfig, Order, Product, ShopSettings

from .helpers import ShopTestCase

SHOP_URLS = ["/fa/shop/", "/fa/shop/cart/", "/api/v1/shop/products/", "/api/v1/shop/categories/"]
PANEL_URLS = ["shop_panel:products", "shop_panel:orders", "shop_panel:categories", "shop_panel:gateways", "shop_panel:settings"]


class ModuleSwitchTests(ShopTestCase):
    def login(self, user):
        c = Client()
        c.force_login(user)
        return c

    def switch(self, enable, user=None):
        return self.login(user or self.superadmin).post(reverse("plugins:module_toggle", args=["shop"]), {"enable": "1" if enable else "0"})

    def shop_nav(self):
        return [i.key for i in get_nav_items() if i.key.startswith("shop.")]

    # --- the default and the first activation
    @override_settings(BUILTIN_MODULES_DEFAULT_ON=False)
    def test_new_install_starts_off_and_hides_everything(self):
        self.assertFalse(modules.is_enabled("shop"))
        for url in SHOP_URLS:
            self.assertEqual(Client().get(url).status_code, 404, url)
        admin = self.login(self.superadmin)
        for name in PANEL_URLS:
            self.assertEqual(admin.get(reverse(name)).status_code, 404, name)
        self.assertEqual(self.shop_nav(), [])

    @override_settings(BUILTIN_MODULES_DEFAULT_ON=False)
    def test_first_activation_opens_the_setup_guide_then_everything_works(self):
        r = self.switch(True)
        self.assertRedirects(r, reverse("plugins:module_setup", args=["shop"]), fetch_redirect_response=False)
        self.assertTrue(modules.is_enabled("shop"))
        self.assertEqual(Client().get("/fa/shop/").status_code, 200)
        self.assertEqual(Client().get("/api/v1/shop/products/").status_code, 200)
        self.assertEqual(self.login(self.superadmin).get(reverse("shop_panel:products")).status_code, 200)
        self.assertEqual(len(self.shop_nav()), 5)

    def test_second_activation_goes_back_to_the_plugins_page(self):
        modules.set_setup("shop", ModuleState.SETUP_DONE)
        self.switch(False)
        self.assertRedirects(self.switch(True), reverse("plugins:list"), fetch_redirect_response=False)

    # --- deactivation hides, never deletes
    def test_deactivation_hides_routes_menus_and_api_but_keeps_data(self):
        self.place_order()
        before = (Product.objects.count(), Order.objects.count(), ShopSettings.objects.count())
        GatewayConfig.objects.create(site=self.site, gateway="sandbox", enabled=True)
        self.switch(False)
        self.assertFalse(modules.is_enabled("shop"))
        for url in SHOP_URLS + ["/fa/shop/p/book/"]:
            self.assertEqual(Client().get(url).status_code, 404, url)
        admin = self.login(self.superadmin)
        for name in PANEL_URLS + ["shop_panel:orders"]:
            self.assertEqual(admin.get(reverse(name)).status_code, 404, name)
        self.assertEqual(self.shop_nav(), [])
        self.assertEqual((Product.objects.count(), Order.objects.count(), ShopSettings.objects.count()), before)
        self.assertEqual(GatewayConfig.objects.count(), 1)
        # everything comes back
        self.switch(True)
        self.assertEqual(Client().get("/fa/shop/p/book/").status_code, 200)
        self.assertEqual(admin.get(reverse("shop_panel:orders")).status_code, 200)
        self.assertEqual(len(self.shop_nav()), 5)
        self.assertEqual((Product.objects.count(), Order.objects.count()), before[:2])

    def test_hidden_from_sitemap_and_health_while_off(self):
        self.switch(False)
        self.assertNotIn("/shop/", Client().get("/sitemap.xml").content.decode() + Client().get("/fa/sitemap.xml").content.decode())
        from apps.core.sitehealth import check_timers

        self.assertNotIn("ravix-expire-orders.timer", " ".join(str(c) for c in check_timers()))

    def test_api_stays_hidden_for_signed_in_staff_too(self):
        self.switch(False)
        self.assertEqual(self.login(self.superadmin).get("/api/v1/shop/products/").status_code, 404)

    # --- hooks, notifications, timers
    def test_hooks_and_customer_emails_stop_while_off(self):
        self.assertTrue(modules.hook_allowed("shop.order_status_changed"))
        self.assertTrue(modules.hook_allowed("comments.created"))  # not the shop's
        self.switch(False)
        self.assertFalse(modules.hook_allowed("shop.order_status_changed"))
        self.assertFalse(modules.hook_allowed("shop.gateways"))
        self.assertTrue(modules.hook_allowed("comments.created"))
        from apps.shop import services

        order = Order.objects.create(site=self.site, number="RV-OFF1", token="o" * 40, customer_name="x", phone="09123456789", email="a@b.com", total=1, subtotal=1)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True), mock.patch("apps.plugins.hooks.run_action") as run:
            services.record_event(order, "status", from_status="pending_payment", to_status="cancelled")
        self.assertEqual(len(mail.outbox), 0)
        run.assert_not_called()

    def test_expire_command_does_nothing_while_off(self):
        self.switch(False)
        with mock.patch("apps.shop.services.expire_pending") as expire:
            call_command("shop_expire_orders", stdout=mock.MagicMock())
        expire.assert_not_called()

    # --- who may switch
    def test_only_superadmin_may_switch_and_it_is_audited(self):
        self.assertEqual(self.switch(False, self.admin).status_code, 403)
        self.assertEqual(self.switch(False, self.author).status_code, 403)
        self.assertEqual(Client().post(reverse("plugins:module_toggle", args=["shop"]), {"enable": "0"}).status_code, 302)
        self.assertTrue(modules.is_enabled("shop"))  # nothing changed
        AuditLog.objects.all().delete()
        self.switch(False)
        self.switch(True)
        self.switch(True)  # no change: not logged again
        events = list(AuditLog.objects.filter(object_type__in=["Module", "ماژول"]).order_by("id"))
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e.actor_id == self.superadmin.pk for e in events))
        self.assertIn("Ravix Shop", events[0].message)

    def test_unknown_module_is_404(self):
        self.assertEqual(self.login(self.superadmin).post(reverse("plugins:module_toggle", args=["nope"]), {"enable": "1"}).status_code, 404)

    # --- the server lock
    def test_server_lock_forces_off_and_cannot_be_overridden(self):
        modules.set_enabled("shop", True)
        with mock.patch("apps.core.modules.is_installed", return_value=False):
            self.assertTrue(modules.server_locked("shop"))
            self.assertFalse(modules.is_enabled("shop"))
            r = self.switch(True)
            self.assertRedirects(r, reverse("plugins:list"), fetch_redirect_response=False)
            self.assertEqual(ModuleState.objects.get(key="shop").enabled, True)  # the saved choice is untouched
            self.assertFalse(modules.is_enabled("shop"))
            page = self.login(self.superadmin).get(reverse("plugins:list")).content.decode()
            self.assertIn("RAVIX_SHOP_ENABLED=false", page)
            self.assertRegex(page, r"<button[^>]*disabled")
            with self.assertRaises(ValueError):
                modules.set_enabled("shop", True)
            self.assertEqual(Client().get("/fa/shop/").status_code, 404)

    # --- the Plugins page
    def test_plugins_page_shows_the_module_card(self):
        page = self.login(self.superadmin).get(reverse("plugins:list"))
        html = page.content.decode()
        self.assertContains(page, "Ravix Shop")
        self.assertIn("brand/ravix-icon", html)
        self.assertIn("1.0.0", html)
        self.assertIn(reverse("plugins:module_toggle", args=["shop"]), html)

    def test_admin_without_the_capability_sees_the_card_but_no_switch(self):
        html = self.login(self.admin).get(reverse("plugins:list")).content.decode()
        self.assertIn("Ravix Shop", html)
        self.assertNotIn(reverse("plugins:module_toggle", args=["shop"]), html)


class SetupGuideTests(ShopTestCase):
    def setUp(self):
        super().setUp()
        self.c = Client()
        self.c.force_login(self.superadmin)
        self.url = reverse("plugins:module_setup", args=["shop"])

    def steps(self):
        return {s.key: s.done for s in modules.wizard("shop", self.c.get(self.url).wsgi_request)}

    def test_steps_tick_themselves(self):
        self.assertEqual(self.c.get(self.url).status_code, 200)
        Product.objects.all().delete()
        ShopSettings.objects.all().delete()
        self.assertEqual(self.steps(), {"currency": False, "store": False, "gateway": False, "product": False})
        cfg = ShopSettings.for_site(self.site)
        cfg.store_name = "Ravix Store"
        cfg.save()
        GatewayConfig.objects.create(site=self.site, gateway="sandbox", enabled=True)
        Product.objects.create(site=self.site, name="p", slug="p", price=5, stock=1)
        self.assertEqual(self.steps(), {"currency": True, "store": True, "gateway": True, "product": True})

    def test_can_be_skipped_and_reopened(self):
        r = self.c.post(self.url, {"action": "dismiss"})
        self.assertRedirects(r, reverse("plugins:list"), fetch_redirect_response=False)
        self.assertEqual(ModuleState.objects.get(key="shop").setup, "dismissed")
        self.assertEqual(self.c.get(self.url).status_code, 200)  # reopened from the settings link
        self.c.post(self.url, {"action": "finish"})
        self.assertEqual(ModuleState.objects.get(key="shop").setup, "done")
        self.assertIn(self.url, self.c.get(reverse("shop_panel:settings")).content.decode())

    def test_guide_is_not_available_while_off_or_for_ordinary_staff(self):
        staff = Client()
        staff.force_login(self.author)
        self.assertEqual(staff.get(self.url).status_code, 403)
        self.c.post(reverse("plugins:module_toggle", args=["shop"]), {"enable": "0"})
        self.assertEqual(self.c.get(self.url).status_code, 404)

    def test_store_details_are_saved_from_settings(self):
        r = self.c.post(reverse("shop_panel:settings"), {"store_name": "فروشگاه راویکس", "store_email": "shop@example.com", "currency": ShopSettings.for_site(self.site).currency,
                                                          "low_stock_threshold": 3, "notify_customers": "on"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(ShopSettings.for_site(self.site).store_name, "فروشگاه راویکس")


class GenericModuleTests(ShopTestCase):
    """The pattern is reusable: a second module gets the same gating without touching the shop."""

    def setUp(self):
        super().setUp()
        self.mod = modules.ModuleDef(key="studio", name="Ravix Studio", description="x", version="0.1.0", app_label="shop", namespaces=("studio_ns",),
                                     path_prefixes=("/api/v1/studio/",), hook_prefixes=("studio.",))
        modules.register_module(self.mod)
        self.addCleanup(modules._registry.pop, "studio", None)

    def test_registered_module_is_gated_by_path_hook_and_state(self):
        self.assertIs(modules.module_for_path("/api/v1/studio/x"), self.mod)
        self.assertIs(modules.module_for_namespaces(["studio_ns"]), self.mod)
        modules.set_enabled("studio", False)
        self.assertFalse(modules.hook_allowed("studio.saved"))
        self.assertEqual(Client().get("/api/v1/studio/anything/").status_code, 404)
        modules.set_enabled("studio", True)
        self.assertTrue(modules.hook_allowed("studio.saved"))

    def test_nav_item_with_module_key_follows_the_switch(self):
        from apps.core.navigation import NavItem, _registry, register_nav_item

        register_nav_item(NavItem(key="studio.home", label="Studio", url="/panel/studio/", module="studio"))
        self.addCleanup(_registry.pop, "studio.home", None)
        modules.set_enabled("studio", False)
        self.assertNotIn("studio.home", [i.key for i in get_nav_items()])
        modules.set_enabled("studio", True)
        self.assertIn("studio.home", [i.key for i in get_nav_items()])

    def test_card_appears_on_the_plugins_page(self):
        c = Client()
        c.force_login(self.superadmin)
        self.assertContains(c.get(reverse("plugins:list")), "Ravix Studio")


class ModuleMigrationTests(TransactionTestCase):
    before = [("shop", "0007_shopsettings_store_info")]
    after = [("shop", "0008_enable_module_for_existing_sites")]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def make_user(self):
        from apps.accounts.models import User  # only the shop is rolled back: the accounts schema is current

        return User.objects.create(username="old", email="old@example.com")

    def test_site_with_users_keeps_the_shop_on(self):
        old = self.migrate(self.before)
        self.make_user()
        new = self.migrate(self.after)
        row = new.get_model("core", "ModuleState").objects.get(key="shop")
        self.assertEqual((row.enabled, row.setup), (True, "done"))

    def test_fresh_install_has_no_row_and_so_starts_off(self):
        self.migrate(self.before)
        new = self.migrate(self.after)
        self.assertFalse(new.get_model("core", "ModuleState").objects.filter(key="shop").exists())

    def test_reversible_and_repeatable(self):
        old = self.migrate(self.before)
        self.make_user()
        self.migrate(self.after)
        self.migrate(self.before)
        from apps.accounts.models import User

        self.assertEqual(User.objects.count(), 1)  # nothing lost
        again = self.migrate(self.after)
        self.assertEqual(again.get_model("core", "ModuleState").objects.filter(key="shop").count(), 1)  # no duplicate
