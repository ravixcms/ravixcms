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
    help = "یک قالب جدید را از روی قالب پیش‌فرض می‌سازد (themes/<slug>) تا شروع کار ساده باشد."

    def add_arguments(self, parser):
        parser.add_argument("slug", help="نامک قالب: حروف کوچک انگلیسی، عدد، «-» و «_»")
        parser.add_argument("--name", default="", help="نام نمایشی قالب")
        parser.add_argument("--author", default="", help="نام سازنده")
        parser.add_argument("--from", dest="source", default=DEFAULT_THEME, help="قالب مبدأ (پیش‌فرض: default)")

    def handle(self, *args, **options):
        slug = options["slug"]
        if not SLUG_RE.match(slug):
            raise CommandError("نامک نامعتبر است؛ فقط حروف کوچک انگلیسی، عدد، «-» و «_» (حداکثر ۴۹ نویسه).")
        themes = registry.get_themes(refresh=True)
        source = themes.get(options["source"])
        if source is None:
            raise CommandError(f"قالب مبدأ «{options['source']}» پیدا نشد.")
        if slug in themes:
            raise CommandError(f"قالبی با نامک «{slug}» از قبل وجود دارد.")
        dirs = [Path(d) for d in getattr(settings, "RAVIN_THEME_DIRS", [])]
        if not dirs:
            raise CommandError("RAVIN_THEME_DIRS خالی است.")
        target = dirs[0] / slug
        if target.exists():
            raise CommandError(f"مسیر {target} از قبل وجود دارد.")
        shutil.copytree(source.path, target, ignore=shutil.ignore_patterns("__pycache__", "*.png", "*.jpg", "*.webp"))
        manifest_path = target / THEME_MANIFEST
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        data.update(
            slug=slug,
            name=options["name"] or re.sub(r"[-_]+", " ", slug).title(),
            version="0.1.0",
            author=options["author"] or data.get("author", ""),
            description="قالب جدید بر پایهٔ قالب «%s»؛ این توضیح را ویرایش کنید." % source.slug,
        )
        data.pop("screenshot", None)
        manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"قالب «{slug}» در {target} ساخته شد."))
        self.stdout.write(f"بررسی: python manage.py check_themes {slug}")
