import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import Client, TransactionTestCase, override_settings

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.plugins import registry, services, state
from apps.plugins.integrity import fingerprint

REPO_PLUGINS = Path(settings.BASE_DIR) / "plugins"

DEFAULT_VIEWS = '''from django.http import HttpResponse


def home(request, api):
    return HttpResponse("HOME:" + str(api.setting("greeting", "")))


def ping(request, api):
    return HttpResponse("PONG")
'''


def base_manifest(slug, **over):
    data = {
        "slug": slug, "name": f"افزونه {slug}", "version": "1.0.0", "description": "آزمایشی", "api_version": 1, "ravix": ">=0.5.0,<1.0.0",
        "capabilities": {f"{slug}.use": "استفاده"},
        "settings": [{"key": "greeting", "label": "پیام", "type": "text", "default": "hi", "max_len": 20}],
        "admin_pages": [{"key": "home", "title": f"صفحه {slug}", "view": "views:home", "capability": f"{slug}.use"}],
        "public_routes": [{"path": "ping", "view": "views:ping"}],
    }
    data.update(over)
    return data


class PluginTestCase(TransactionTestCase):
    """هر تست پوشهٔ موقت افزونه دارد. TransactionTestCase چون مهاجرت در SQLite داخل تراکنش ممکن نیست."""

    copy_samples = False

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ravix-plugins-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        if self.copy_samples:
            for name in ("contact_form", "seo_toolkit"):
                shutil.copytree(REPO_PLUGINS / name, self.tmp / name, ignore=shutil.ignore_patterns("__pycache__"))
        override = override_settings(RAVIX_PLUGIN_DIRS=[self.tmp], RAVIX_PLUGINS_ENABLED=True, RAVIX_PLUGINS_SAFE_MODE=False)
        override.enable()
        self.addCleanup(override.disable)
        patcher = mock.patch.object(registry, "VERIFY_INTERVAL", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        cache.clear()
        registry.reset_for_tests()
        self.addCleanup(self._cleanup)
        self.admin = make_user("boss", Role.ADMIN)
        self.superadmin = make_user("root", Role.SUPER_ADMIN)
        self.author = make_user("writer", Role.AUTHOR)
        self.client = Client()

    def _cleanup(self):
        from django.apps import apps
        from django.core.management import call_command

        for label in [l for l in apps.app_configs if l.startswith("plugin_")]:
            try:  # جدول‌های ساخته‌شده توسط تست را پاک می‌کند تا به تست بعدی نشت نکند
                call_command("migrate", label, "zero", interactive=False, verbosity=0)
            except Exception:  # noqa: BLE001
                pass
        registry.reset_for_tests()

    # --- ابزارها
    def write_plugin(self, slug, manifest=None, files=None, *, views=DEFAULT_VIEWS, init=""):
        root = self.tmp / slug
        root.mkdir(parents=True, exist_ok=True)
        data = manifest if manifest is not None else base_manifest(slug)
        (root / "plugin.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        (root / "__init__.py").write_text(init, encoding="utf-8")
        (root / "views.py").write_text(views, encoding="utf-8")
        for rel, content in (files or {}).items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return root

    def fp(self, slug):
        return fingerprint(self.tmp / slug)

    def install(self, slug, user=None):
        return services.install(slug, user or self.admin, self.fp(slug))

    def install_activate(self, slug, user=None):
        self.install(slug, user)
        return services.activate(slug, user or self.admin)

    def login(self, user):
        self.client.force_login(user)
        return self.client

    def invalidate(self):
        state.invalidate()
