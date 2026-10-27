"""Order status notifications: an email to the customer in the language of the order, and the ``shop.order_status_changed`` hook
(the SMS hook: a plugin that talks to an SMS provider listens to it; Ravix Shop itself sends no SMS)."""
import logging

from django.conf import settings
from django.db import transaction
from django.utils.translation import gettext, gettext_lazy

from apps.core.mailhtml import build_message
from apps.core.recipient import language_for_order, use_language

from . import money
from .models import Order, ShopSettings

logger = logging.getLogger("ravix.shop")

S = Order.Status
# What the customer is told for each status (lazy: translated in the order's language at send time).
STATUS_TEXT = {
    S.PENDING: gettext_lazy("We received your order. It will be processed once the payment is confirmed."),
    S.PAID: gettext_lazy("Your payment was confirmed. Thank you!"),
    S.PROCESSING: gettext_lazy("Your order is being prepared."),
    S.SHIPPED: gettext_lazy("Your order has been shipped."),
    S.COMPLETED: gettext_lazy("Your order is complete."),
    S.CANCELLED: gettext_lazy("Your order was cancelled."),
    S.REFUNDED: gettext_lazy("Your order was refunded."),
}


def _base_url(order) -> str:
    if settings.SITE_URL:
        return settings.SITE_URL
    domain = order.site.domains.order_by("-is_primary", "id").values_list("domain", flat=True).first()
    return f"https://{domain}" if domain else ""


def build_email(order, new_status):
    """``(subject, body)`` in the order's language (never the language of whoever triggered the change)."""
    with use_language(language_for_order(order)):
        label = str(Order.Status(new_status).label)
        subject = (gettext("Order %(number)s: %(status)s") % {"number": order.number, "status": label}).replace("\n", " ")[:150]
        show_toman = ShopSettings.for_site(order.site).toman_display
        lines = [gettext("Hello %(name)s,") % {"name": order.customer_name}, "", str(STATUS_TEXT.get(new_status, label)), ""]
        lines.append(gettext("Order number: %(number)s") % {"number": order.number})
        for item in order.items.all():
            lines.append(f"- {item.name} × {item.quantity}: {money.format_money(item.line_total, order.currency, show_toman=show_toman and order.currency == 'IRR')}")
        lines.append(gettext("Grand total: %(total)s") % {"total": money.format_money(order.total, order.currency, show_toman=show_toman and order.currency == "IRR")})
        base = _base_url(order)
        if base:
            lines += ["", gettext("Track your order: %(url)s") % {"url": base + order.get_public_url()}]
        return subject, "\n".join(lines)


def _send(order_id, new_status, old_status):
    order = Order.objects.select_related("site", "user").filter(pk=order_id).first()
    if order is None:
        return
    cfg = ShopSettings.for_site(order.site)
    if order.email and cfg.notify_customers:
        try:
            subject, body = build_email(order, new_status)
            build_message(subject, body, [order.email], settings.DEFAULT_FROM_EMAIL).send()
        except Exception:  # noqa: BLE001 - a mail problem never breaks an order
            logger.exception("Sending the order status email failed (order %s).", order.number)
    try:
        from apps.plugins.hooks import run_action

        with use_language(language_for_order(order)):
            text = str(STATUS_TEXT.get(new_status, ""))
        run_action("shop.order_status_changed", order_id=order.pk, number=order.number, old_status=old_status, new_status=new_status,
                   phone=order.phone, language=language_for_order(order), text=text)
    except Exception:  # noqa: BLE001
        logger.exception("The shop.order_status_changed hook failed (order %s).", order.number)


def order_status_changed(order, old_status, new_status):
    """Called after the status row is written; runs once the surrounding transaction commits (never for a rolled-back change)."""
    if old_status == new_status:
        return
    from apps.core import modules

    if not modules.is_enabled("shop"):  # switched off: no emails, no hook (the order itself is untouched)
        return
    transaction.on_commit(lambda: _send(order.pk, new_status, old_status))


def low_stock(site_id, crossings):
    """Tell the shop owner that some stock fell to its alert threshold: one email (site language) and one ``shop.low_stock`` hook call per item.
    ``crossings`` is a list of ``("product" | "variant", pk)``; the levels are read again from the database."""
    from apps.core import modules
    from apps.core.models import SiteSettings
    from apps.sites.models import Site

    from .models import Product, ProductVariant

    if not modules.is_enabled("shop"):
        return
    site = Site.objects.filter(pk=site_id).first()
    if site is None:
        return
    rows = []
    for kind, pk in crossings:
        obj = (ProductVariant.objects.select_related("product").filter(pk=pk, site=site) if kind == "variant" else Product.objects.filter(pk=pk, site=site)).first()
        if obj is None:
            continue
        product = obj.product if kind == "variant" else obj
        name = product.name + (f" ({obj.label})" if kind == "variant" else "")
        rows.append({"kind": kind, "object_id": obj.pk, "product_id": product.pk, "name": name, "sku": (obj.sku or product.sku or ""), "stock": obj.stock,
                     "threshold": product.effective_low_stock_threshold})
    if not rows:
        return
    cfg = ShopSettings.for_site(site)
    to = cfg.store_email or getattr(SiteSettings.load(), "admin_email", "")
    if to:
        try:
            with use_language():  # the shop owner reads it: the site language
                subject = gettext("Low stock: %(count)s product(s) need restocking") % {"count": len(rows)}
                lines = [gettext("These items have reached the low-stock threshold:"), ""]
                for r in rows:
                    sku = f" [{r['sku']}]" if r["sku"] else ""
                    lines.append(gettext("- %(name)s%(sku)s: %(stock)s left (alert at %(threshold)s)") % {"name": r["name"], "sku": sku, "stock": r["stock"], "threshold": r["threshold"]})
            build_message(subject, "\n".join(lines), [to], settings.DEFAULT_FROM_EMAIL).send()
        except Exception:  # noqa: BLE001
            logger.exception("Sending the low-stock email failed (site %s).", site_id)
    try:
        from apps.plugins.hooks import run_action

        for r in rows:
            run_action("shop.low_stock", **r)
    except Exception:  # noqa: BLE001
        logger.exception("The shop.low_stock hook failed (site %s).", site_id)
