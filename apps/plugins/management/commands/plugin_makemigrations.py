from apps.core.clilang import cli_gettext
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.plugins import registry


class Command(BaseCommand):
    help = cli_gettext("(Development only) Creates the migrations of a plugin. The code is loaded without approval; allowed only with DEBUG=True.")

    def add_arguments(self, parser):
        parser.add_argument("slug")

    def handle(self, *args, slug, **options):
        try:
            registry.load_for_development(slug)
        except Exception as exc:  # noqa: BLE001
            raise CommandError(str(exc))
        call_command("makemigrations", f"plugin_{slug}", verbosity=1)
        self.stdout.write(self.style.WARNING(cli_gettext("The plugin code changed; an administrator must approve it again before it is used on a server.")))
