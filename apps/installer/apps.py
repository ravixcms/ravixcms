from django.utils.translation import gettext_lazy
from django.apps import AppConfig


class InstallerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.installer"
    verbose_name = gettext_lazy("Web installer")
