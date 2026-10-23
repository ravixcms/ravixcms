from apps.core.clilang import cli_gettext
import json
import re
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.themes import registry
from apps.themes.constants import DEFAULT_THEME, THEME_MANIFEST
from apps.themes.manifest import SLUG_RE


class Command(BaseCommand):
    help = cli_gettext("Creates a new theme from the default theme (themes/<slug>) to make starting easy.")

    def add_arguments(self, parser):
        parser.add_argument("slug", help=cli_gettext("Theme slug: lowercase English letters, digits, “-” and “_”"))
        parser.add_argument("--name", default="", help=cli_gettext("Display name of the theme"))
        parser.add_argument("--author", default="", help=cli_gettext("Author name"))
        parser.add_argument("--from", dest="source", default=DEFAULT_THEME, help=cli_gettext("Source theme (default: default)"))

    def handle(self, *args, **options):
        slug = options["slug"]
        if not SLUG_RE.match(slug):
            raise CommandError(cli_gettext("The slug is invalid; only lowercase English letters, digits, “-” and “_” (at most 49 characters)."))
        themes = registry.get_themes(refresh=True)
        source = themes.get(options["source"])
        if source is None:
            raise CommandError(cli_gettext("The source theme “%(a1)s” was not found.") % {"a1": options['source']})
        if slug in themes:
            raise CommandError(cli_gettext("A theme with the slug “%(slug)s” already exists.") % {"slug": slug})
        dirs = [Path(d) for d in getattr(settings, "RAVIX_THEME_DIRS", [])]
        if not dirs:
            raise CommandError(cli_gettext("RAVIX_THEME_DIRS is empty."))
        target = dirs[0] / slug
        if target.exists():
            raise CommandError(cli_gettext("The path %(target)s already exists.") % {"target": target})
        shutil.copytree(source.path, target, ignore=shutil.ignore_patterns("__pycache__", "*.png", "*.jpg", "*.webp"))
        manifest_path = target / THEME_MANIFEST
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        data.update(
            slug=slug,
            name=options["name"] or re.sub(r"[-_]+", " ", slug).title(),
            version="0.1.0",
            author=options["author"] or data.get("author", ""),
            description=cli_gettext("A new theme based on the theme “%s”; edit this description.") % source.slug,
        )
        data.pop("screenshot", None)
        manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(cli_gettext("The theme “%(slug)s” was created in %(target)s.") % {"slug": slug, "target": target}))
        self.stdout.write(cli_gettext("Check: python manage.py check_themes %(slug)s") % {"slug": slug})
