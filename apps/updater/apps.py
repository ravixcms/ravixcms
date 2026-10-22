from django.apps import AppConfig


class UpdaterConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.updater"
    verbose_name = "به‌روزرسانی"

    def ready(self):
        from apps.core.navigation import NavItem, register_nav_item

        register_nav_item(NavItem(key="updater.home", label="به‌روزرسانی‌ها", url_name="updater:home", icon="layers", section="system", order=90,
                                  capability="updates.view", active_urls=("updater:home", "updater:run")))
