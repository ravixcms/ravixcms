from django.apps import AppConfig


class BuilderConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.builder"
    verbose_name = "صفحه‌ساز"

    def ready(self):
        from apps.core.navigation import NavItem, register_nav_item

        register_nav_item(NavItem(
            "builder.pages", "صفحه‌ساز", "builder:list", "layout", "appearance", 5, "builder.edit",
            ("builder:list", "builder:editor"),
        ))
