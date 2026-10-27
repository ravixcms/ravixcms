from apps.core.clilang import cli_gettext
from django.core.management.base import BaseCommand

from apps.shop import services


class Command(BaseCommand):
    help = cli_gettext("Before cancelling, asks the gateway; then cancels overdue unpaid orders, restores stock and closes inconclusive transactions. Run it with a timer every few minutes.")

    def handle(self, *args, **options):
        from apps.core import modules

        if not modules.is_enabled("shop"):
            self.stdout.write(cli_gettext("Ravix Shop is switched off; nothing was changed."))
            return
        self.stdout.write(cli_gettext("%(a1)s orders were cancelled.") % {"a1": services.expire_pending()})
        self.stdout.write(cli_gettext("%(a1)s inconclusive transactions were closed.") % {"a1": services.expire_stale_payments()})
