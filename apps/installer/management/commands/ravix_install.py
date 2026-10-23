from apps.core.clilang import cli_gettext
import getpass
import os

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.installer import services


class Command(BaseCommand):
    help = (cli_gettext("Install without the web (for scripts/Docker): runs migrations and creates the superadmin and initial settings. The password comes only from an environment variable (--password-env) or an interactive prompt; never from a command-line argument."))

    def add_arguments(self, parser):
        parser.add_argument("--site-name", default="", help="default: \"Ravix\" in the --language language")
        parser.add_argument("--language", default="fa")
        parser.add_argument("--timezone", default="Asia/Tehran")
        parser.add_argument("--username", required=True)
        parser.add_argument("--email", required=True)
        parser.add_argument("--password-env", metavar="VAR", help=cli_gettext("Name of the environment variable that holds the password"))
        parser.add_argument("--skip-migrate", action="store_true")

    def handle(self, *args, **o):
        if services.is_installed():
            raise CommandError(cli_gettext("Installation has already been done."))
        password = os.environ.get(o["password_env"]) if o["password_env"] else None
        if not password:
            if o["password_env"]:
                raise CommandError(cli_gettext("The variable %(a1)s is not set or is empty.") % {"a1": o['password_env']})
            password = getpass.getpass(cli_gettext("Superadmin password: "))
            if password != getpass.getpass(cli_gettext("Repeat password: ")):
                raise CommandError(cli_gettext("The two passwords do not match."))
        try:
            validate_password(password, user=User(username=o["username"], email=o["email"]))
        except ValidationError as exc:
            raise CommandError(cli_gettext("; ").join(exc.messages)) from exc
        if not o["skip_migrate"]:
            services.run_migrations()
        try:
            services.perform_install(site_name=o["site_name"] or services.default_site_name(o["language"]), language=o["language"], timezone_name=o["timezone"],
                                     username=o["username"], email=o["email"].strip().lower(), password=password)
        except (services.InstallError, services.AlreadyInstalled) as exc:
            raise CommandError(str(exc) or cli_gettext("Installation has already been done.")) from exc
        self.stdout.write(self.style.SUCCESS(cli_gettext("Installation complete.")))
