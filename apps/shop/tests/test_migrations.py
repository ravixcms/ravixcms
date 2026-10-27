"""Migrations on a site that already has products and orders: data stays intact, going back and forward again is safe."""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings


class MigrationBase(TransactionTestCase):
    before = [("shop", "0005_order_language")]
    after = [("shop", "0006_shop_settings")]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def seed(self, apps):
        Site = apps.get_model("sites", "Site")
        Product = apps.get_model("shop", "Product")
        Order = apps.get_model("shop", "Order")
        site, _ = Site.objects.get_or_create(slug="main", defaults={"name": "Main", "is_default": True})
        Product.objects.create(site=site, name="old", slug="old", price=120000, stock=3)
        Order.objects.create(site=site, number="RV-OLD1", token="t" * 40, customer_name="Old Customer", phone="09123456789", total=120000, subtotal=120000, currency="IRT")
        return site


class ShopSettingsMigrationTests(MigrationBase):
    before = [("shop", "0005_order_language")]
    after = [("shop", "0006_shop_settings")]

    @override_settings(SHOP_CURRENCY_CODE="IRT")
    def test_forward_keeps_data_and_currency(self):
        old = self.migrate(self.before)
        site = self.seed(old)
        new = self.migrate(self.after)
        cfg = new.get_model("shop", "ShopSettings").objects.get(site_id=site.pk)
        self.assertEqual(cfg.currency, "IRT")
        self.assertFalse(cfg.show_in_toman)
        self.assertEqual(new.get_model("shop", "Product").objects.get(slug="old").price, 120000)
        order = new.get_model("shop", "Order").objects.get(number="RV-OLD1")
        self.assertEqual((order.total, order.currency), (120000, "IRT"))

    @override_settings(SHOP_CURRENCY_CODE="IRR")
    def test_deployment_currency_is_kept_for_existing_sites(self):
        self.seed(self.migrate(self.before))
        new = self.migrate(self.after)
        self.assertEqual(set(new.get_model("shop", "ShopSettings").objects.values_list("currency", flat=True)), {"IRR"})

    def test_backward_then_forward_is_lossless(self):
        old = self.migrate(self.before)
        self.seed(old)
        self.migrate(self.after)
        back = self.migrate(self.before)  # reversible: the settings table goes, shop data stays
        self.assertEqual(back.get_model("shop", "Order").objects.count(), 1)
        self.assertEqual(back.get_model("shop", "Product").objects.count(), 1)
        again = self.migrate(self.after)
        self.assertEqual(again.get_model("shop", "ShopSettings").objects.count(), again.get_model("sites", "Site").objects.count())
        self.assertEqual(again.get_model("shop", "Order").objects.get().total, 120000)

    def test_rerunning_the_data_step_does_not_duplicate(self):
        from importlib import import_module

        self.seed(self.migrate(self.before))
        new = self.migrate(self.after)
        step = import_module("apps.shop.migrations.0006_shop_settings").create_settings
        before = new.get_model("shop", "ShopSettings").objects.count()
        step(new, None)
        self.assertEqual(new.get_model("shop", "ShopSettings").objects.count(), before)


class CatalogMigrationTests(MigrationBase):
    """0009: variants, tags, nested categories. Existing products and orders keep working, and the step can be undone."""

    before = [("shop", "0008_enable_module_for_existing_sites")]
    after = [("shop", "0009_catalog_variants_tags")]

    def test_forward_keeps_products_and_orders(self):
        site = self.seed(self.migrate(self.before))
        new = self.migrate(self.after)
        product = new.get_model("shop", "Product").objects.get(slug="old")
        self.assertEqual((product.price, product.stock, product.sku, product.weight_grams), (120000, 3, "", 0))
        self.assertEqual(new.get_model("shop", "Order").objects.get().total, 120000)
        self.assertEqual(new.get_model("shop", "ProductVariant").objects.count(), 0)
        # two products without a SKU do not clash on the unique constraint
        new.get_model("shop", "Product").objects.create(site_id=site.pk, name="b", slug="b", price=1)

    def test_backward_then_forward_is_lossless(self):
        self.seed(self.migrate(self.before))
        self.migrate(self.after)
        back = self.migrate(self.before)
        self.assertEqual(back.get_model("shop", "Product").objects.get().price, 120000)
        self.assertEqual(back.get_model("shop", "Order").objects.get().total, 120000)
        again = self.migrate(self.after)
        self.assertEqual(again.get_model("shop", "Product").objects.get().stock, 3)
