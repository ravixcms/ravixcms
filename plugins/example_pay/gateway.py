"""
درگاه نمونه «ExamplePay» — قرارداد API فرضی:

    POST   {api_base}/v1/payments            {order_id, amount, currency, callback_url, description}   هدر: Authorization: Bearer <api_key>، Idempotency-Key: <order_id>
           ← {"id": "...", "pay_token": "..."}   (مشتری باید به https://<pay_host>/pay/<pay_token> برود)
    POST   {api_base}/v1/payments/{id}/verify ← {"status": "paid|failed|pending", "amount": 1000, "currency": "IRT", "order_id": "..."}
    GET    {api_base}/v1/payments/{id}        ← همان ساختار (استعلام)
    GET    {api_base}/v1/payments?order_id=.. ← {"items": [ ... ]}  (بازیابی تراکنشی که پاسخ ایجادش گم شد)

هیچ‌یک از این آدرس‌ها واقعی نیست. برای درگاه واقعی: همین ساختار را نگه دارید و فقط فراخوانی‌ها/نگاشت‌ها را طبق مستندات رسمی ارائه‌دهنده بنویسید.
"""
from django.utils.translation import gettext, gettext_lazy
from urllib.parse import quote

from apps.shop.payments import (ConfigField, CreateResult, GatewayError, PaymentGateway, Redirect, VerifyResult, VerifyStatus)
from apps.shop.payments.http import request_json

PAID = {"paid", "succeeded"}
FAILED = {"failed", "cancelled", "canceled", "expired"}
PENDING = {"pending", "created", "processing"}


class ExamplePay(PaymentGateway):
    name = "example_pay"
    label = gettext_lazy("ExamplePay (sample)")
    description = gettext_lazy("A tutorial sample gateway; it is not connected to a real service.")
    supported_currencies = ("IRT", "IRR")
    reports_currency = True
    config_fields = (
        ConfigField("api_base", gettext_lazy("API URL (live)"), type="url", help=gettext_lazy("Example: https://api.examplepay.invalid")),
        ConfigField("sandbox_api_base", gettext_lazy("API URL (sandbox)"), type="url", required=False),
        ConfigField("pay_host", gettext_lazy("Payment page host"), help=gettext_lazy("Hostname only, e.g. pay.examplepay.invalid; redirecting is allowed only to this host.")),
        ConfigField("api_key", gettext_lazy("API key"), secret=True),
    )

    def __init__(self, config=None, test_mode=False):
        super().__init__(config, test_mode)
        self.redirect_hosts = (str(self.config.get("pay_host", "")).strip().lower(),)

    # --- کمکی
    def _base(self):
        base = self.config.get("sandbox_api_base") if self.test_mode and self.config.get("sandbox_api_base") else self.config.get("api_base", "")
        return str(base).rstrip("/")

    def _headers(self, idempotency=None):
        h = {"Authorization": "Bearer " + str(self.config["api_key"])}
        if idempotency:
            h["Idempotency-Key"] = idempotency
        return h

    @staticmethod
    def _map(data) -> VerifyResult:
        status = str(data.get("status", "")).lower()
        if status in PAID:
            kind = VerifyStatus.PAID
        elif status in FAILED:
            kind = VerifyStatus.FAILED
        elif status in PENDING:
            kind = VerifyStatus.PENDING
        else:
            raise GatewayError(gettext("Unknown status from the gateway."))  # هرگز موفق فرض نمی‌شود
        try:
            amount = int(data.get("amount", 0))
        except (TypeError, ValueError):
            amount = -1
        return VerifyResult(kind, amount=amount, currency=str(data.get("currency", "")), gateway_ref=str(data.get("id", "")), order_ref=str(data.get("order_id", "")),
                            raw={"status": status})

    # --- رابط استاندارد
    def create_transaction(self, ctx):
        body = {"order_id": ctx.reference, "amount": ctx.amount, "currency": ctx.currency, "callback_url": ctx.callback_url, "description": ctx.description}
        data = request_json("POST", f"{self._base()}/v1/payments", json_body=body, headers=self._headers(idempotency=ctx.reference))
        pid, token = str(data.get("id", "")), str(data.get("pay_token", ""))
        if not pid or not token:
            raise GatewayError(gettext("The create-transaction response is incomplete."))
        return CreateResult(redirect=Redirect(url=f"https://{self.redirect_hosts[0]}/pay/{quote(token, safe='')}"), gateway_ref=pid, raw={"id": pid})

    def verify_transaction(self, ctx, params):
        # شناسهٔ تراکنش فقط از رکورد خودمان می‌آید؛ params مرورگر (حتی اگر id بدهد) نادیده گرفته می‌شود
        if not ctx.gateway_ref:
            return self.inquire_status(ctx)
        return self._map(request_json("POST", f"{self._base()}/v1/payments/{quote(ctx.gateway_ref, safe='')}/verify", headers=self._headers(ctx.reference)))

    def inquire_status(self, ctx):
        if ctx.gateway_ref:
            return self._map(request_json("GET", f"{self._base()}/v1/payments/{quote(ctx.gateway_ref, safe='')}", headers=self._headers()))
        data = request_json("GET", f"{self._base()}/v1/payments?order_id={quote(ctx.reference, safe='')}", headers=self._headers())
        items = data.get("items") or []
        if not items:
            return VerifyResult.failed()  # ارائه‌دهنده چنین تراکنشی ندارد: ایجاد هرگز انجام نشده
        return self._map(items[0])
