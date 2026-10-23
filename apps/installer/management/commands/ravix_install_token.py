from apps.core.clilang import cli_gettext
from django.core.management.base import BaseCommand

from apps.installer import services, token


class Command(BaseCommand):
    help = cli_gettext("Prints the install token of the /install/ page (creates one if there is none). Prints nothing after installation.")

    def handle(self, *args, **options):
        if services.is_installed():
            self.stdout.write(cli_gettext("Installation has already been done; no token is needed."))
            return
        if not token.required():
            self.stdout.write(cli_gettext("DEBUG is on; no token is needed."))
            return
        self.stdout.write(token.expected_token())
