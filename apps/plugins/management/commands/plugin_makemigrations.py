from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.plugins import registry


class Command(BaseCommand):
    help = "(فقط توسعه) مهاجرت‌های یک افزونه را می‌سازد. کد بدون تأیید بارگذاری می‌شود؛ فقط با DEBUG=True مجاز است."

    def add_arguments(self, parser):
        parser.add_argument("slug")

    def handle(self, *args, slug, **options):
        try:
            registry.load_for_development(slug)
        except Exception as exc:  # noqa: BLE001
            raise CommandError(str(exc))
        call_command("makemigrations", f"plugin_{slug}", verbosity=1)
        self.stdout.write(self.style.WARNING("کد افزونه تغییر کرد؛ پیش از استفاده در سرور باید دوباره توسط مدیر تأیید شود."))
