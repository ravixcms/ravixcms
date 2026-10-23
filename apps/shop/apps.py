from django.utils.translation import gettext_lazy
from django.apps import AppConfig


class ShopConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.shop"
    verbose_name = gettext_lazy("Shop")

    def ready(self):
        from apps.core.navigation import NavItem, register_nav_item

        register_nav_item(NavItem(key="shop.products", label=gettext_lazy("Products"), url_name="shop_panel:products", icon="layers", section="content", order=60,
                                  capability="shop.manage", active_urls=("shop_panel:products", "shop_panel:product_create", "shop_panel:product_edit")))
        register_nav_item(NavItem(key="shop.categories", label=gettext_lazy("Shop categories"), url_name="shop_panel:categories", icon="layers", section="content", order=60,
                                  capability="shop.manage", active_urls=("shop_panel:categories", "shop_panel:category_create", "shop_panel:category_edit")))
        register_nav_item(NavItem(key="shop.gateways", label=gettext_lazy("Payment gateways"), url_name="shop_panel:gateways", icon="layers", section="content", order=62,
                                  capability="shop.gateways", active_urls=("shop_panel:gateways", "shop_panel:gateway_edit")))
        from . import gateways  # noqa: F401 - ثبت درگاه آزمایشی داخلی
        register_nav_item(NavItem(key="shop.orders", label=gettext_lazy("Orders"), url_name="shop_panel:orders", icon="list", section="content", order=61,
                                  capability="shop.orders", active_urls=("shop_panel:orders", "shop_panel:order")))
