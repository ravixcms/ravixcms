"""نام منسوخ دستور؛ از «ravix_preflight» استفاده کنید. تا Ravix 2.0 همان دستور را اجرا می‌کند."""

from apps.core.deprecation import warn_legacy
from apps.core.management.commands.ravix_preflight import Command as _Command


class Command(_Command):
    help = "[منسوخ] " + (_Command.help or "") + " — به‌جای آن ravix_preflight"

    def run_from_argv(self, argv):
        warn_legacy("ravin_preflight", "ravix_preflight", stacklevel=2)
        return super().run_from_argv(argv)
