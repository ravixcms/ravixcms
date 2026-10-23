from apps.core.clilang import cli_gettext
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.plugins.models import PluginEvent


class Command(BaseCommand):
    help = cli_gettext("Deletes events older than N days (default 90).")

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=90)

    def handle(self, *args, days, **options):
        n, _ = PluginEvent.objects.filter(created_at__lt=timezone.now() - timedelta(days=days)).delete()
        self.stdout.write(cli_gettext("%(n)s events were deleted.") % {"n": n})
