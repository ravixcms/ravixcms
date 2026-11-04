"""Puts posts (or pages) that are translations of each other into one translation group.

    python manage.py ravix_link_translations --type post --slugs how-to-start fa-how-to-start --dry-run
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.content import translations
from apps.content.models import Page, Post
from apps.core.clilang import cli_gettext


class Command(BaseCommand):
    help = cli_gettext("Puts posts or pages that are translations of each other into one translation group (at most one per language).")

    def add_arguments(self, parser):
        parser.add_argument("--type", choices=["post", "page"], required=True, help=cli_gettext("What the slugs are: post or page."))
        parser.add_argument("--slugs", nargs="+", required=True, help=cli_gettext("Two or more slugs of the same type, each in a different language."))
        parser.add_argument("--dry-run", action="store_true", default=False, help=cli_gettext("Only report; change nothing."))

    def handle(self, *args, **options):
        if not translations.enabled():
            raise CommandError(cli_gettext("Translations are not available while several sites are enabled (RAVIX_MULTISITE_ENABLED)."))
        model = Post if options["type"] == "post" else Page
        slugs = list(dict.fromkeys(options["slugs"]))
        found = {obj.slug: obj for obj in model.objects.filter(slug__in=slugs)}
        missing = [slug for slug in slugs if slug not in found]
        if missing:
            raise CommandError(cli_gettext("Not found: %(slugs)s") % {"slugs": ", ".join(missing)})
        objs = [found[slug] for slug in slugs]
        for obj in objs:
            self.stdout.write(f"  {obj.language:8} {obj.slug}  ({obj.title})")
        try:
            if options["dry_run"]:
                with transaction.atomic():  # the same checks and writes as the real run, then rolled back
                    group = translations.link(objs)
                    transaction.set_rollback(True)
                self.stdout.write(cli_gettext("Dry run: these %(count)s items could be linked; nothing was changed.") % {"count": len(objs)})
                return
            group = translations.link(objs)
        except translations.TranslationError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(cli_gettext("%(count)s items are now translations of each other (group %(group)s).") % {"count": len(objs), "group": group}))
