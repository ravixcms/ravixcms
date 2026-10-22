from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.audit"
    verbose_name = "گزارش رویدادها"

    def ready(self):
        from apps.core.navigation import NavItem, register_nav_item

        from . import signals  # noqa: F401

        register_nav_item(
            NavItem(
                key="audit.log",
                label="گزارش رویدادها",
                url_name="audit:log_list",
                icon="list",
                section="system",
                order=80,
                capability="audit.view",
                active_urls=("audit:log_list",),
            )
        )
