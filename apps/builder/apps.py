from django.apps import AppConfig
from django.utils.translation import gettext_lazy


class BuilderConfig(AppConfig):
    """The first page builder. Only its tables remain (Ravix Studio imports them during migration and keeps them as the rollback source)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.builder"
    verbose_name = gettext_lazy("Page builder (legacy)")
