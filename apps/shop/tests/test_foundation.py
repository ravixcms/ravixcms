"""Ravix Shop foundation: money model, currency settings, price form, customer notifications, the SMS hook, API currency."""
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.test import Client, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import translation

from apps.shop import money, notify
from apps.shop.forms import ProductForm, ShopSettingsForm
from apps.shop.models import Order, Product, ShopSettings

from .helpers import ShopTestCase

LANGS = ["fa", "en", "ar", "tr", "es", "fr", "de", "ru", "pt-br", "zh-hans"]


class MoneyTests(SimpleTestCase):
    def test_rial_to_toman_is_exactly_ten_to_one(self):
        """The 10x guard: 50,000 rials are 5,000 toman, never the other way round."""
        value, shown = money.display(50000, "IRR", show_toman=True)
        self.assertEqual((value, shown), (Decimal(5000), "IRT"))
        self.assertEqual(money.parse_amount("5000", "IRR", show_toman=True), 50000)
        self.assertEqual(money.parse_amount("50000", "IRR"), 50000)
        # a toman shop stores tomans as they are: no hidden factor
        self.assertEqual(money.parse_amount("5000", "IRT"), 5000)
        self.assertEqual(money.display(5000, "IRT"), (Decimal(5000), "IRT"))

    def test_round_trip_for_every_currency(self):
        for code, toman in (("IRR", False), ("IRR", True), ("IRT", False), ("USD", False), ("EUR", False)):
            for amount in (1, 10, 19990, 123456789):
                shown = money.input_value(amount, code, show_toman=toman)
                self.assertEqual(money.parse_amount(str(shown), code, show_toman=toman), amount, (code, toman, amount))

    def test_cents(self):
        self.assertEqual(money.parse_amount("19.99", "USD"), 1999)
        self.assertEqual(money.jsonld_offer(1999, "USD"), ("19.99", "USD"))
        with self.assertRaises(ValueError):
            money.parse_amount("19.999", "USD")
        with self.assertRaises(ValueError):
            money.parse_amount("12.5", "IRR")
        with self.assertRaises(ValueError):
            money.parse_amount("abc", "USD")
        with self.assertRaises(ValueError):
            money.parse_amount("NaN", "USD")

    def test_schema_org_uses_iso_rial_for_toman_shops(self):
        self.assertEqual(money.jsonld_offer(5000, "IRT"), ("50000", "IRR"))
        self.assertEqual(money.jsonld_offer(50000, "IRR"), ("50000", "IRR"))

    def test_formatting_per_language(self):
        expected_usd = {"en": "$1,234.56", "fr": "1 234,56 $US", "de": "1.234,56 $", "pt-br": "US$ 1.234,56", "zh-hans": "US$1,234.56"}
        for lang, want in expected_usd.items():
            with translation.override(lang):
                got = money.format_money(123456, "USD")
            self.assertEqual(got.replace(" ", " ").replace(" ", " "), want.replace(" ", " ").replace(" ", " "), lang)

    def test_persian_digits_only_for_persian(self):
        with translation.override("fa"):
            self.assertIn("۱۲۳٬۴۵۶", money.format_money(123456, "IRT"))
        with translation.override("ar"):
            self.assertNotRegex(money.format_money(123456, "IRT"), "[۰-۹]")
        with translation.override("en"):
            self.assertEqual(money.format_money(123456, "IRT").split()[0], "123,456")

    def test_every_language_formats_every_currency(self):
        for lang in LANGS:
            with translation.override(lang):
                for code in money.CURRENCIES:
                    self.assertTrue(money.format_money(123456, code).strip(), (lang, code))


class SettingsTests(ShopTestCase):
    def test_site_without_row_uses_deployment_default(self):
        ShopSettings.objects.filter(site=self.site).delete()
        with override_settings(SHOP_CURRENCY_CODE="IRR"):
            self.assertEqual(ShopSettings.for_site(self.site).currency, "IRR")
        with override_settings(SHOP_CURRENCY_CODE="XYZ"):
            self.assertEqual(ShopSettings.for_site(self.site).currency, "IRT")

    def test_currency_locked_once_catalog_exists(self):
        cfg = ShopSettings.for_site(self.site)
        form = ShopSettingsForm({"currency": "USD", "low_stock_threshold": 3}, instance=cfg, site=self.site)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(ShopSettings.for_site(self.site).currency, cfg.currency)  # product exists: the change is ignored

    def test_currency_can_change_on_empty_shop(self):
        Product.objects.all().delete()
        cfg = ShopSettings.for_site(self.site)
        form = ShopSettingsForm({"currency": "USD", "low_stock_threshold": 3}, instance=cfg, site=self.site)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(ShopSettings.for_site(self.site).currency, "USD")

    def test_toman_display_only_for_rial(self):
        Product.objects.all().delete()
        cfg = ShopSettings.for_site(self.site)
        bad = ShopSettingsForm({"currency": "USD", "show_in_toman": "on", "low_stock_threshold": 3}, instance=cfg, site=self.site)
        self.assertFalse(bad.is_valid())
        good = ShopSettingsForm({"currency": "IRR", "show_in_toman": "on", "low_stock_threshold": 3}, instance=cfg, site=self.site)
        self.assertTrue(good.is_valid(), good.errors)

    def test_panel_page_saves_and_audits(self):
        c = Client()
        c.force_login(self.admin)
        self.assertEqual(c.get(reverse("shop_panel:settings")).status_code, 200)
        r = c.post(reverse("shop_panel:settings"), {"currency": ShopSettings.for_site(self.site).currency, "low_stock_threshold": 7, "notify_customers": "on"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(ShopSettings.for_site(self.site).low_stock_threshold, 7)

    def test_panel_settings_requires_capability(self):
        c = Client()
        c.force_login(self.author)
        self.assertEqual(c.get(reverse("shop_panel:settings")).status_code, 403)
        self.assertEqual(Client().get(reverse("shop_panel:settings")).status_code, 302)


class PriceFormTests(ShopTestCase):
    def form(self, price, **cfg):
        ShopSettings.objects.update_or_create(site=self.site, defaults=cfg)
        data = {"name": "x", "slug": "x1", "price": price, "stock": 1, "track_stock": "on", "is_active": "on"}
        return ProductForm(data, site=self.site)

    def test_toman_entry_in_a_rial_shop_stores_rials(self):
        f = self.form("5000", currency="IRR", show_in_toman=True)
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data["price"], 50000)

    def test_usd_entry_stores_cents(self):
        f = self.form("19.99", currency="USD")
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data["price"], 1999)

    def test_persian_digits_and_separators(self):
        f = self.form("۱۲٬۰۰۰", currency="IRT")
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data["price"], 12000)

    def test_rejects_zero_negative_and_fraction(self):
        for bad in ("0", "-5", "1.5", "x"):
            self.assertFalse(self.form(bad, currency="IRT").is_valid(), bad)

    def test_edit_form_shows_price_in_display_unit(self):
        ShopSettings.objects.update_or_create(site=self.site, defaults={"currency": "IRR", "show_in_toman": True})
        p = Product.objects.create(site=self.site, name="r", slug="r", price=50000, stock=1)
        self.assertEqual(ProductForm(instance=p, site=self.site).initial["price"], Decimal(5000))


class RenderingTests(ShopTestCase):
    def test_storefront_shows_price_in_shop_currency(self):
        ShopSettings.objects.update_or_create(site=self.site, defaults={"currency": "IRR", "show_in_toman": True})
        self.product.price = 1500000
        self.product.save()
        page = Client().get("/en/shop/p/book/").content.decode()
        self.assertIn("150,000 Toman", page)  # 1,500,000 rials shown as toman
        self.assertNotIn("1,500,000", page)

    def test_order_keeps_its_currency(self):
        _, r = self.place_order()
        self.assertEqual(Order.objects.get().currency, ShopSettings.for_site(self.site).currency)

    def test_json_ld_uses_iso_code(self):
        page = Client().get("/en/shop/p/book/").content.decode()
        self.assertIn('"priceCurrency": "IRR"', page.replace('"priceCurrency":"IRR"', '"priceCurrency": "IRR"'))

    def test_api_exposes_currency(self):
        data = Client().get("/api/v1/shop/products/").json()
        row = (data.get("results") or data)[0]
        self.assertEqual(row["currency"], ShopSettings.for_site(self.site).currency)


class NotificationTests(ShopTestCase):
    @override_settings(SITE_URL="https://shop.example.test")
    def test_email_in_customer_language_not_in_staff_language(self):
        c = Client()
        c.post("/en/shop/cart/add/", {"product": self.product.pk, "qty": 1})
        c.post("/en/shop/checkout/", self.checkout_data())
        order = Order.objects.get()
        self.assertEqual(order.language, "en")
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True), translation.override("fa"):
            from apps.shop import services

            services.change_status(order, Order.Status.CANCELLED, actor=self.admin)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertIn(order.number, msg.subject)
        self.assertIn("cancelled", msg.body.lower())  # the order's English, though staff worked in Persian
        self.assertEqual(msg.to, ["a@b.com"])
        self.assertIn(order.token, msg.body)

    def test_creation_sends_email_and_runs_hook(self):
        with mock.patch("apps.plugins.hooks.run_action") as run, self.captureOnCommitCallbacks(execute=True):
            self.place_order()
        self.assertEqual(len(mail.outbox), 1)
        name = run.call_args.args[0]
        self.assertEqual(name, "shop.order_status_changed")
        kw = run.call_args.kwargs
        self.assertEqual((kw["old_status"], kw["new_status"], kw["phone"]), ("", "pending_payment", "09123456789"))
        self.assertTrue(kw["text"])

    def test_no_email_without_address_or_when_disabled(self):
        ShopSettings.objects.update_or_create(site=self.site, defaults={"notify_customers": False})
        with self.captureOnCommitCallbacks(execute=True):
            self.place_order()
        self.assertEqual(len(mail.outbox), 0)

    def test_mail_failure_never_breaks_the_order(self):
        with mock.patch("apps.shop.notify.build_message", side_effect=RuntimeError("smtp down")), self.captureOnCommitCallbacks(execute=True):
            _, r = self.place_order()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Order.objects.count(), 1)

    def test_hook_is_registered(self):
        from apps.plugins.hooks_registry import HOOKS

        self.assertEqual(HOOKS["shop.order_status_changed"], "action")

    def test_every_status_has_text_in_every_language(self):
        for lang in LANGS:
            with translation.override(lang):
                for status in Order.Status.values:
                    self.assertTrue(str(notify.STATUS_TEXT[status]).strip())
