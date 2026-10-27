from django import template

from apps.shop import money as money_mod
from apps.shop.models import ShopSettings
from apps.sites.middleware import current_site

register = template.Library()


def _settings(context):
    cached = context.get("shop_settings")
    if cached is not None:
        return cached
    site = context.get("site_obj")
    if site is None and context.get("request") is not None:
        site = current_site(context["request"])
    return ShopSettings.for_site(site) if site is not None else None


@register.simple_tag(takes_context=True)
def price(context, amount, source=None):
    """``{% price p.final_price %}`` — an amount in the shop currency; ``{% price o.total o %}`` — in the currency of an order / payment
    (anything with ``.currency``) or a currency code. Language-correct, with the currency name or symbol."""
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return ""
    cfg = _settings(context)
    code = getattr(source, "currency", source) if source else (cfg.currency if cfg else "IRT")
    if not money_mod.is_valid(code):
        return str(amount)
    return money_mod.format_money(amount, code, show_toman=bool(cfg and cfg.toman_display and code == cfg.currency))


@register.simple_tag(takes_context=True)
def price_number(context, amount, source=None):
    """Like ``price`` but only the number (for tables that print the currency once in the header)."""
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return ""
    cfg = _settings(context)
    code = getattr(source, "currency", source) if source else (cfg.currency if cfg else "IRT")
    if not money_mod.is_valid(code):
        return str(amount)
    return money_mod.format_amount(amount, code, show_toman=bool(cfg and cfg.toman_display and code == cfg.currency))
