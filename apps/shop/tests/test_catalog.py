"""Ravix Shop catalog: variable products with variants, SKU, stock alerts, tags, nested categories, related products, weight."""
from unittest import mock

from django.core import mail
from django.test import Client, override_settings
from django.urls import reverse

from apps.shop import services
from apps.shop.forms import AttributeForm, CategoryForm, ProductForm
from apps.shop.models import (AttributeValue, Order, OrderItem, Product, ProductAttribute, ProductCategory, ProductTag, ProductVariant, ShopSettings)

from .helpers import ShopTestCase


class CatalogCase(ShopTestCase):
    def make_attrs(self):
        color = ProductAttribute.objects.create(site=self.site, name="Colour", slug="colour")
        size = ProductAttribute.objects.create(site=self.site, name="Size", slug="size")
        vals = {}
        for attr, names in ((color, ["Red", "Blue"]), (size, ["S", "L"])):
            for i, n in enumerate(names):
                vals[n] = AttributeValue.objects.create(attribute=attr, value=n, slug=n.lower(), order=i)
        return color, size, vals

    def make_variable(self, slug="shirt", **kw):
        color, size, vals = self.make_attrs() if not ProductAttribute.objects.filter(site=self.site).exists() else (None, None, {v.value: v for v in AttributeValue.objects.filter(attribute__site=self.site)})
        p = Product.objects.create(site=self.site, name="Shirt", slug=slug, kind=Product.Kind.VARIABLE, price=1000, track_stock=False, **kw)
        p.variation_attributes.set(ProductAttribute.objects.filter(site=self.site))
        self.vals = vals
        return p

    def add_variant(self, p, opts, price=1000, stock=5, sku="", **kw):
        v = ProductVariant(product=p, price=price, stock=stock, sku=sku, options_key=ProductVariant.key_for([self.vals[o].pk for o in opts]), **kw)
        v.save()
        v.options.set([self.vals[o] for o in opts])
        p.sync_variable_price()
        return v

    def admin_client(self):
        c = Client()
        c.force_login(self.admin)
        return c


class VariantModelTests(CatalogCase):
    def test_prices_discount_and_range(self):
        p = self.make_variable(discount_percent=10)
        self.add_variant(p, ["Red", "S"], price=1000)
        self.add_variant(p, ["Red", "L"], price=2000)
        low, high = p.price_range
        self.assertEqual((low, high), (900, 1800))
        self.assertTrue(p.has_price_range)
        self.assertEqual(Product.objects.get(pk=p.pk).price, 1000)  # follows the cheapest variant

    def test_in_stock_follows_active_variants(self):
        p = self.make_variable()
        v = self.add_variant(p, ["Red", "S"], stock=0)
        self.assertFalse(p.in_stock)
        w = self.add_variant(p, ["Blue", "S"], stock=2)
        p = Product.objects.get(pk=p.pk)
        self.assertTrue(p.in_stock)
        w.is_active = False
        w.save()
        self.assertFalse(Product.objects.get(pk=p.pk).in_stock)
        self.assertEqual(v.label, "Red / S")

    def test_one_variant_per_combination(self):
        from django.db import IntegrityError, transaction

        p = self.make_variable()
        self.add_variant(p, ["Red", "S"])
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.add_variant(p, ["Red", "S"])

    def test_weight_falls_back_to_the_product(self):
        p = self.make_variable(weight_grams=300)
        a = self.add_variant(p, ["Red", "S"])
        b = self.add_variant(p, ["Red", "L"], weight_grams=450)
        self.assertEqual((a.weight, b.weight), (300, 450))

    def test_sku_unique_across_products_and_variants(self):
        from apps.shop.forms import sku_taken

        p = self.make_variable(sku="SHIRT")
        self.add_variant(p, ["Red", "S"], sku="SHIRT-RS")
        self.assertTrue(sku_taken(self.site, "SHIRT-RS"))
        self.assertTrue(sku_taken(self.site, "SHIRT"))
        self.assertFalse(sku_taken(self.site, "SHIRT", product=p))
        self.assertFalse(sku_taken(self.site, ""))
        self.assertFalse(sku_taken(self.site, "FREE"))


class ProductFormTests(CatalogCase):
    def form(self, **over):
        data = {"name": "x", "slug": "x1", "kind": "physical", "price": "100", "stock": 1, "track_stock": "on", "is_active": "on"}
        data.update(over)
        return ProductForm(data, site=self.site)

    def test_variable_requires_attributes_and_ignores_product_stock(self):
        self.assertFalse(self.form(kind="variable").is_valid())
        color, _, _ = self.make_attrs()
        f = self.form(kind="variable", variation_attributes=[color.pk])
        self.assertTrue(f.is_valid(), f.errors)
        self.assertFalse(f.cleaned_data["track_stock"])

    def test_sku_weight_tags_related(self):
        other = Product.objects.create(site=self.site, name="o", slug="o", price=5, stock=1)
        f = self.form(sku="ABC-1", weight_grams="250", tags_text="Summer, summer, جدید ,", related=[other.pk])
        self.assertTrue(f.is_valid(), f.errors)
        p = f.save()
        self.assertEqual((p.sku, p.weight_grams), ("ABC-1", 250))
        self.assertEqual(sorted(p.tags.values_list("slug", flat=True)), ["summer", "جدید"])
        self.assertEqual(list(p.related.all()), [other])
        self.assertFalse(self.form(slug="x2", sku="ABC-1").is_valid())  # duplicate SKU on the site

    def test_product_cannot_relate_to_itself_or_other_sites(self):
        p = self.product
        f = ProductForm({"name": "n", "slug": "book", "kind": "physical", "price": "100", "stock": 1, "track_stock": "on", "is_active": "on", "related": [p.pk]}, instance=p, site=self.site)
        self.assertFalse(f.is_valid())  # itself is not even a choice

    def test_tags_are_site_scoped_and_reused(self):
        for slug, tag in (("x1", "Hot"), ("x2", "hot")):
            f = self.form(slug=slug, tags_text=tag)
            self.assertTrue(f.is_valid(), f.errors)
            f.save()
        self.assertEqual(ProductTag.objects.filter(site=self.site).count(), 1)


class AttributeTests(CatalogCase):
    def test_values_parsed_one_per_line_and_used_values_are_protected(self):
        f = AttributeForm({"name": "Colour", "values": "Red\nBlue\n\n red "}, site=self.site)
        self.assertTrue(f.is_valid(), f.errors)
        attr = f.save()
        self.assertEqual(list(attr.values.values_list("value", flat=True)), ["Red", "Blue"])
        p = Product.objects.create(site=self.site, name="s", slug="s", kind="variable", price=10)
        self.vals = {v.value: v for v in attr.values.all()}
        v = ProductVariant(product=p, price=10, options_key=ProductVariant.key_for([self.vals["Red"].pk]))
        v.save()
        v.options.set([self.vals["Red"]])
        again = AttributeForm({"name": "Colour", "values": "Blue"}, site=self.site, instance=attr)
        self.assertFalse(again.is_valid())  # Red is used by a variant
        ok = AttributeForm({"name": "Colour", "values": "Red\nGreen"}, site=self.site, instance=attr)
        self.assertTrue(ok.is_valid(), ok.errors)
        ok.save()
        self.assertEqual(sorted(attr.values.values_list("value", flat=True)), ["Green", "Red"])

    def test_delete_blocked_while_used(self):
        color, _, vals = self.make_attrs()
        p = self.make_variable()
        self.add_variant(p, ["Red", "S"])
        c = self.admin_client()
        c.post(reverse("shop_panel:attribute_delete", args=[color.pk]))
        self.assertTrue(ProductAttribute.objects.filter(pk=color.pk).exists())
        unused = ProductAttribute.objects.create(site=self.site, name="Material", slug="material")
        c.post(reverse("shop_panel:attribute_delete", args=[unused.pk]))
        self.assertFalse(ProductAttribute.objects.filter(pk=unused.pk).exists())

    def test_panel_pages(self):
        c = self.admin_client()
        self.assertEqual(c.get(reverse("shop_panel:attributes")).status_code, 200)
        r = c.post(reverse("shop_panel:attribute_create"), {"name": "Size", "values": "S\nM\nL"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(ProductAttribute.objects.get(slug="size").values.count(), 3)


class PanelVariantTests(CatalogCase):
    def test_generate_is_complete_and_idempotent(self):
        p = self.make_variable()
        c = self.admin_client()
        c.post(reverse("shop_panel:variants_generate", args=[p.pk]))
        self.assertEqual(p.variants.count(), 4)  # 2 colours x 2 sizes
        c.post(reverse("shop_panel:variants_generate", args=[p.pk]))
        self.assertEqual(p.variants.count(), 4)
        labels = {v.label for v in p.variants.all()}
        self.assertEqual({frozenset(x.split(" / ")) for x in labels}, {frozenset(x) for x in ({"Red", "S"}, {"Red", "L"}, {"Blue", "S"}, {"Blue", "L"})})

    def test_generate_refuses_a_flood(self):
        p = self.make_variable()
        big = ProductAttribute.objects.create(site=self.site, name="Big", slug="big")
        for i in range(30):
            AttributeValue.objects.create(attribute=big, value=f"v{i}", slug=f"v{i}")
        p.variation_attributes.add(big)
        self.admin_client().post(reverse("shop_panel:variants_generate", args=[p.pk]))
        self.assertEqual(p.variants.count(), 0)  # 2 x 2 x 30 = 120 > 100

    def test_save_variants_converts_price_and_syncs_product(self):
        ShopSettings.objects.update_or_create(site=self.site, defaults={"currency": "IRR", "show_in_toman": True})
        p = self.make_variable()
        self.admin_client().post(reverse("shop_panel:variants_generate", args=[p.pk]))
        vs = list(p.variants.order_by("id"))
        data = {"variants-TOTAL_FORMS": len(vs), "variants-INITIAL_FORMS": len(vs), "variants-MIN_NUM_FORMS": 0, "variants-MAX_NUM_FORMS": 1000}
        for i, v in enumerate(vs):
            data.update({f"variants-{i}-id": v.pk, f"variants-{i}-price": str(500 + i), f"variants-{i}-stock": 4, f"variants-{i}-track_stock": "on", f"variants-{i}-is_active": "on",
                         f"variants-{i}-sku": f"SKU-{i}"})
        r = self.admin_client().post(reverse("shop_panel:variants_save", args=[p.pk]), data)
        self.assertEqual(r.status_code, 302, r.content[:500])
        first = ProductVariant.objects.get(pk=vs[0].pk)
        self.assertEqual((first.price, first.stock, first.sku), (5000, 4, "SKU-0"))  # 500 toman typed -> 5,000 rials stored
        self.assertEqual(Product.objects.get(pk=p.pk).price, 5000)

    def test_duplicate_sku_in_the_table_is_rejected(self):
        p = self.make_variable()
        self.admin_client().post(reverse("shop_panel:variants_generate", args=[p.pk]))
        vs = list(p.variants.order_by("id"))[:2]
        data = {"variants-TOTAL_FORMS": 2, "variants-INITIAL_FORMS": 2, "variants-MIN_NUM_FORMS": 0, "variants-MAX_NUM_FORMS": 1000}
        for i, v in enumerate(vs):
            data.update({f"variants-{i}-id": v.pk, f"variants-{i}-price": "10", f"variants-{i}-stock": 1, f"variants-{i}-is_active": "on", f"variants-{i}-sku": "SAME"})
        r = self.admin_client().post(reverse("shop_panel:variants_save", args=[p.pk]), data)
        self.assertEqual(r.status_code, 400)

    def test_other_site_product_is_404(self):
        from apps.sites.models import Site

        other = Site.objects.create(name="o", slug="o2")
        foreign = Product.objects.create(site=other, name="f", slug="f", kind="variable", price=5)
        self.assertEqual(self.admin_client().post(reverse("shop_panel:variants_generate", args=[foreign.pk])).status_code, 404)

    def test_product_page_shows_the_variant_editor(self):
        p = self.make_variable()
        self.admin_client().post(reverse("shop_panel:variants_generate", args=[p.pk]))
        page = self.admin_client().get(reverse("shop_panel:product_edit", args=[p.pk])).content.decode()
        self.assertIn("variants-0-price", page)


class StorefrontVariantTests(CatalogCase):
    def setUp(self):
        super().setUp()
        self.p = self.make_variable()
        self.red_s = self.add_variant(self.p, ["Red", "S"], price=1000, stock=3, sku="R-S")
        self.blue_l = self.add_variant(self.p, ["Blue", "L"], price=2000, stock=0, sku="B-L")

    def test_detail_lists_variants_and_price_range(self):
        page = Client().get("/en/shop/p/shirt/").content.decode()
        self.assertIn("Red / S", page)
        self.assertIn("name=\"variant\"", page)
        self.assertIn("R-S", page)
        self.assertIn('"AggregateOffer"', page)

    def test_adding_requires_a_valid_variant(self):
        c = Client()
        r = c.post("/en/shop/cart/add/", {"product": self.p.pk, "qty": 1})
        self.assertEqual(c.session.get(f"shop_cart_{self.site.pk}") or {}, {})
        other = self.make_variable("other")  # a variant of another product
        foreign = self.add_variant(other, ["Blue", "S"])
        c.post("/en/shop/cart/add/", {"product": self.p.pk, "variant": foreign.pk, "qty": 1})
        self.assertEqual(c.session.get(f"shop_cart_{self.site.pk}") or {}, {})
        c.post("/en/shop/cart/add/", {"product": self.p.pk, "variant": self.red_s.pk, "qty": 2})
        self.assertEqual(c.session[f"shop_cart_{self.site.pk}"], {f"{self.p.pk}:{self.red_s.pk}": 2})

    def test_simple_product_rejects_a_variant(self):
        r = Client().post("/en/shop/cart/add/", {"product": self.product.pk, "variant": self.red_s.pk, "qty": 1})
        self.assertEqual(r.status_code, 404)

    def test_checkout_snapshots_the_variant_and_reserves_its_stock(self):
        c = Client()
        c.post("/en/shop/cart/add/", {"product": self.p.pk, "variant": self.red_s.pk, "qty": 2})
        r = c.post("/en/shop/checkout/", self.checkout_data())
        self.assertEqual(r.status_code, 302)
        item = OrderItem.objects.get()
        self.assertEqual((item.variant_id, item.variant_label, item.sku, item.unit_price, item.quantity, item.kind), (self.red_s.pk, "Red / S", "R-S", 1000, 2, "physical"))
        self.red_s.refresh_from_db()
        self.assertEqual(self.red_s.stock, 1)
        self.assertEqual(Order.objects.get().total, 2000)

    def test_out_of_stock_variant_cannot_be_ordered(self):
        c = Client()
        c.post("/en/shop/cart/add/", {"product": self.p.pk, "variant": self.blue_l.pk, "qty": 1})
        r = c.post("/en/shop/checkout/", self.checkout_data())
        self.assertEqual(Order.objects.count(), 0)
        self.assertNotEqual(r.status_code, 302)

    def test_cancelling_restores_variant_stock_not_product_stock(self):
        c = Client()
        c.post("/en/shop/cart/add/", {"product": self.p.pk, "variant": self.red_s.pk, "qty": 3})
        c.post("/en/shop/checkout/", self.checkout_data())
        self.red_s.refresh_from_db()
        self.assertEqual(self.red_s.stock, 0)
        services.change_status(Order.objects.get(), Order.Status.CANCELLED)
        self.red_s.refresh_from_db()
        self.assertEqual(self.red_s.stock, 3)

    def test_tampered_cart_keys_are_ignored(self):
        c = Client()
        c.get("/en/shop/cart/")
        s = c.session
        s[f"shop_cart_{self.site.pk}"] = {"x:y": 1, "9999999999999999": 1, f"{self.p.pk}": 2, f"{self.p.pk}:999999": 1, f"{self.product.pk}:{self.red_s.pk}": 1}
        s.save()
        self.assertEqual(c.get("/en/shop/cart/").status_code, 200)
        lines, total = services.cart_lines(type("R", (), {"session": c.session})(), self.site)
        self.assertEqual((lines, total), ([], 0))

    def test_price_is_never_taken_from_the_browser(self):
        c = Client()
        c.post("/en/shop/cart/add/", {"product": self.p.pk, "variant": self.red_s.pk, "qty": 1, "price": 1, "unit_price": 1})
        c.post("/en/shop/checkout/", self.checkout_data())
        self.assertEqual(Order.objects.get().total, 1000)

    def test_cart_page_updates_by_line_key(self):
        c = Client()
        c.post("/en/shop/cart/add/", {"product": self.p.pk, "variant": self.red_s.pk, "qty": 1})
        key = f"{self.p.pk}:{self.red_s.pk}"
        self.assertIn(f"qty_{key}", c.get("/en/shop/cart/").content.decode())
        c.post("/en/shop/cart/update/", {f"qty_{key}": 2})
        self.assertEqual(c.session[f"shop_cart_{self.site.pk}"][key], 2)
        c.post("/en/shop/cart/update/", {f"qty_{key}": 0})
        self.assertEqual(c.session[f"shop_cart_{self.site.pk}"], {})

    def test_cart_weight_and_order_weight_snapshot(self):
        self.p.weight_grams = 200
        self.p.save()
        self.blue_l.weight_grams = 500
        self.blue_l.stock = 5
        self.blue_l.save()
        c = Client()
        c.post("/en/shop/cart/add/", {"product": self.p.pk, "variant": self.red_s.pk, "qty": 2})
        c.post("/en/shop/cart/add/", {"product": self.p.pk, "variant": self.blue_l.pk, "qty": 1})
        lines, _ = services.cart_lines(type("R", (), {"session": c.session})(), self.site)
        self.assertEqual(services.cart_summary(lines)["weight"], 2 * 200 + 500)
        c.post("/en/shop/checkout/", self.checkout_data())
        self.assertEqual(sorted(OrderItem.objects.values_list("weight_grams", flat=True)), [200, 500])


class LowStockTests(CatalogCase):
    def order(self, product, variant=None, qty=1):
        c = Client()
        data = {"product": product.pk, "qty": qty}
        if variant:
            data["variant"] = variant.pk
        c.post("/en/shop/cart/add/", data)
        return c.post("/en/shop/checkout/", self.checkout_data())

    def test_crossing_the_threshold_sends_one_email_and_the_hook(self):
        ShopSettings.objects.update_or_create(site=self.site, defaults={"low_stock_threshold": 3, "store_email": "owner@example.com"})
        p = Product.objects.create(site=self.site, name="Pen", slug="pen", price=10, stock=5, sku="PEN")
        mail.outbox.clear()
        with mock.patch("apps.plugins.hooks.run_action") as run, self.captureOnCommitCallbacks(execute=True):
            self.order(p, qty=1)  # 5 -> 4: still above 3
        self.assertEqual([m.to for m in mail.outbox if "owner@example.com" in m.to], [])
        mail.outbox.clear()
        with mock.patch("apps.plugins.hooks.run_action") as run, self.captureOnCommitCallbacks(execute=True):
            self.order(p, qty=1)  # 4 -> 3: reaches the threshold
        low = [m for m in mail.outbox if m.to == ["owner@example.com"]]
        self.assertEqual(len(low), 1)
        self.assertIn("Pen", low[0].body)
        self.assertIn("PEN", low[0].body)
        names = [c.args[0] for c in run.call_args_list]
        self.assertIn("shop.low_stock", names)
        mail.outbox.clear()
        with mock.patch("apps.plugins.hooks.run_action"), self.captureOnCommitCallbacks(execute=True):
            self.order(p, qty=1)  # 3 -> 2: already low, no second alert
        self.assertEqual([m for m in mail.outbox if m.to == ["owner@example.com"]], [])

    def test_variant_stock_alerts_and_per_product_override(self):
        ShopSettings.objects.update_or_create(site=self.site, defaults={"low_stock_threshold": 1, "store_email": "owner@example.com"})
        p = self.make_variable(low_stock_threshold=4)
        v = self.add_variant(p, ["Red", "S"], stock=5, sku="RS")
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.order(p, v, qty=1)  # 5 -> 4, product threshold 4
        mails = [m for m in mail.outbox if m.to == ["owner@example.com"]]
        self.assertEqual(len(mails), 1)
        self.assertIn("Red / S", mails[0].body)

    def test_selling_the_last_unit_alerts(self):
        ShopSettings.objects.update_or_create(site=self.site, defaults={"low_stock_threshold": 0, "store_email": "owner@example.com"})
        p = Product.objects.create(site=self.site, name="Last", slug="last1", price=10, stock=1)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.order(p)
        self.assertEqual(len([m for m in mail.outbox if m.to == ["owner@example.com"]]), 1)

    def test_alert_is_off_when_the_module_is_off(self):
        from apps.core import modules

        ShopSettings.objects.update_or_create(site=self.site, defaults={"low_stock_threshold": 10, "store_email": "owner@example.com"})
        p = Product.objects.create(site=self.site, name="Pen", slug="pen2", price=10, stock=5)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.order(p)
        modules.set_enabled("shop", False)
        for cb in callbacks:
            cb()
        self.assertEqual([m for m in mail.outbox if m.to == ["owner@example.com"]], [])

    def test_panel_filters_and_badges(self):
        ShopSettings.objects.update_or_create(site=self.site, defaults={"low_stock_threshold": 3})
        Product.objects.create(site=self.site, name="LowOne", slug="low1", price=10, stock=2)
        Product.objects.create(site=self.site, name="OutOne", slug="out1", price=10, stock=0)
        Product.objects.create(site=self.site, name="PlentyOne", slug="plenty1", price=10, stock=50)
        c = self.admin_client()
        low = c.get(reverse("shop_panel:products") + "?stock=low").content.decode()
        self.assertIn("LowOne", low)
        self.assertNotIn("PlentyOne", low)
        self.assertNotIn("OutOne", low)
        out = c.get(reverse("shop_panel:products") + "?stock=out").content.decode()
        self.assertIn("OutOne", out)
        self.assertNotIn("LowOne", out)
        self.assertIn("?stock=low", c.get(reverse("shop_panel:products")).content.decode())

    def test_variable_product_with_a_low_variant_is_in_the_low_filter(self):
        ShopSettings.objects.update_or_create(site=self.site, defaults={"low_stock_threshold": 3})
        p = self.make_variable()
        self.add_variant(p, ["Red", "S"], stock=2)
        self.add_variant(p, ["Blue", "S"], stock=40)
        page = self.admin_client().get(reverse("shop_panel:products") + "?stock=low").content.decode()
        self.assertIn("Shirt", page)


class TagTests(CatalogCase):
    def test_filter_search_and_page(self):
        a = Product.objects.create(site=self.site, name="Alpha", slug="alpha", price=10, stock=1, sku="AL-1")
        b = Product.objects.create(site=self.site, name="Beta", slug="beta", price=10, stock=1)
        t = ProductTag.objects.create(site=self.site, name="Summer", slug="summer")
        a.tags.add(t)
        base = Product.objects.filter(site=self.site)
        self.assertEqual([p.slug for p in services.search_products(base, tag="summer")], ["alpha"])
        self.assertEqual([p.slug for p in services.search_products(base, q="summer")], ["alpha"])
        self.assertEqual([p.slug for p in services.search_products(base, q="AL-1")], ["alpha"])  # SKU search
        page = Client().get("/en/shop/?tag=summer").content.decode()
        self.assertIn("Alpha", page)
        self.assertNotIn("Beta", page)
        self.assertIn("/shop/?tag=summer", Client().get("/en/shop/p/alpha/").content.decode())

    def test_variant_sku_is_searchable(self):
        p = self.make_variable()
        self.add_variant(p, ["Red", "S"], sku="VAR-SKU-9")
        self.assertEqual([x.slug for x in services.search_products(Product.objects.filter(site=self.site), q="VAR-SKU-9")], ["shirt"])

    def test_api_tag_filter(self):
        a = Product.objects.create(site=self.site, name="Alpha", slug="alpha", price=10, stock=1)
        t = ProductTag.objects.create(site=self.site, name="Hot", slug="hot")
        a.tags.add(t)
        data = Client().get("/api/v1/shop/products/?tag=hot").json()
        rows = data.get("results", data)
        self.assertEqual([r["slug"] for r in rows], ["alpha"])
        self.assertEqual(rows[0]["tags"], ["hot"])


class NestedCategoryTests(CatalogCase):
    def tree(self):
        top = ProductCategory.objects.create(site=self.site, name="Clothes", slug="clothes")
        mid = ProductCategory.objects.create(site=self.site, name="Shirts", slug="shirts", parent=top)
        leaf = ProductCategory.objects.create(site=self.site, name="Polo", slug="polo", parent=mid)
        return top, mid, leaf

    def form(self, instance=None, **data):
        base = {"name": data.pop("name", "N"), "slug": data.pop("slug", ""), "order": 100}
        base.update(data)
        return CategoryForm(base, site=self.site, instance=instance)

    def test_paths_and_descendants(self):
        top, mid, leaf = self.tree()
        self.assertEqual(leaf.path_name, "Clothes › Shirts › Polo")
        self.assertEqual(sorted(top.descendant_ids()), sorted([top.pk, mid.pk, leaf.pk]))
        self.assertEqual((top.depth, leaf.depth, top.subtree_height()), (1, 3, 2))

    def test_filter_includes_subcategories(self):
        top, mid, leaf = self.tree()
        p = Product.objects.create(site=self.site, name="PoloShirt", slug="poloshirt", price=10, stock=1, category=leaf)
        q = Product.objects.create(site=self.site, name="Other", slug="other", price=10, stock=1)
        base = Product.objects.filter(site=self.site)
        self.assertEqual([x.slug for x in services.search_products(base, category="clothes")], ["poloshirt"])
        self.assertEqual([x.slug for x in services.search_products(base, category="polo")], ["poloshirt"])
        self.assertEqual(services.search_products(base, category="nonexistent").count(), 0)

    def test_cycle_and_self_parent_are_refused(self):
        top, mid, leaf = self.tree()
        self.assertNotIn(leaf.pk, [c.pk for c in self.form(instance=top).fields["parent"].queryset])  # not even offered
        f = self.form(instance=top, name=top.name, slug=top.slug, parent=leaf.pk)
        self.assertFalse(f.is_valid())
        f2 = self.form(instance=top, name=top.name, slug=top.slug, parent=top.pk)
        self.assertFalse(f2.is_valid())

    def test_depth_limit(self):
        top, mid, leaf = self.tree()
        l4 = self.form(name="L4", slug="l4", parent=leaf.pk)
        self.assertTrue(l4.is_valid(), l4.errors)
        l4.save()
        l5 = self.form(name="L5", slug="l5", parent=ProductCategory.objects.get(slug="l4").pk)
        self.assertFalse(l5.is_valid())  # more than MAX_DEPTH levels
        move = self.form(instance=top, name=top.name, slug=top.slug, parent="")
        self.assertTrue(move.is_valid())

    def test_other_sites_category_cannot_be_a_parent(self):
        from apps.sites.models import Site

        other = Site.objects.create(name="o", slug="o3")
        foreign = ProductCategory.objects.create(site=other, name="F", slug="f")
        self.assertFalse(self.form(name="X", slug="x", parent=foreign.pk).is_valid())

    def test_tree_order_and_storefront_dropdown(self):
        top, mid, leaf = self.tree()
        ProductCategory.objects.create(site=self.site, name="Books", slug="books2", order=1)
        order = [(c.slug, d) for c, d in services.category_tree(self.site)]
        self.assertEqual(order, [("books2", 0), ("clothes", 0), ("shirts", 1), ("polo", 2)])
        self.assertIn("polo", Client().get("/en/shop/").content.decode())

    def test_damaged_tree_never_loops(self):
        top, mid, leaf = self.tree()
        ProductCategory.objects.filter(pk=top.pk).update(parent=leaf)  # a cycle written behind the form's back
        self.assertTrue(len(services.category_tree(self.site)) <= 3)
        self.assertTrue(len(ProductCategory.objects.get(pk=leaf.pk).ancestors()) <= ProductCategory.MAX_DEPTH + 2)
        self.assertEqual(Client().get("/en/shop/").status_code, 200)

    def test_breadcrumbs_on_the_product_page_follow_the_path(self):
        top, mid, leaf = self.tree()
        Product.objects.create(site=self.site, name="PoloShirt", slug="poloshirt", price=10, stock=1, category=leaf)
        page = Client().get("/en/shop/p/poloshirt/").content.decode()
        self.assertIn("Clothes › Shirts › Polo", page)
        self.assertIn("category=shirts", page)

    def test_deleting_a_parent_keeps_the_children(self):
        top, mid, leaf = self.tree()
        self.admin_client().post(reverse("shop_panel:category_delete", args=[mid.pk]))
        self.assertIsNone(ProductCategory.objects.get(pk=leaf.pk).parent_id)


class RelatedProductTests(CatalogCase):
    def test_manual_first_then_same_category_or_tag(self):
        cat = ProductCategory.objects.create(site=self.site, name="C", slug="c")
        tag = ProductTag.objects.create(site=self.site, name="T", slug="t")
        main = Product.objects.create(site=self.site, name="Main", slug="main", price=10, stock=1, category=cat)
        main.tags.add(tag)
        manual = Product.objects.create(site=self.site, name="Manual", slug="manual", price=10, stock=1)
        same_cat = Product.objects.create(site=self.site, name="SameCat", slug="samecat", price=10, stock=1, category=cat)
        same_tag = Product.objects.create(site=self.site, name="SameTag", slug="sametag", price=10, stock=1)
        same_tag.tags.add(tag)
        unrelated = Product.objects.create(site=self.site, name="Unrelated", slug="unrelated", price=10, stock=1)
        hidden = Product.objects.create(site=self.site, name="Hidden", slug="hidden2", price=10, stock=1, category=cat, is_active=False)
        main.related.add(manual)
        names = [p.name for p in services.related_products(main)]
        self.assertEqual(names[0], "Manual")
        self.assertEqual(set(names), {"Manual", "SameCat", "SameTag"})
        self.assertNotIn("Main", names)
        self.assertNotIn("Hidden", names)
        self.assertNotIn("Unrelated", names)

    def test_limit_and_inactive_manual_picks(self):
        main = Product.objects.create(site=self.site, name="Main", slug="main", price=10, stock=1)
        off = Product.objects.create(site=self.site, name="Off", slug="off", price=10, stock=1, is_active=False)
        main.related.add(off)
        self.assertEqual(services.related_products(main), [])
        cat = ProductCategory.objects.create(site=self.site, name="C", slug="c")
        main.category = cat
        main.save()
        for i in range(8):
            Product.objects.create(site=self.site, name=f"P{i}", slug=f"p{i}", price=10, stock=1, category=cat)
        self.assertEqual(len(services.related_products(main)), 4)

    def test_other_site_products_are_never_related(self):
        from apps.sites.models import Site

        other = Site.objects.create(name="o", slug="o4")
        cat = ProductCategory.objects.create(site=self.site, name="C", slug="c")
        main = Product.objects.create(site=self.site, name="Main", slug="main", price=10, stock=1, category=cat)
        foreign = Product.objects.create(site=other, name="Foreign", slug="foreign", price=10, stock=1)
        main.related.add(foreign)
        self.assertEqual(services.related_products(main), [])

    def test_related_block_on_the_detail_page_and_in_the_api(self):
        main = Product.objects.create(site=self.site, name="Main", slug="main", price=10, stock=1)
        rel = Product.objects.create(site=self.site, name="RelatedOne", slug="relatedone", price=10, stock=1)
        main.related.add(rel)
        self.assertIn("RelatedOne", Client().get("/en/shop/p/main/").content.decode())
        self.assertEqual(Client().get("/api/v1/shop/products/main/").json()["related"], ["relatedone"])


class CatalogApiTests(CatalogCase):
    def test_variants_in_the_api(self):
        p = self.make_variable(weight_grams=100, sku="SH")
        self.add_variant(p, ["Red", "S"], price=1000, stock=3, sku="R-S")
        self.add_variant(p, ["Blue", "L"], price=2500, stock=0, sku="B-L", is_active=False)
        row = Client().get("/api/v1/shop/products/shirt/").json()
        self.assertEqual((row["sku"], row["weight_grams"], row["price"], row["price_max"]), ("SH", 100, 1000, 1000))
        self.assertEqual(len(row["variants"]), 1)  # inactive variants are not published
        v = row["variants"][0]
        self.assertEqual((v["sku"], v["price"], v["stock"], v["weight_grams"]), ("R-S", 1000, 3, 100))
        self.assertEqual({o["label"] for o in v["options"]}, {"Red", "S"})
        self.assertNotIn("digital_file", str(row))

    def test_simple_product_has_no_variants(self):
        row = Client().get("/api/v1/shop/products/book/").json()
        self.assertEqual(row["variants"], [])
        self.assertEqual(row["price_max"], row["final_price"])
