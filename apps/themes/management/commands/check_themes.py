from apps.core.clilang import cli_gettext
from django.core.management.base import BaseCommand, CommandError

from apps.themes import registry


class Command(BaseCommand):
    help = cli_gettext("Checks the installed themes against the theme contract (theme.json, required files, allowed tags).")

    def add_arguments(self, parser):
        parser.add_argument("slugs", nargs="*", help=cli_gettext("Slugs of the themes to check (default: all)"))

    def handle(self, *args, **options):
        themes = registry.get_themes(refresh=True)
        slugs = options["slugs"] or list(themes)
        if not themes:
            raise CommandError(cli_gettext("No theme was found in the RAVIX_THEME_DIRS paths."))
        bad = 0
        for slug in slugs:
            info = themes.get(slug)
            if info is None:
                self.stderr.write(self.style.ERROR(cli_gettext("✗ %(slug)s: no theme with this name was found.") % {"slug": slug}))
                bad += 1
            elif info.valid:
                self.stdout.write(self.style.SUCCESS(cli_gettext("✓ %(slug)s (version %(a2)s) is valid.") % {"slug": slug, "a2": info.version}))
            else:
                bad += 1
                self.stderr.write(self.style.ERROR(cli_gettext("✗ %(slug)s is invalid:") % {"slug": slug}))
                for error in info.errors:
                    self.stderr.write(f"    - {error}")
        if bad:
            raise CommandError(cli_gettext("%(bad)s themes are invalid or missing.") % {"bad": bad})
