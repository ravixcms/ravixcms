"""افزونهٔ نمونهٔ درگاه (plugins/example_pay): بارگذاری فقط پس از فعال‌سازی، چرخهٔ کامل پرداخت با یک سرویس‌دهندهٔ ساختگی (بدون شبکهٔ واقعی)."""
import json
import shutil
import unittest
from unittest import mock

from cryptography.fernet import Fernet
from django.conf import settings
from django.test import Client, override_settings
from django.test.client import RequestFactory

from apps.plugins import hooks
from apps.plugins.tests.helpers import REPO_PLUGINS, PluginTestCase

if not settings.SHOP_ENABLED:
    raise unittest.SkipTest("فروشگاه غیرفعال است")

from apps.shop import gateways, services  # noqa: E402
from apps.shop.models import GatewayConfig, Order, Payment, Product  # noqa: E402
from apps.shop.payments import GatewayNetworkError, TransactionContext, http, vault  # noqa: E402
from apps.sites.models import Site  # noqa: E402

KEY = Fernet.generate_key().decode()
API_KEY = "EP-LIVE-KEY-9876543210"


class FakeProvider:
    """سرویس‌دهندهٔ ساختگی ExamplePay (همان قرارداد مستند در gateway.py) با idempotency بر اساس هدر."""

    def __init__(self):
        self.payments, self.by_key, self.requests, self.fail_next = {}, {}, [], None

    def send(self, method, url, body, headers, timeout):
        self.requests.append((method, url, dict(headers), json.loads(body) if body else None))
        if self.fail_next:
            err, self.fail_next = self.fail_next, None
            if isinstance(err, Exception):
                raise err
            return err, b""
        if headers.get("Authorization") != "Bearer " + API_KEY:
            return 401, b"{}"
        path = url.split("//", 1)[1].split("/", 1)[1]
        if method == "POST" and path == "v1/payments":
            data = json.loads(body)
            key = headers["Idempotency-Key"]
            if key not in self.by_key:
                pid = f"P{len(self.payments) + 1}"
                self.payments[pid] = {"id": pid, "status": "pending", "amount": data["amount"], "currency": data["currency"], "order_id": data["order_id"]}
                self.by_key[key] = pid
            pid = self.by_key[key]
            return 201, json.dumps({"id": pid, "pay_token": "tok" + pid}).encode()
        if path.startswith("v1/payments?order_id="):
            oid = path.split("=", 1)[1]
            return 200, json.dumps({"items": [p for p in self.payments.values() if p["order_id"] == oid]}).encode()
        pid = path.split("/")[2]
        if pid not in self.payments:
            return 404, b"{}"
        return 200, json.dumps(self.payments[pid]).encode()


@override_settings(RAVIX_SECRETS_KEYS=[KEY], SHOP_PAYMENT_GATEWAY="example_pay")
class ExamplePayPluginTests(PluginTestCase):
    def setUp(self):
        super().setUp()
        shutil.copytree(REPO_PLUGINS / "example_pay", self.tmp / "example_pay", ignore=shutil.ignore_patterns("__pycache__"))
        self.site, _ = Site.objects.get_or_create(is_default=True, defaults={"name": "Main", "slug": "main"})
        self.provider = FakeProvider()
        patcher = mock.patch.object(http, "_send", self.provider.send)
        patcher.start()
        self.addCleanup(patcher.stop)

    def configure(self, **over):
        cfg = {"api_base": "https://api.examplepay.invalid", "sandbox_api_base": "https://sandbox.examplepay.invalid", "pay_host": "pay.examplepay.invalid"}
        cfg.update(over)
        return GatewayConfig.objects.update_or_create(site=self.site, gateway="example_pay", defaults={
            "enabled": True, "test_mode": False, "public_config": cfg, "secrets_encrypted": vault.encrypt_json({"api_key": API_KEY})})[0]

    def new_order(self, price=25000):
        p = Product.objects.create(site=self.site, name="کالا", slug=f"k{Product.objects.count()}", price=price, stock=3)
        order = services.create_order(self.site, [{"product": p, "qty": 1}], {"customer_name": "علی رضایی", "phone": "09123456789", "email": "", "address": "تهران، خیابان آزادی ۱", "note": ""})
        return order

    def start(self, order):
        return services.start_payment(order, RequestFactory().get("/fa/"))

    def test_gateway_available_only_while_plugin_active(self):
        self.assertNotIn("example_pay", gateways.available_gateways())
        self.install_activate("example_pay")
        self.assertIn("example_pay", gateways.available_gateways())
        from apps.plugins import services as plugin_services

        plugin_services.deactivate("example_pay", self.admin)
        self.invalidate()
        self.assertNotIn("example_pay", gateways.available_gateways())

    def test_plugin_gateway_does_not_replace_builtin(self):
        self.install_activate("example_pay")
        self.assertIs(gateways.available_gateways()["sandbox"], gateways.SandboxGateway)

    def test_full_paid_flow_through_core(self):
        self.install_activate("example_pay")
        self.configure()
        order = self.new_order()
        redirect = self.start(order)
        self.assertEqual(redirect.url, "https://pay.examplepay.invalid/pay/tokP1")
        create = self.provider.requests[0]
        self.assertEqual(create[3]["order_id"], Payment.objects.get().reference)
        self.assertEqual((create[3]["amount"], create[3]["currency"]), (25000, "IRT"))
        self.assertEqual(create[2]["Idempotency-Key"], Payment.objects.get().reference)
        self.assertTrue(create[3]["callback_url"].endswith(f"/shop/pay/callback/{Payment.objects.get().reference}/"))
        # مشتری بازمی‌گردد ولی بانک هنوز پرداختی ثبت نکرده → پرداخت نمی‌شود، حتی با پارامتر ساختگی
        ref = Payment.objects.get().reference
        Client().get(f"/fa/shop/pay/callback/{ref}/?status=paid&id=P999")
        self.assertEqual(Order.objects.get().status, "pending_payment")
        self.assertFalse(any("P999" in r[1] for r in self.provider.requests))  # id مرورگر استفاده نشد
        self.provider.payments["P1"]["status"] = "paid"
        Client().get(f"/fa/shop/pay/callback/{ref}/")
        self.assertEqual((Order.objects.get().status, Payment.objects.get().status), ("paid", "succeeded"))

    def test_amount_or_currency_tampering_goes_to_review(self):
        self.install_activate("example_pay")
        self.configure()
        order = self.new_order()
        self.start(order)
        self.provider.payments["P1"].update(status="paid", amount=1)
        services.finalize_payment(Payment.objects.get().reference)
        self.assertEqual(Payment.objects.get().status, "needs_review")
        self.assertEqual(Order.objects.get().status, "pending_payment")

    def test_failed_and_unknown_status(self):
        self.install_activate("example_pay")
        self.configure()
        self.start(self.new_order())
        self.provider.payments["P1"]["status"] = "mystery"
        services.finalize_payment(Payment.objects.get().reference)
        self.assertEqual(Payment.objects.get().status, "initiated")
        self.provider.payments["P1"]["status"] = "cancelled"
        services.finalize_payment(Payment.objects.get().reference)
        self.assertEqual(Payment.objects.get().status, "failed")

    def test_network_errors_and_provider_rejection(self):
        self.install_activate("example_pay")
        self.configure()
        order = self.new_order()
        self.provider.fail_next = GatewayNetworkError("x")
        with self.assertRaises(services.ShopError):
            self.start(order)
        self.provider.fail_next = 503
        with self.assertRaises(services.ShopError):
            self.start(order)
        self.assertEqual(Payment.objects.get().status, "initiated")  # هنوز وضعیت ایجاد نامعلوم است
        self.start(order)  # تلاش سوم موفق؛ همان تراکنش، همان کلید
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(len(self.provider.payments), 1)
        self.provider.fail_next = 503
        _p, _o, outcome = services.inquire_payment(Payment.objects.get())
        self.assertEqual(outcome, "unknown")

    def test_wrong_api_key_is_rejected_without_leaking_it(self):
        self.install_activate("example_pay")
        row = self.configure()
        row.secrets_encrypted = vault.encrypt_json({"api_key": "WRONG-KEY-VALUE-1"})
        row.save()
        with self.assertRaises(services.ShopError) as cm:
            self.start(self.new_order())
        self.assertNotIn("WRONG-KEY-VALUE-1", str(cm.exception))
        self.assertNotIn("WRONG-KEY-VALUE-1", repr(list(Payment.objects.get().events.values())))

    def test_test_mode_uses_sandbox_base(self):
        self.install_activate("example_pay")
        row = self.configure()
        row.test_mode = True
        row.save()
        self.start(self.new_order())
        self.assertTrue(self.provider.requests[0][1].startswith("https://sandbox.examplepay.invalid/"))
        self.assertTrue(Payment.objects.get().is_test)

    def test_inquire_recovers_transaction_whose_creation_response_was_lost(self):
        self.install_activate("example_pay")
        self.configure()
        order = self.new_order()
        self.start(order)
        Payment.objects.update(gateway_ref="")  # پاسخ ایجاد گم شد؛ شناسهٔ ارائه‌دهنده نزد ما نیست
        self.provider.payments["P1"]["status"] = "paid"
        services.inquire_payment(Payment.objects.get())
        self.assertEqual(Order.objects.get().status, "paid")

    def test_redirect_host_taken_from_config_only(self):
        self.install_activate("example_pay")
        self.configure(pay_host="")
        with self.assertRaises(services.ShopError):  # پیکربندی ناقص ← درگاه در دسترس نیست، تراکنشی ساخته نمی‌شود
            self.start(self.new_order())
        self.assertFalse(Payment.objects.exists())
        self.configure(pay_host="evil.example.com")  # میزبان دیگری که پاسخ درگاه هدایت می‌کند
        order = Order.objects.get()
        self.provider.by_key.clear()
        self.assertTrue(self.start(order).url.startswith("https://evil.example.com/pay/tok"))  # میزبان را مدیر تعیین کرده؛ هسته فقط همان را می‌پذیرد

    def test_invalid_pay_token_chars_are_quoted(self):
        gw = gateways.available_gateways()
        self.install_activate("example_pay")
        cls = gateways.available_gateways()["example_pay"]
        inst = cls({"api_base": "https://a.invalid", "pay_host": "pay.x.invalid", "api_key": API_KEY})
        self.provider.requests.clear()
        with mock.patch.object(http, "_send", return_value=(201, json.dumps({"id": "P1", "pay_token": "a/b?c"}).encode())):
            res = inst.create_transaction(TransactionContext("r1", "RV-1", 10, "IRT", "d", "https://s/cb/", test_mode=False))
        self.assertEqual(res.redirect.url, "https://pay.x.invalid/pay/a%2Fb%3Fc")
