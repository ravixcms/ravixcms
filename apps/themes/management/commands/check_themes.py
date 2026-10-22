from django.core.management.base import BaseCommand, CommandError

from apps.themes import registry


class Command(BaseCommand):
    help = "قالب‌های نصب‌شده را بر اساس قرارداد قالب (theme.json، فایل‌های الزامی، تگ‌های مجاز) بررسی می‌کند."

    def add_arguments(self, parser):
        parser.add_argument("slugs", nargs="*", help="نامک قالب‌هایی که باید بررسی شوند (پیش‌فرض: همه)")

    def handle(self, *args, **options):
        themes = registry.get_themes(refresh=True)
        slugs = options["slugs"] or list(themes)
        if not themes:
            raise CommandError("هیچ قالبی در مسیرهای RAVIX_THEME_DIRS پیدا نشد.")
        bad = 0
        for slug in slugs:
            info = themes.get(slug)
            if info is None:
                self.stderr.write(self.style.ERROR(f"✗ {slug}: قالبی با این نام پیدا نشد."))
                bad += 1
            elif info.valid:
                self.stdout.write(self.style.SUCCESS(f"✓ {slug} (نسخه {info.version}) معتبر است."))
            else:
                bad += 1
                self.stderr.write(self.style.ERROR(f"✗ {slug} نامعتبر است:"))
                for error in info.errors:
                    self.stderr.write(f"    - {error}")
        if bad:
            raise CommandError(f"{bad} قالب نامعتبر یا ناموجود است.")
