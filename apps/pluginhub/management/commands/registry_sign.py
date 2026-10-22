"""نسخه‌های منتشرشدهٔ بدون امضا را با کلید امضای مخزن (از env/فایل) امضا می‌کند."""
from django.core.management.base import BaseCommand, CommandError

from apps.pluginhub import services
from apps.pluginhub.models import HubVersion


class Command(BaseCommand):
    help = "امضای نسخه‌های منتشرشدهٔ بدون امضا."

    def handle(self, *args, **opts):
        n = 0
        for v in HubVersion.objects.filter(status=HubVersion.Status.PUBLISHED, signature="").select_related("plugin"):
            try:
                if services.sign_version(v):
                    v.save(update_fields=["signature", "signature_key_id"])
                    n += 1
                else:
                    raise CommandError("کلید امضای مخزن تنظیم نشده است (RAVIX_REGISTRY_SIGNING_KEY_FILE یا RAVIX_REGISTRY_SIGNING_KEY).")
            except services.HubError as exc:
                raise CommandError(str(exc))
        self.stdout.write(f"{n} نسخه امضا شد.")
