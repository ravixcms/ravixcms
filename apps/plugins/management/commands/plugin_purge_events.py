from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.plugins.models import PluginEvent


class Command(BaseCommand):
    help = "رویدادهای قدیمی‌تر از N روز (پیش‌فرض ۹۰) را حذف می‌کند."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=90)

    def handle(self, *args, days, **options):
        n, _ = PluginEvent.objects.filter(created_at__lt=timezone.now() - timedelta(days=days)).delete()
        self.stdout.write(f"{n} رویداد حذف شد.")
