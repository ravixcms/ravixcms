"""نام منسوخ دستور؛ از «ravix_release» استفاده کنید. تا Ravix 2.0 همان دستور را اجرا می‌کند."""

from apps.core.clilang import cli_gettext
from apps.core.deprecation import warn_legacy
from apps.updater.management.commands.ravix_release import Command as _Command


class Command(_Command):
    help = cli_gettext("[deprecated] ") + (_Command.help or "") + cli_gettext(" — use ravix_release instead")

    def run_from_argv(self, argv):
        warn_legacy("ravin_release", "ravix_release", stacklevel=2)
        return super().run_from_argv(argv)
