from django.core.management.base import BaseCommand, CommandError

from apps.plugins import services


class Command(BaseCommand):
    help = "افزونه‌های موجود را بررسی می‌کند (manifest، سازگاری، اثرانگشت). هیچ کدی اجرا نمی‌شود."

    def add_arguments(self, parser):
        parser.add_argument("slugs", nargs="*")

    def handle(self, *args, slugs, **options):
        rows = services.overview()
        if slugs:
            rows = [r for r in rows if r.slug in slugs]
            if not rows:
                raise CommandError("افزونه‌ای با این نام پیدا نشد.")
        bad = 0
        for r in rows:
            line = f"{r.slug:<20} {r.version or '-':<8} {r.status_label}"
            if r.status in ("invalid", "incompatible", "missing", "update_pending"):
                bad += 1
                self.stdout.write(self.style.ERROR(line))
                for p in r.problems:
                    self.stdout.write(f"    - {p}")
            else:
                self.stdout.write(self.style.SUCCESS(line))
        if bad:
            raise CommandError(f"{bad} افزونه مشکل دارد.")
