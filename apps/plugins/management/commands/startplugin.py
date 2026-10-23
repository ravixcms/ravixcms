import json
import re

from django.core.management.base import BaseCommand, CommandError

from apps.core.clilang import cli_gettext
from apps.plugins import discovery
from apps.plugins.manifest import RESERVED_SLUGS, SLUG_RE

# The generated plugin is written in English and its texts go through gettext, so it can be translated like Ravix itself
# (a gettext tree in <plugin>/locale/<lang>/LC_MESSAGES/django.mo; see docs/translation.md).
VIEWS = '''from django.utils.translation import gettext


def hello(request, api):
    """Admin page view: (request, api). `api` is the PluginAPI; the core has already checked the permission before calling this."""
    return api.render_admin(request, "hello.html", {"message": api.setting("greeting", gettext("Hello!"))}, title=api.name)
'''
TEMPLATE = '''{% extends "plugins/page.html" %}
{% load i18n %}
{% block plugin_content %}<div class="card"><div class="card-body"><p>{{ message }}</p><p class="help">{% trans "Edit this page in views.py and templates/hello.html." %}</p></div></div>{% endblock %}
'''


class Command(BaseCommand):
    help = cli_gettext("Creates the skeleton of a new plugin in the first RAVIX_PLUGIN_DIRS folder.")

    def add_arguments(self, parser):
        parser.add_argument("slug")
        parser.add_argument("--name", default="")
        parser.add_argument("--author", default="")
        parser.add_argument("--models", action="store_true", help=cli_gettext("Also create a database model"))

    def handle(self, *args, slug, **options):
        if not SLUG_RE.match(slug) or slug in RESERVED_SLUGS:
            raise CommandError(cli_gettext("The slug is invalid or reserved (lowercase English letters, digits and _, starting with a letter, 3 to 31 characters)."))
        dirs = discovery.plugin_dirs()
        if not dirs:
            raise CommandError(cli_gettext("RAVIX_PLUGIN_DIRS is empty."))
        target = dirs[0] / slug
        if target.exists():
            raise CommandError(cli_gettext("%(target)s already exists.") % {"target": target})
        (target / "templates").mkdir(parents=True)
        manifest = {
            "slug": slug, "name": options["name"] or re.sub(r"_+", " ", slug).title(), "version": "0.1.0", "description": "Describe your plugin here.",
            "author": options["author"], "api_version": 1, "ravix": ">=0.9,<2.0", "models": options["models"],
            "capabilities": {f"{slug}.use": "Use the plugin"},
            "settings": [{"key": "greeting", "label": "Welcome message", "type": "text", "default": "Hello!", "max_len": 100}],
            "admin_pages": [{"key": "home", "title": options["name"] or slug, "view": "views:hello", "capability": f"{slug}.use", "icon": "puzzle"}],
        }
        (target / "plugin.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (target / "__init__.py").write_text('"""Plugin. Run no code at import time."""\n', encoding="utf-8")
        (target / "views.py").write_text(VIEWS, encoding="utf-8")
        (target / "templates" / "hello.html").write_text(TEMPLATE, encoding="utf-8")
        if options["models"]:
            (target / "models.py").write_text("from django.db import models\n\n\n# Tables must be named plugin_%s_* (this is the default).\n" % slug, encoding="utf-8")
            (target / "migrations").mkdir()
            (target / "migrations" / "__init__.py").write_text("", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(cli_gettext("The plugin was created in %(target)s.") % {"target": target}))
        self.stdout.write(cli_gettext("Check: python manage.py check_plugins %(slug)s   then review and install it in the panel → Plugins.") % {"slug": slug})
