from django.apps import AppConfig


class SitesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sites"
    verbose_name = "سایت‌ها"

    def ready(self):
        from apps.core.navigation import NavItem, register_nav_item

        register_nav_item(NavItem(key="sites.list", label="سایت‌ها", url_name="sites:list", icon="globe", section="system", order=55,
                                  capability="sites.manage", active_urls=("sites:list", "sites:detail")))
