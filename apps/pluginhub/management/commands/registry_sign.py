"""نسخه‌های منتشرشدهٔ بدون امضا را با کلید امضای مخزن (از env/فایل) امضا می‌کند."""
from apps.core.clilang import cli_gettext
from django.core.management.base import BaseCommand, CommandError

from apps.pluginhub import services
from apps.pluginhub.models import HubVersion


class Command(BaseCommand):
    help = cli_gettext("Signs published versions that have no signature.")

    def handle(self, *args, **opts):
        n = 0
        for v in HubVersion.objects.filter(status=HubVersion.Status.PUBLISHED, signature="").select_related("plugin"):
            try:
                if services.sign_version(v):
                    v.save(update_fields=["signature", "signature_key_id"])
                    n += 1
                else:
                    raise CommandError(cli_gettext("The registry signing key is not set (RAVIX_REGISTRY_SIGNING_KEY_FILE or RAVIX_REGISTRY_SIGNING_KEY)."))
            except services.HubError as exc:
                raise CommandError(str(exc))
        self.stdout.write(cli_gettext("%(n)s versions were signed.") % {"n": n})
