"""ابزار مشترک تست‌های قالب: نصب موقت قالب در یک مسیر جداگانه (بدون دست‌زدن به پوشه themes/ پروژه)."""
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import override_settings

BASE_THEMES = Path(settings.BASE_DIR) / "themes"


class ThemeDirMixin:
    """هر تست یک پوشه موقت قالب‌ها دارد که کنار پوشه اصلی themes/ ثبت می‌شود."""

    def setUp(self):
        super().setUp()
        self.extra_dir = Path(tempfile.mkdtemp(prefix="ravin-test-themes-"))
        self._override = override_settings(RAVIN_THEME_DIRS=[BASE_THEMES, self.extra_dir])
        self._override.enable()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self._override.disable()
        shutil.rmtree(self.extra_dir, ignore_errors=True)

    def install_theme(self, slug="custom", *, source="default", edit=None):
        """قالب ``source`` را به‌عنوان ``slug`` در مسیر موقت کپی می‌کند. ``edit(path)`` اجازه خراب‌کردن آن را می‌دهد."""
        import json

        from apps.themes import registry

        target = self.extra_dir / slug
        shutil.copytree(BASE_THEMES / source, target)
        manifest = target / "theme.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["slug"] = slug
        data["name"] = f"قالب {slug}"
        data.pop("screenshot", None)
        manifest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        if edit:
            edit(target)
        registry.clear_cache()
        return target
