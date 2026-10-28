from django.utils.translation import gettext_lazy
from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = gettext_lazy("Ravix core")

    def ready(self):
        self._activate_cli_language()
        from . import builtin_modules  # noqa: F401 - registers the built-in modules
        from . import pagecache  # noqa: F401 - connects the signals that clear the page cache (also needed in cron and shell processes, not only web workers)
        from .navigation import NavItem, register_nav_item

        register_nav_item(
            NavItem(
                key="core.settings",
                label=gettext_lazy("Site settings"),
                url_name="core:site_settings",
                icon="settings",
                section="system",
                order=90,
                capability="settings.manage",
                active_urls=("core:site_settings",),
            )
        )
        register_nav_item(
            NavItem(key="core.speed", label=gettext_lazy("Speed"), url_name="core:speed_settings", icon="settings", section="system", order=92,
                    capability="settings.manage", active_urls=("core:speed_settings",))
        )
        from django.conf import settings as dj_settings

        if str(getattr(dj_settings, "EMAIL_BACKEND", "")).startswith("apps.core.mailbackend."):  # the panel's SMTP page only matters with Ravix's own mail backend
            register_nav_item(
                NavItem(key="core.email", label=gettext_lazy("Email"), url_name="core:email_settings", icon="settings", section="system", order=93,
                        capability="settings.manage", active_urls=("core:email_settings",))
            )
        register_nav_item(
            NavItem(
                key="core.health",
                label=gettext_lazy("Site health"),
                url_name="core:site_health",
                icon="shield",
                section="system",
                order=91,
                capability="health.view",
                active_urls=("core:site_health",),
            )
        )

    @staticmethod
    def _activate_cli_language():
        """``manage.py <command>``: show translated (gettext) text in the operator's language, not the default one.

        The language is RAVIX_CLI_LANG, else LANGUAGE_CODE (see apps/core/clilang.py). ``runserver`` and ``test`` keep their own
        per-request / per-test language handling."""
        import os
        import sys

        if os.path.basename(sys.argv[0] or "") not in ("manage.py", "django-admin") or len(sys.argv) < 2:
            return
        if sys.argv[1] in ("runserver", "test"):
            return
        from django.utils import translation

        from .clilang import cli_language
        from .regional import normalize_language

        translation.activate(normalize_language(cli_language()) or "en")
