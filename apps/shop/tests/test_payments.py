"""سیستم درگاه پرداخت: رابط استاندارد، ذخیرهٔ امن تنظیمات، اعتبارسنجی سمت سرور، idempotency، خطای شبکه/ناموفق/تکراری، پنل و API."""
import re
from datetime import timedelta
from unittest import mock

from cryptography.fernet import Fernet
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.shop import gateways, services
from apps.shop.models import GatewayConfig, Order, Payment, PaymentEvent
from apps.shop.payments import (ConfigField, CreateResult, GatewayNetworkError, GatewayRejected, PaymentGateway, Redirect, VerifyResult, vault)
from apps.sites.models import Site

from .helpers import ShopTestCase

SECRET = "SUPER-SECRET-API-KEY-12345"
KEY = Fernet.generate_key().decode()


class Script:
    """رفتار درگاه آزمایشی تست‌ها (قابل برنامه‌ریزی). هر مقدار می‌تواند تابع یا استثنا باشد."""

    create = None
    verify = None
    calls = []

    @classmethod
    def reset(cls):
        cls.create, cls.verify, cls.calls = None, None, []


def _run(step, *args):
    if isinstance(step, BaseException):
        raise step
    return step(*args) if callable(step) else step


class ScriptedGateway(PaymentGateway):
    name = "scripted"
    label = "درگاه برنامه‌پذیر تست"
    reports_currency = True
    redirect_hosts = ("pay.example.test",)
    config_fields = (ConfigField("api_key", "کلید", secret=True), ConfigField("merchant", "مرچنت", required=False), ConfigField("base", "آدرس", type="url", required=False))

    def create_transaction(self, ctx):
        Script.calls.append(("create", ctx.reference, ctx.amount, ctx.currency))
        if Script.create is not None:
            return _run(Script.create, ctx)
        return CreateResult(redirect=Redirect(url=f"https://pay.example.test/p/{ctx.reference[:8]}"), gateway_ref="GW" + ctx.reference[:10])

    def _verify(self, ctx, params):
        Script.calls.append(("verify", ctx.reference, dict(params or {})))
        if Script.verify is not None:
            return _run(Script.verify, ctx, params)
        return VerifyResult.paid(ctx.amount, currency=ctx.currency, gateway_ref=ctx.gateway_ref, order_ref=ctx.reference)

    def verify_transaction(self, ctx, params):
        return self._verify(ctx, params)

    def inquire_status(self, ctx):
        return self._verify(ctx, None)


@override_settings(SHOP_PAYMENT_GATEWAY="scripted", RAVIX_SECRETS_KEYS=[KEY])
class PayCase(ShopTestCase):
    def setUp(self):
        super().setUp()
        Script.reset()
        gateways.GATEWAYS["scripted"] = ScriptedGateway
        self.addCleanup(lambda: (gateways.GATEWAYS.pop("scripted", None), Script.reset()))
        self.cfg = GatewayConfig.objects.create(site=self.site, gateway="scripted", enabled=True, test_mode=False, public_config={"merchant": "M1"},
                                                secrets_encrypted=vault.encrypt_json({"api_key": SECRET}))

    def order_and_payment(self, qty=1):
        c = Client()
        self.add_to_cart(c, qty=qty)
        r = c.post("/shop/checkout/", self.checkout_data())
        self.assertEqual(r.status_code, 302, r.content[:200])
        return c, r, Order.objects.latest("id"), Payment.objects.latest("id")

    def callback(self, payment, c=None, qs=""):
        return (c or Client()).get(f"/shop/pay/callback/{payment.reference}/{qs}")


class InterfaceTests(PayCase):
    def test_base_interface_is_abstract(self):
        gw = PaymentGateway()
        for fn, args in ((gw.create_transaction, (None,)), (gw.verify_transaction, (None, {})), (gw.inquire_status, (None,))):
            with self.assertRaises(NotImplementedError):
                fn(*args)

    def test_repr_never_leaks_config(self):
        gw = ScriptedGateway({"api_key": SECRET})
        self.assertNotIn(SECRET, repr(gw))

    def test_core_has_no_provider_specific_code(self):
        import pathlib

        src = "".join(p.read_text(encoding="utf-8").lower() for p in pathlib.Path("apps/shop").rglob("*.py") if "tests" not in p.parts)
        for provider in ("zarinpal", "zibal", "idpay", "mellat", "saman", "parsian", "nextpay", "payping", "stripe", "paypal"):
            self.assertNotIn(provider, src)


class HappyPathTests(PayCase):
    def test_full_flow_validates_everything_server_side(self):
        c, r, order, payment = self.order_and_payment()
        self.assertTrue(r["Location"].startswith("https://pay.example.test/p/"))
        self.assertEqual((payment.gateway, payment.amount, payment.currency, payment.status), ("scripted", order.total, "IRT", "initiated"))
        self.callback(payment, c, "?status=OK&Authority=zzz")
        payment.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual((payment.status, order.status), ("succeeded", "paid"))
        self.assertEqual(payment.events.filter(kind="callback").count(), 1)
        # پارامترهای مرورگر به درگاه رسید ولی فقط به‌عنوان اشاره
        self.assertEqual([x for x in Script.calls if x[0] == "verify"][0][2], {"status": "OK", "Authority": "zzz"})

    def test_browser_params_never_decide(self):
        c, r, order, payment = self.order_and_payment()
        Script.verify = VerifyResult.pending()
        for qs in ("?status=OK", "?paid=1&amount=1", "?Status=OK&Authority=x"):
            self.callback(payment, c, qs)
        payment.refresh_from_db()
        self.assertEqual(payment.status, "initiated")
        self.assertEqual(Order.objects.get().status, "pending_payment")


class FailureTests(PayCase):
    def test_provider_reports_failed(self):
        c, r, order, payment = self.order_and_payment()
        Script.verify = VerifyResult.failed()
        self.callback(payment, c)
        payment.refresh_from_db()
        self.assertEqual((payment.status, Order.objects.get().status), ("failed", "pending_payment"))
        # مشتری می‌تواند دوباره پرداخت کند و تراکنش جدید ساخته می‌شود
        Script.verify = None
        resp = c.post(reverse("shop:order_pay", args=[order.number, order.token]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(order.payments.count(), 2)

    def test_network_error_on_create_cancels_checkout_order_and_keeps_stock(self):
        Script.create = GatewayNetworkError("timeout")
        c = Client()
        self.add_to_cart(c, qty=2)
        r = c.post("/shop/checkout/", self.checkout_data())
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Order.objects.get().status, "cancelled")
        self.assertEqual(self.product.__class__.objects.get(pk=self.product.pk).stock, 5)
        self.assertNotEqual(Payment.objects.get().status, "succeeded")
        self.assertTrue(Payment.objects.get().events.filter(kind="error").exists())

    def test_retry_after_network_error_reuses_same_transaction_id(self):
        from django.test import RequestFactory

        c = Client()
        self.add_to_cart(c)
        lines, _ = services.cart_lines(type("R", (), {"session": c.session})(), self.site)
        order = services.create_order(self.site, lines, {k: v for k, v in self.checkout_data().items()})
        req = RequestFactory().get("/")
        Script.create = GatewayNetworkError("timeout")
        with self.assertRaises(services.ShopError):
            services.start_payment(order, req)
        Script.create = None
        redirect = services.start_payment(order, req)
        self.assertTrue(redirect.url.startswith("https://pay.example.test/"))
        self.assertEqual(Payment.objects.count(), 1)  # همان کلید idempotency به ارائه‌دهنده رسید
        refs = [x[1] for x in Script.calls if x[0] == "create"]
        self.assertEqual(refs, [Payment.objects.get().reference] * 2)

    def test_provider_rejects_creation(self):
        Script.create = GatewayRejected("bad key")
        c = Client()
        self.add_to_cart(c)
        self.assertEqual(c.post("/shop/checkout/", self.checkout_data()).status_code, 400)
        self.assertEqual(Payment.objects.get().status, "failed")
        self.assertEqual(Order.objects.get().status, "cancelled")

    def test_network_error_on_verify_keeps_unknown_then_inquire_resolves(self):
        c, r, order, payment = self.order_and_payment()
        Script.verify = GatewayNetworkError("down")
        self.callback(payment, c)
        payment.refresh_from_db()
        self.assertEqual((payment.status, Order.objects.get().status), ("initiated", "pending_payment"))
        self.assertIsNotNone(payment.last_checked_at)
        Script.verify = None
        _p, _o, outcome = services.inquire_payment(payment)
        self.assertEqual(outcome, "applied")
        self.assertEqual(Order.objects.get().status, "paid")

    def test_plugin_bug_does_not_crash_or_mark_paid(self):
        c, r, order, payment = self.order_and_payment()
        Script.verify = RuntimeError("bug")
        self.assertEqual(self.callback(payment, c).status_code, 302)
        self.assertEqual(Payment.objects.get().status, "initiated")

    def test_unknown_provider_status_never_counts_as_paid(self):
        c, r, order, payment = self.order_and_payment()
        Script.verify = VerifyResult("weird")
        self.callback(payment, c)
        self.assertEqual(Payment.objects.get().status, "initiated")
        self.assertEqual(Order.objects.get().status, "pending_payment")


class ValidationTests(PayCase):
    def _mismatch(self, result, expected_payment_status="needs_review"):
        c, r, order, payment = self.order_and_payment()
        Script.verify = result
        self.callback(payment, c)
        payment.refresh_from_db()
        self.assertEqual(payment.status, expected_payment_status)
        self.assertEqual(Order.objects.get().status, "pending_payment")
        return payment

    def test_amount_mismatch(self):
        self._mismatch(VerifyResult.paid(1, currency="IRT"))

    def test_currency_mismatch(self):
        self._mismatch(lambda ctx, p: VerifyResult.paid(ctx.amount, currency="IRR"))

    def test_missing_currency_when_gateway_reports_currency(self):
        self._mismatch(lambda ctx, p: VerifyResult.paid(ctx.amount))

    def test_order_reference_mismatch(self):
        self._mismatch(lambda ctx, p: VerifyResult.paid(ctx.amount, currency=ctx.currency, order_ref="someone-elses-order"))

    def test_provider_transaction_id_mismatch(self):
        self._mismatch(lambda ctx, p: VerifyResult.paid(ctx.amount, currency=ctx.currency, gateway_ref="OTHER"))

    def test_order_total_changed_after_payment_start(self):
        c, r, order, payment = self.order_and_payment()
        Order.objects.filter(pk=order.pk).update(total=order.total + 5)
        self.callback(payment, c)
        self.assertEqual(Payment.objects.get().status, "needs_review")

    def test_mismatch_message_is_logged_for_admin(self):
        p = self._mismatch(VerifyResult.paid(1, currency="IRT"))
        self.assertTrue(p.events.filter(message__contains="مغایرت").exists())

    def test_unsupported_currency_rejected_before_calling_provider(self):
        Order.objects.all().delete()
        c = Client()
        self.add_to_cart(c)
        with mock.patch.object(ScriptedGateway, "supported_currencies", ("USD",)):
            self.assertEqual(c.post("/shop/checkout/", self.checkout_data()).status_code, 400)
        self.assertEqual([x for x in Script.calls if x[0] == "create"], [])

    def test_redirect_to_unlisted_host_is_refused(self):
        for url in ("https://evil.example.com/x", "http://pay.example.test/x", "//evil.example.com/x", "javascript:alert(1)"):
            Payment.objects.all().delete()
            Order.objects.all().delete()
            Script.create = CreateResult(redirect=Redirect(url=url), gateway_ref="G" + str(abs(hash(url)))[:8])
            c = Client()
            self.add_to_cart(c)
            self.assertEqual(c.post("/shop/checkout/", self.checkout_data()).status_code, 400, url)
            self.assertEqual(Payment.objects.get().status, "failed", url)

    def test_post_redirect_renders_form_and_opens_csp_only_for_gateway_host(self):
        Script.create = CreateResult(redirect=Redirect(url="https://pay.example.test/go", method="POST", fields={"token": "T1", "x": '"><script>'}), gateway_ref="GPOST1")
        c = Client()
        self.add_to_cart(c)
        r = c.post("/shop/checkout/", self.checkout_data())
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('action="https://pay.example.test/go"', html)
        self.assertIn('name="token" value="T1"', html)
        self.assertNotIn('"><script>', html)  # خروجی escape می‌شود
        self.assertIn("form-action 'self' https://pay.example.test", r["Content-Security-Policy"])
        self.assertIn("no-store", r["Cache-Control"])


class IdempotencyTests(PayCase):
    def test_duplicate_callbacks_record_one_success(self):
        c, r, order, payment = self.order_and_payment()
        for _ in range(3):
            self.callback(payment, c)
        self.assertEqual(Payment.objects.filter(status="succeeded").count(), 1)
        self.assertEqual(len([x for x in Script.calls if x[0] == "verify"]), 1)  # بعد از نهایی‌شدن دوباره از درگاه نمی‌پرسد
        self.assertEqual(payment.events.filter(kind="duplicate").count(), 2)
        o = Order.objects.get()
        self.assertEqual(o.events.filter(to_status="paid").count(), 1)

    def test_double_click_pay_creates_single_transaction(self):
        c = Client()
        self.add_to_cart(c)
        c.post("/shop/checkout/", self.checkout_data())
        order = Order.objects.get()
        url = reverse("shop:order_pay", args=[order.number, order.token])
        for _ in range(3):
            c.post(url)
        self.assertEqual(order.payments.count(), 1)
        self.assertEqual(order.payments.get().events.filter(kind="duplicate").count(), 3)

    def test_second_success_for_same_order_goes_to_review(self):
        c, r, order, p1 = self.order_and_payment()
        Payment.objects.filter(pk=p1.pk).update(created_at=timezone.now() - timedelta(hours=1))  # خارج از بازهٔ استفادهٔ مجدد
        c.post(reverse("shop:order_pay", args=[order.number, order.token]))
        p2 = Payment.objects.exclude(pk=p1.pk).get()
        self.callback(p1, c)
        self.callback(p2, c)
        p1.refresh_from_db()
        p2.refresh_from_db()
        self.assertEqual((p1.status, p2.status), ("succeeded", "needs_review"))
        self.assertEqual(Order.objects.get().events.filter(to_status="paid").count(), 1)

    def test_db_constraints_block_double_success(self):
        c, r, order, p1 = self.order_and_payment()
        self.callback(p1, c)
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            Payment.objects.create(order=order, gateway="scripted", amount=order.total, status="succeeded")

    def test_same_provider_transaction_id_cannot_credit_two_payments(self):
        c, r, order, p1 = self.order_and_payment()
        self.callback(p1, c)
        p1.refresh_from_db()
        o2 = Order.objects.create(site=self.site, customer_name="x", phone="09123456789", total=10, subtotal=10)
        p2 = Payment.objects.create(order=o2, gateway="scripted", amount=10)
        Script.verify = lambda ctx, p: VerifyResult.paid(ctx.amount, currency="IRT", gateway_ref=p1.gateway_ref)
        services.finalize_payment(p2.reference)
        p2.refresh_from_db()
        self.assertEqual(p2.status, "needs_review")
        self.assertEqual(Order.objects.get(pk=o2.pk).status, "pending_payment")


class LoggingAndSecretsTests(PayCase):
    def test_events_have_no_secrets_even_if_provider_echoes_them(self):
        c, r, order, payment = self.order_and_payment()
        Script.verify = lambda ctx, p: VerifyResult.paid(ctx.amount, currency="IRT", gateway_ref=ctx.gateway_ref,
                                                         raw={"api_key": SECRET, "note": f"echo {SECRET}", "card_number": "6037991234567890", "nested": {"token": "abc", "ok": 1}})
        self.callback(payment, c, f"?token={SECRET}&Authority=A1")
        blob = repr(list(PaymentEvent.objects.values()))
        self.assertNotIn(SECRET, blob)
        self.assertNotIn("6037991234567890", blob)
        ev = payment.events.filter(kind="verify").get()
        self.assertEqual(ev.data["raw"]["api_key"], "***")
        self.assertEqual(ev.data["raw"]["nested"], {"token": "***", "ok": 1})
        self.assertTrue(ev.created_at)

    def test_error_messages_scrubbed(self):
        c, r, order, payment = self.order_and_payment()
        Script.verify = GatewayNetworkError(f"failed with key {SECRET}")
        self.callback(payment, c)
        self.assertNotIn(SECRET, repr(list(PaymentEvent.objects.values())))

    def test_redact_function(self):
        out = vault.redact({"Authorization": "Bearer x", "a": "b" * 500, "l": list(range(100)), "deep": {"a": {"b": {"c": {"d": 1}}}}, "t": f"x{SECRET}y"}, [SECRET])
        self.assertEqual(out["Authorization"], "***")
        self.assertEqual(len(out["a"]), 200)
        self.assertEqual(len(out["l"]), 30)
        self.assertEqual(out["t"], "x***y")

    def test_secrets_encrypted_at_rest(self):
        raw = GatewayConfig.objects.get().secrets_encrypted
        self.assertNotIn(SECRET, raw)
        self.assertNotIn(SECRET, repr(GatewayConfig.objects.values()))
        self.assertEqual(vault.decrypt_json(raw), {"api_key": SECRET})

    def test_key_rotation_and_missing_key(self):
        new = Fernet.generate_key().decode()
        with override_settings(RAVIX_SECRETS_KEYS=[new, KEY]):
            self.assertEqual(vault.decrypt_json(self.cfg.secrets_encrypted), {"api_key": SECRET})
        with override_settings(RAVIX_SECRETS_KEYS=[new]), self.assertRaises(vault.SecretsError):
            vault.decrypt_json(self.cfg.secrets_encrypted)
        with override_settings(RAVIX_SECRETS_KEYS=[], DEBUG=False, RAVIX_SECRETS_ALLOW_DERIVED_KEY=False), self.assertRaises(vault.SecretsError):
            vault.encrypt_json({"a": "b"})

    def test_unreadable_secrets_make_gateway_unavailable_not_crash(self):
        with override_settings(RAVIX_SECRETS_KEYS=[Fernet.generate_key().decode()]):
            c = Client()
            self.add_to_cart(c)
            r = c.post("/shop/checkout/", self.checkout_data())
            self.assertEqual(r.status_code, 400)
            self.assertNotIn(SECRET, r.content.decode())

    def test_secrets_not_in_pages_or_api(self):
        c, r, order, payment = self.order_and_payment()
        self.callback(payment, c)
        pages = [c.get("/shop/checkout/").content, c.get(order.get_public_url()).content, c.get("/api/v1/shop/gateways/").content, c.get("/api/v1/shop/products/").content]
        admin = Client()
        admin.force_login(self.admin)
        pages += [admin.get(reverse("shop_panel:gateways")).content, admin.get(reverse("shop_panel:gateway_edit", args=["scripted"])).content,
                  admin.get(reverse("shop_panel:order", args=[order.pk])).content, admin.get(f"/api/v1/shop/orders/{order.number}/").content,
                  admin.get("/api/v1/shop/orders/").content]
        for body in pages:
            self.assertNotIn(SECRET.encode(), body)
        detail = admin.get(f"/api/v1/shop/orders/{order.number}/").json()
        for pay in detail["payments"]:
            self.assertNotIn("gateway_ref", pay)

    def test_env_override_wins_and_is_not_shown(self):
        with mock.patch.dict("os.environ", {"RAVIX_GATEWAY_SCRIPTED_API_KEY": "ENV-KEY-VALUE-999"}):
            gw = gateways.get_gateway("scripted", site=self.site)
            self.assertEqual(gw.config["api_key"], "ENV-KEY-VALUE-999")
            admin = Client()
            admin.force_login(self.admin)
            self.assertNotIn(b"ENV-KEY-VALUE-999", admin.get(reverse("shop_panel:gateway_edit", args=["scripted"])).content)


class PanelTests(PayCase):
    def setUp(self):
        super().setUp()
        self.c = Client()
        self.c.force_login(self.admin)

    def test_permissions(self):
        urls = [reverse("shop_panel:gateways"), reverse("shop_panel:gateway_edit", args=["scripted"])]
        for u in urls:
            self.assertEqual(Client().get(u).status_code, 302)
        for user in (self.author, make_user("plain", Role.USER)):
            c = Client()
            c.force_login(user)
            for u in urls:
                self.assertEqual(c.get(u).status_code, 403)
            self.assertEqual(c.post(reverse("shop_panel:gateway_toggle", args=["scripted"]), {"enable": "0"}).status_code, 403)
        self.assertTrue(GatewayConfig.objects.get().enabled)

    def test_toggle_disable_enable_and_checkout_follows(self):
        self.c.post(reverse("shop_panel:gateway_toggle", args=["scripted"]), {"enable": "0"})
        self.assertFalse(GatewayConfig.objects.get().enabled)
        cl = Client()
        self.add_to_cart(cl)
        self.assertEqual(cl.post("/shop/checkout/", self.checkout_data()).status_code, 400)
        self.assertFalse(Order.objects.exists())
        self.c.post(reverse("shop_panel:gateway_toggle", args=["scripted"]), {"enable": "1"})
        self.assertEqual(cl.post("/shop/checkout/", self.checkout_data()).status_code, 302)

    def test_disabling_gateway_still_verifies_open_payments(self):
        cl, r, order, payment = self.order_and_payment()
        self.c.post(reverse("shop_panel:gateway_toggle", args=["scripted"]), {"enable": "0"})
        self.callback(payment, cl)
        self.assertEqual(Order.objects.get().status, "paid")

    def test_toggle_requires_post_and_csrf(self):
        self.assertEqual(self.c.get(reverse("shop_panel:gateway_toggle", args=["scripted"])).status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.admin)
        self.assertEqual(strict.post(reverse("shop_panel:gateway_toggle", args=["scripted"]), {"enable": "0"}).status_code, 403)

    def test_cannot_enable_incomplete_gateway(self):
        GatewayConfig.objects.all().delete()
        self.c.post(reverse("shop_panel:gateway_toggle", args=["scripted"]), {"enable": "1"})
        self.assertFalse(GatewayConfig.objects.filter(enabled=True).exists())

    def test_edit_saves_encrypted_keeps_blank_and_clears(self):
        url = reverse("shop_panel:gateway_edit", args=["scripted"])
        page = self.c.get(url).content.decode()
        self.assertNotIn(SECRET, page)
        self.assertIn("مقدار فعلی ذخیره شده است", page)
        data = {"enabled": "on", "cfg_api_key": "", "cfg_merchant": "M2", "cfg_base": ""}
        self.assertEqual(self.c.post(url, data).status_code, 302)
        row = GatewayConfig.objects.get()
        self.assertEqual(row.public_config["merchant"], "M2")
        self.assertEqual(vault.decrypt_json(row.secrets_encrypted)["api_key"], SECRET)  # خالی = بدون تغییر
        NEW = "BRAND-NEW-KEY-777"
        self.c.post(url, {**data, "cfg_api_key": NEW})
        row.refresh_from_db()
        self.assertNotIn(NEW, row.secrets_encrypted)
        self.assertEqual(vault.decrypt_json(row.secrets_encrypted)["api_key"], NEW)
        self.assertFalse(row.test_mode)
        r = self.c.post(url, {**data, "clear_api_key": "on", "enabled": ""})
        self.assertEqual(r.status_code, 302)
        row.refresh_from_db()
        self.assertEqual(vault.decrypt_json(row.secrets_encrypted), {})
        # فعال‌کردن بدون کلید الزامی ممکن نیست
        self.assertEqual(self.c.post(url, data).status_code, 400)

    def test_edit_validates_url_and_audit_has_no_secret(self):
        from apps.audit.models import AuditLog

        url = reverse("shop_panel:gateway_edit", args=["scripted"])
        self.assertEqual(self.c.post(url, {"cfg_base": "http://insecure.example", "cfg_api_key": ""}).status_code, 400)
        self.c.post(url, {"enabled": "on", "cfg_api_key": "AUDIT-SECRET-000"})
        self.assertFalse(AuditLog.objects.filter(message__contains="AUDIT-SECRET-000").exists())
        self.assertTrue(AuditLog.objects.filter(object_type="درگاه پرداخت").exists())

    def test_secret_field_is_password_input_and_never_prefilled(self):
        page = self.c.get(reverse("shop_panel:gateway_edit", args=["scripted"])).content.decode()
        self.assertRegex(page, r'<input type="password" name="cfg_api_key"[^>]*>')
        self.assertNotRegex(page, r'name="cfg_api_key"[^>]*value=')

    def test_sites_are_isolated(self):
        other = Site.objects.create(name="B", slug="b")
        GatewayConfig.objects.create(site=other, gateway="scripted", enabled=False)
        self.assertTrue(GatewayConfig.objects.get(site=self.site).enabled)
        self.assertFalse(gateways.is_enabled(other, "scripted"))

    def test_manual_inquire_button(self):
        cl, r, order, payment = self.order_and_payment()
        page = self.c.get(reverse("shop_panel:order", args=[order.pk])).content.decode()
        self.assertIn("استعلام وضعیت از درگاه", page)
        self.c.post(reverse("shop_panel:payment_inquire", args=[order.pk, payment.pk]))
        self.assertEqual(Order.objects.get().status, "paid")
        other = Site.objects.create(name="B", slug="b2")
        o2 = Order.objects.create(site=other, customer_name="x", phone="09123456789", total=10, subtotal=10)
        p2 = Payment.objects.create(order=o2, gateway="scripted", amount=10)
        self.assertEqual(self.c.post(reverse("shop_panel:payment_inquire", args=[o2.pk, p2.pk])).status_code, 404)

    def test_user_can_choose_among_multiple_gateways(self):
        class Other(ScriptedGateway):
            label = "دومی"

        gateways.GATEWAYS["scripted2"] = Other
        self.addCleanup(gateways.GATEWAYS.pop, "scripted2", None)
        GatewayConfig.objects.create(site=self.site, gateway="scripted2", enabled=True, public_config={}, secrets_encrypted=vault.encrypt_json({"api_key": "k2k2k2"}))
        cl = Client()
        self.add_to_cart(cl)
        html = cl.get("/shop/checkout/").content.decode()
        self.assertIn('name="gateway"', html)
        self.assertEqual(cl.post("/shop/checkout/", self.checkout_data(gateway="evil_gateway")).status_code, 400)  # مقدار آزاد پذیرفته نمی‌شود
        r = cl.post("/shop/checkout/", self.checkout_data(gateway="scripted2"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Payment.objects.get().gateway, "scripted2")
        names = [g["name"] for g in Client().get("/api/v1/shop/gateways/").json()]
        self.assertEqual(sorted(names), ["scripted", "scripted2"])


class ExpiryAndInquiryTests(PayCase):
    def _old(self, order):
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=2))

    def test_expire_asks_provider_first_and_does_not_cancel_paid_order(self):
        cl, r, order, payment = self.order_and_payment(qty=2)
        self._old(order)
        self.assertEqual(services.expire_pending(), 0)  # کاربر پرداخت کرده بود ولی callback گم شد
        self.assertEqual(Order.objects.get().status, "paid")

    def test_expire_cancels_when_provider_says_not_paid(self):
        cl, r, order, payment = self.order_and_payment(qty=2)
        Script.verify = VerifyResult.pending()
        self._old(order)
        self.assertEqual(services.expire_pending(), 1)
        self.assertEqual(Order.objects.get().status, "cancelled")
        self.assertEqual(self.product.__class__.objects.get(pk=self.product.pk).stock, 5)

    def test_expire_defers_when_provider_unreachable_then_cancels_after_hard_limit(self):
        cl, r, order, payment = self.order_and_payment()
        Script.verify = GatewayNetworkError("down")
        self._old(order)
        self.assertEqual(services.expire_pending(), 0)
        self.assertEqual(Order.objects.get().status, "pending_payment")
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(days=3))
        self.assertEqual(services.expire_pending(), 1)

    def test_late_payment_after_cancel_needs_review(self):
        cl, r, order, payment = self.order_and_payment()
        services.change_status(order, "cancelled")
        self.callback(payment, cl)
        self.assertEqual(Payment.objects.get().status, "needs_review")
        self.assertEqual(Order.objects.get().status, "cancelled")

    def test_stale_initiated_payments_are_closed(self):
        cl, r, order, payment = self.order_and_payment()
        Script.verify = VerifyResult.pending()
        services.change_status(order, "cancelled")
        Payment.objects.filter(pk=payment.pk).update(created_at=timezone.now() - timedelta(hours=30))
        self.assertEqual(services.expire_stale_payments(), 1)
        self.assertEqual(Payment.objects.get().status, "failed")


class HttpClientTests(PayCase):
    def test_https_only_and_error_mapping(self):
        from apps.shop.payments import http

        with self.assertRaises(gateways.GatewayError):
            http.request_json("GET", "http://insecure.example/x")
        with mock.patch.object(http, "_send", return_value=(503, b"")), self.assertRaises(GatewayNetworkError):
            http.request_json("GET", "https://api.example.test/x")
        with mock.patch.object(http, "_send", return_value=(429, b"")), self.assertRaises(GatewayNetworkError):
            http.request_json("GET", "https://api.example.test/x")
        with mock.patch.object(http, "_send", return_value=(401, b'{"secret":"x"}')), self.assertRaises(GatewayRejected) as cm:
            http.request_json("GET", "https://api.example.test/x")
        self.assertNotIn("secret", str(cm.exception))
        with mock.patch.object(http, "_send", return_value=(200, b"<html>")), self.assertRaises(gateways.GatewayError):
            http.request_json("GET", "https://api.example.test/x")
        with mock.patch.object(http, "_send", return_value=(200, b"[1]")), self.assertRaises(gateways.GatewayError):
            http.request_json("GET", "https://api.example.test/x")
        with mock.patch.object(http, "_send", return_value=(200, b'{"a":1}')):
            self.assertEqual(http.request_json("GET", "https://api.example.test/x"), {"a": 1})

    def test_private_hosts_blocked(self):
        from apps.shop.payments import http

        with mock.patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 0))]), self.assertRaises(gateways.GatewayError):
            http._check_host("internal.example")
        with mock.patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]):
            http._check_host("public.example")

    def test_real_transport_maps_connection_errors(self):
        from apps.shop.payments import http

        with mock.patch.object(http, "_check_host"), mock.patch("urllib.request.OpenerDirector.open", side_effect=ConnectionRefusedError()):
            with self.assertRaises(GatewayNetworkError):
                http._send("GET", "https://api.example.test/x", None, {}, 1)
        with mock.patch.object(http, "_check_host"), mock.patch("urllib.request.OpenerDirector.open", side_effect=TimeoutError()):
            with self.assertRaises(GatewayNetworkError):
                http._send("GET", "https://api.example.test/x", None, {}, 1)


class SandboxGatewayTests(ShopTestCase):
    def test_sandbox_end_to_end_with_new_interface(self):
        c, r = self.place_order()
        ref, page = self.pay(c, r)
        p = Payment.objects.get()
        self.assertEqual((p.status, p.is_test, p.currency), ("succeeded", True, "IRT"))
        self.assertEqual(Order.objects.get().currency, "IRT")

    def test_sandbox_failed_payment(self):
        c, r = self.place_order()
        self.pay(c, r, action="fail")
        self.assertEqual(Payment.objects.get().status, "failed")
        self.assertTrue(Payment.objects.get().events.filter(kind="verify", to_status="failed").exists())

    def test_sandbox_can_be_disabled_in_panel(self):
        GatewayConfig.objects.create(site=self.site, gateway="sandbox", enabled=False)
        c = Client()
        self.add_to_cart(c)
        self.assertEqual(c.post("/shop/checkout/", self.checkout_data()).status_code, 400)


class PreflightTests(PayCase):
    def run_pf(self, **settings_over):
        import io

        from django.core.management import call_command

        out = io.StringIO()
        with override_settings(**settings_over):
            try:
                call_command("ravix_preflight", stdout=out)
            except SystemExit:
                pass
        return out.getvalue()

    def test_enabled_gateway_without_encryption_key_is_an_error(self):
        out = self.run_pf(RAVIX_SECRETS_KEYS=[])
        self.assertIn("RAVIX_SECRETS_KEY تنظیم نشده", out)
        self.assertNotIn(SECRET, out)

    def test_test_mode_warning_and_ok(self):
        GatewayConfig.objects.update(test_mode=True)
        out = self.run_pf()
        self.assertIn("درگاه در حالت آزمایشی فعال است: scripted", out)
        self.assertIn("درگاه(های) پرداخت فعال: scripted", out)

    def test_sandbox_enabled_in_production_is_an_error(self):
        GatewayConfig.objects.create(site=self.site, gateway="sandbox", enabled=True)
        out = self.run_pf(DEBUG=False)
        self.assertIn("درگاه آزمایشی در پنل فعال است", out)
