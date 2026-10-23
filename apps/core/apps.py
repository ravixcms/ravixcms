from django.utils.translation import gettext_lazy
from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = gettext_lazy("Ravix core")

    def ready(self):
        from .navigation import NavItem, register_nav_item

        register_nav_item(
            NavItem(
                key="core.settings",
                label=gettext_lazy("Site settings"),
                url_name="core:site_settings",
                icon="settings",
                section="system",
                order=90,
                capability="settings.manage",
                active_urls=("core:site_settings",),
            )
        )
        register_nav_item(
            NavItem(
                key="core.health",
                label=gettext_lazy("Site health"),
                url_name="core:site_health",
                icon="shield",
                section="system",
                order=91,
                capability="health.view",
                active_urls=("core:site_health",),
            )
        )
