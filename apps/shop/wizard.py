"""Setup guide of Ravix Shop (like WooCommerce's setup wizard): the steps a new store needs, each linking to the real page and ticked
automatically once done. It can be dismissed and opened again later from the shop settings."""
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy

from apps.core.modules import WizardStep
from apps.sites.access import panel_site

from .models import GatewayConfig, Product, ShopSettings


def _url(name):
    try:
        return reverse(name)
    except NoReverseMatch:
        return ""


def steps(request):
    site = panel_site(request)
    if site is None:
        return []
    cfg = ShopSettings.objects.filter(site=site).first()
    out = [
        WizardStep("currency", gettext_lazy("Currency and toman display"), gettext_lazy("Choose the shop currency (rial, toman, dollar or euro) and whether prices are shown in toman. It is fixed once the first product exists."),
                   _url("shop_panel:settings"), done=cfg is not None),
        WizardStep("store", gettext_lazy("Store details"), gettext_lazy("The store name, address, phone and email shown on invoices and customer emails."),
                   _url("shop_panel:settings"), done=bool(cfg and cfg.store_name)),
        WizardStep("gateway", gettext_lazy("Payment gateway"), gettext_lazy("Connect and enable a payment gateway so customers can pay."),
                   _url("shop_panel:gateways"), done=GatewayConfig.objects.filter(site=site, enabled=True).exists()),
    ]
    shipping = _url("shop_panel:shipping")
    if shipping:  # the shipping step appears together with shipping methods
        from .models import ShippingMethod  # noqa: PLC0415

        out.append(WizardStep("shipping", gettext_lazy("Shipping method"), gettext_lazy("Add a shipping method with its cost by region and weight."), shipping,
                              done=ShippingMethod.objects.filter(site=site).exists()))
    out.append(WizardStep("product", gettext_lazy("First product"), gettext_lazy("Create your first product."), _url("shop_panel:product_create"), done=Product.objects.filter(site=site).exists()))
    return out
