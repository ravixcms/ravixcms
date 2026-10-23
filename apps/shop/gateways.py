"""نمای سازگار: تمام منطق در ``apps.shop.payments`` است. اینجا فقط درگاه آزمایشیِ داخلی (شبیه‌ساز) قرار دارد و هیچ ارائه‌دهندهٔ واقعی‌ای در هسته نیست.

درگاه واقعی = افزونهٔ مستقل (docs/payment-gateways.md). هیچ درگاه واقعی‌ای متصل نیست.
"""
from django.utils.translation import gettext_lazy
from django.urls import reverse

from .payments import *  # noqa: F401,F403
from .payments import (CreateResult, PaymentGateway, Redirect, TransactionContext, VerifyResult, VerifyStatus, register_gateway)
from .payments import registry as _registry

GATEWAYS = _registry.GATEWAYS
SandboxStartResult = CreateResult


class SandboxGateway(PaymentGateway):
    """شبیه‌ساز بانک برای توسعه و تست. وضعیت فقط توسط صفحهٔ آزمایشی تغییر می‌کند و تأیید همان را از دیتابیس می‌خواند."""

    name = "sandbox"
    label = gettext_lazy("Test gateway (simulator)")
    description = gettext_lazy("For development and demos only; no real money is moved.")
    is_test = True
    reports_currency = False

    def create_transaction(self, ctx: TransactionContext):
        from .models import SandboxTransaction

        SandboxTransaction.objects.get_or_create(reference=ctx.reference, defaults={"amount": ctx.amount})
        return CreateResult(redirect=Redirect(url=reverse("shop:sandbox_pay", args=[ctx.reference])), gateway_ref=ctx.reference)

    def _read(self, ctx):
        from .models import SandboxTransaction

        tx = SandboxTransaction.objects.filter(reference=ctx.reference).first()
        if tx is None:
            return VerifyResult.failed()
        if tx.state == SandboxTransaction.State.PAID:
            return VerifyResult.paid(tx.amount, gateway_ref=tx.reference, order_ref=tx.reference)
        if tx.state == SandboxTransaction.State.FAILED:
            return VerifyResult.failed(gateway_ref=tx.reference)
        return VerifyResult.pending(gateway_ref=tx.reference)

    def verify_transaction(self, ctx, params):
        return self._read(ctx)

    def inquire_status(self, ctx):
        return self._read(ctx)


register_gateway("sandbox", SandboxGateway)
