from django.core.management.base import BaseCommand

from apps.audit.models import AuditLog
from apps.content import slug_audit
from apps.content.models import Page, Post, Redirect


class Command(BaseCommand):
    help = ("برگه‌ها و نوشته‌هایی را که نامکشان کد زبان است (fa، en، de …) پیدا و با پسوند (مثلاً de-page) عوض می‌کند، "
            "ریدایرکت ۳۰۱ می‌سازد و در لاگ ممیزی ثبت می‌کند. --dry-run فقط گزارش می‌دهد.")

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="فقط نمایش؛ چیزی تغییر نمی‌کند")

    def handle(self, *args, **options):
        changes = slug_audit.fix(Page, Post, Redirect, AuditLog, dry_run=options["dry_run"])
        if not changes:
            self.stdout.write("نامک هیچ برگه یا نوشته‌ای کد زبان نیست.")
            return
        verb = "تغییر می‌کند" if options["dry_run"] else "تغییر کرد"
        for c in changes:
            self.stdout.write(f"{c.kind} #{c.pk} «{c.title}»: {c.old_path} ← {c.new_path} ({verb})")
