from django.utils.translation import gettext_lazy
from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.audit"
    verbose_name = gettext_lazy("Event log")

    def ready(self):
        from apps.core.navigation import NavItem, register_nav_item

        from . import signals  # noqa: F401

        register_nav_item(
            NavItem(
                key="audit.log",
                label=gettext_lazy("Event log"),
                url_name="audit:log_list",
                icon="list",
                section="system",
                order=80,
                capability="audit.view",
                active_urls=("audit:log_list",),
            )
        )
