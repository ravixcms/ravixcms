"""The built-in modules that ship with Ravix. Add a ``register_module(...)`` here for the next one (e.g. Ravix Studio)."""
from django.utils.translation import gettext_lazy

from .modules import ModuleDef, register_module


def _shop_steps(request):
    from apps.shop.wizard import steps

    return steps(request)


register_module(ModuleDef(
    key="shop",
    name="Ravix Shop",  # a product name: not translated
    description=gettext_lazy("An online store inside Ravix: physical and digital products, cart and checkout, payment gateways, orders, discounts, shipping and reports."),
    version="1.0.0",
    app_label="shop",
    namespaces=("shop", "shop_panel"),
    path_prefixes=("/api/v1/shop/",),
    hook_prefixes=("shop.",),
    lock_hint=gettext_lazy("The server has switched the shop off (RAVIX_SHOP_ENABLED=false in ravix.env). Ask the server administrator to remove that line, then restart once."),
    settings_url_name="shop_panel:settings",
    wizard_steps=_shop_steps,
))


register_module(ModuleDef(
    key="studio",
    name="Ravix Studio",  # a product name: not translated
    description=gettext_lazy("A visual page builder: design pages and posts with drag and drop, with responsive settings, revisions and an approval flow. Published pages are saved as plain HTML and keep working when the module is off."),
    version="1.0.0",
    app_label="studio",
    namespaces=("studio",),
    path_prefixes=("/api/v1/studio/",),
    hook_prefixes=("studio.",),
    lock_hint=gettext_lazy("The server has switched Ravix Studio off (RAVIX_STUDIO_ENABLED=false in ravix.env). Ask the server administrator to remove that line, then restart once. Pages that were already published keep showing."),
    lock_setting="STUDIO_ENABLED",
))
