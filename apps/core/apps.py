from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = "هسته Ravin"

    def ready(self):
        from .navigation import NavItem, register_nav_item

        register_nav_item(
            NavItem(
                key="core.settings",
                label="تنظیمات سایت",
                url_name="core:site_settings",
                icon="settings",
                section="system",
                order=90,
                capability="settings.manage",
                active_urls=("core:site_settings",),
            )
        )
