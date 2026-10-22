import json
import re

from django.core.management.base import BaseCommand, CommandError

from apps.plugins import discovery
from apps.plugins.manifest import RESERVED_SLUGS, SLUG_RE

VIEWS = '''from django.http import HttpResponse


def hello(request, api):
    """ویو صفحهٔ مدیریتی: (request, api). api همان PluginAPI است؛ مجوز را هسته پیش از فراخوانی بررسی کرده است."""
    return api.render_admin(request, "hello.html", {"message": api.setting("greeting", "سلام!")}, title=api.name)
'''
TEMPLATE = '''{% extends "plugins/page.html" %}
{% block plugin_content %}<div class="card"><div class="card-body"><p>{{ message }}</p></div></div>{% endblock %}
'''


class Command(BaseCommand):
    help = "اسکلت یک افزونهٔ جدید را در اولین پوشهٔ RAVIN_PLUGIN_DIRS می‌سازد."

    def add_arguments(self, parser):
        parser.add_argument("slug")
        parser.add_argument("--name", default="")
        parser.add_argument("--author", default="")
        parser.add_argument("--models", action="store_true", help="مدل دیتابیس هم داشته باشد")

    def handle(self, *args, slug, **options):
        if not SLUG_RE.match(slug) or slug in RESERVED_SLUGS:
            raise CommandError("نامک نامعتبر یا رزرو است (حروف کوچک انگلیسی، عدد و _، با حرف شروع شود، ۳ تا ۳۱ نویسه).")
        dirs = discovery.plugin_dirs()
        if not dirs:
            raise CommandError("RAVIN_PLUGIN_DIRS خالی است.")
        target = dirs[0] / slug
        if target.exists():
            raise CommandError(f"{target} از قبل وجود دارد.")
        (target / "templates").mkdir(parents=True)
        manifest = {
            "slug": slug, "name": options["name"] or re.sub(r"_+", " ", slug).title(), "version": "0.1.0", "description": "توضیح افزونه را اینجا بنویسید.",
            "author": options["author"], "api_version": 1, "ravin": ">=0.5.0,<1.0.0", "models": options["models"],
            "capabilities": {f"{slug}.use": "استفاده از افزونه"},
            "settings": [{"key": "greeting", "label": "پیام خوش‌آمد", "type": "text", "default": "سلام!", "max_len": 100}],
            "admin_pages": [{"key": "home", "title": options["name"] or slug, "view": "views:hello", "capability": f"{slug}.use", "icon": "puzzle"}],
        }
        (target / "plugin.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (target / "__init__.py").write_text('"""افزونه. هیچ کدی در زمان import اجرا نکنید."""\n', encoding="utf-8")
        (target / "views.py").write_text(VIEWS, encoding="utf-8")
        (target / "templates" / "hello.html").write_text(TEMPLATE, encoding="utf-8")
        if options["models"]:
            (target / "models.py").write_text("from django.db import models\n\n\n# جدول‌ها باید plugin_%s_* باشند (به‌طور پیش‌فرض همین است).\n" % slug, encoding="utf-8")
            (target / "migrations").mkdir()
            (target / "migrations" / "__init__.py").write_text("", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"افزونه در {target} ساخته شد."))
        self.stdout.write(f"بررسی: python manage.py check_plugins {slug}   سپس در پنل ← افزونه‌ها آن را بررسی و نصب کنید.")
