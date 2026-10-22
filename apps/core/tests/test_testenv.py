"""محیط آزمون: فایل‌های آزمون هرگز در media/ واقعی پروژه نمی‌ماند؛ HSTS preload پیش‌فرض خاموش است."""
import os
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class TestEnvironmentTests(SimpleTestCase):
    def test_media_root_is_temporary(self):
        self.assertNotEqual(Path(settings.MEDIA_ROOT).resolve(), (Path(settings.BASE_DIR) / "media").resolve())
        self.assertTrue(Path(settings.MEDIA_ROOT).is_dir())

    def _hsts(self, **extra):
        import json
        import subprocess
        import sys

        env = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_HSTS", "DJANGO_SETTINGS"))}
        env.update(DJANGO_SETTINGS_MODULE="config.settings.production", DJANGO_SECRET_KEY="k" * 50, DJANGO_ALLOWED_HOSTS="example.com",
                   RAVIX_ALLOW_SQLITE_IN_PRODUCTION="true", **extra)
        code = "import json;from django.conf import settings as s;print(json.dumps([s.SECURE_HSTS_PRELOAD,s.SECURE_HSTS_INCLUDE_SUBDOMAINS]))"
        out = subprocess.run([sys.executable, "-I", "-c", "import sys;sys.path.insert(0,%r);" % str(settings.BASE_DIR) + code], env=env,
                             capture_output=True, text=True, timeout=60, cwd=str(settings.BASE_DIR))
        self.assertEqual(out.returncode, 0, out.stderr[-500:])
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_hsts_preload_off_by_default_and_opt_in(self):
        self.assertEqual(self._hsts(), [False, False])
        self.assertEqual(self._hsts(DJANGO_HSTS_PRELOAD="true", DJANGO_HSTS_INCLUDE_SUBDOMAINS="true"), [True, True])
