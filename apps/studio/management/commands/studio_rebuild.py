from django.core.management.base import BaseCommand

from apps.core.clilang import cli_gettext
from apps.studio import render as renderer, services


class Command(BaseCommand):
    help = cli_gettext("Draw the published Ravix Studio pages again (after a widget changed, or to refresh image files). Without options only pages made by an older renderer are rebuilt.")

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help=cli_gettext("Rebuild every published page, not only the stale ones."))

    def handle(self, *args, **options):
        stale = services.stale_versions().count()
        total = services.live_versions().count()
        count = services.rebuild_stale(force=options["all"])
        self.stdout.write(cli_gettext("Renderer version %(v)s: %(done)s of %(total)s published page(s) rebuilt (%(stale)s were stale).") % {
            "v": renderer.RENDERER_VERSION, "done": count, "total": total, "stale": stale})
