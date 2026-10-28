from django.apps import AppConfig
from django.utils.translation import gettext_lazy


class StudioConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.studio"
    verbose_name = gettext_lazy("Ravix Studio")

    def ready(self):
        from apps.core.navigation import NavItem, register_nav_item

        from . import signals, widgets  # noqa: F401  (registers the built-in widgets and the signal handlers)

        from django.db.models.signals import post_migrate

        post_migrate.connect(_rebuild_after_migrate, sender=self, dispatch_uid="studio_rebuild_after_migrate")
        register_nav_item(NavItem(
            "studio.pages", "Ravix Studio", "studio:list", "layout", "appearance", 5, "studio.edit",
            ("studio:list", "studio:editor"), module="studio",
        ))


def _rebuild_after_migrate(sender, **kwargs):
    """After an upgrade: pages published by an older renderer (or imported from the first page builder) are drawn again."""
    import logging

    from django.db import DatabaseError

    from . import services

    try:
        count = services.rebuild_stale()
    except DatabaseError:
        return  # tables not there yet (a partial migrate)
    except Exception:  # noqa: BLE001 — never fail a migrate because of a page
        logging.getLogger("ravix.studio").exception("Ravix Studio could not rebuild its pages after migrate")
        return
    if count:
        logging.getLogger("ravix.studio").info("Ravix Studio rebuilt %s published page(s)", count)
