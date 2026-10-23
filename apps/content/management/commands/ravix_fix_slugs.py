from apps.core.clilang import cli_gettext
from django.core.management.base import BaseCommand

from apps.audit.models import AuditLog
from apps.content import slug_audit
from apps.content.models import Page, Post, Redirect


class Command(BaseCommand):
    help = (cli_gettext("Finds pages and posts whose slug is a language code (fa, en, de …), renames them with a suffix (for example de-page), creates a 301 redirect and records it in the audit log. --dry-run only reports."))

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help=cli_gettext("Report only; nothing is changed"))

    def handle(self, *args, **options):
        changes = slug_audit.fix(Page, Post, Redirect, AuditLog, dry_run=options["dry_run"])
        if not changes:
            self.stdout.write(cli_gettext("No page or post has a language code as its slug."))
            return
        verb = cli_gettext("will be changed") if options["dry_run"] else cli_gettext("changed")
        for c in changes:
            self.stdout.write(f"{c.kind} #{c.pk} «{c.title}»: {c.old_path} ← {c.new_path} ({verb})")
