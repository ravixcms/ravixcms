from django.utils.translation import gettext_lazy
from django.apps import AppConfig


class ThemesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.themes"
    verbose_name = gettext_lazy("Site themes")

    def ready(self):
        from apps.core.navigation import NavItem, register_nav_item

        register_nav_item(
            NavItem(
                key="themes.list", label=gettext_lazy("Themes"), url_name="themes:list", icon="palette", section="appearance", order=10,
                capability="themes.manage",
                active_urls=("themes:list",),
            )
        )
        register_nav_item(
            NavItem(
                key="themes.customize", label=gettext_lazy("Customize appearance"), url_name="themes:customize", icon="brush", section="appearance", order=20,
                capability="themes.manage",
                active_urls=("themes:customize", "themes:layout", "themes:menus", "themes:home"),
            )
        )
