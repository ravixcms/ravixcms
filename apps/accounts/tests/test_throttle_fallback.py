"""کش اصلی خراب نباید ورود را بشکند؛ محدودسازی ورود به کش جدولی دیتابیس برمی‌گردد (نه این‌که خاموش شود)."""
import os
import subprocess
import sys
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from apps.accounts import throttle
from apps.core.models import ThrottleCacheEntry

from .test_auth import STRONG, make_user

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def broken_caches(with_fallback=True):
    """کش اصلی فایلی که مسیرش زیر یک «فایل» است (ساخت پوشه ممکن نیست؛ مثل نبودن/قابل‌نوشتن‌نبودن /var/cache/ravix)."""
    blocker = tempfile.NamedTemporaryFile(delete=False)
    blocker.close()
    caches = {"default": {"BACKEND": "django.core.cache.backends.filebased.FileBasedCache", "LOCATION": os.path.join(blocker.name, "cache")}}
    if with_fallback:
        caches["throttle_fallback"] = {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "ravin_cache"}
    return caches, blocker.name


class BrokenCacheLoginTests(TestCase):
    def setUp(self):
        throttle._last_warn = 0.0
        self.caches, self.blocker = broken_caches()
        self.addCleanup(os.unlink, self.blocker)
        self.url = reverse("accounts:login")
        self.user = make_user("ali")

    def post(self, password):
        return self.client.post(self.url, {"username": "ali", "password": password})

    def test_login_page_and_successful_login_work_with_broken_cache(self):
        with override_settings(CACHES=self.caches):
            self.assertEqual(self.client.get(self.url).status_code, 200)
            with self.assertLogs("apps.accounts.throttle", "WARNING"):
                r = self.post(STRONG)
        self.assertEqual(r.status_code, 302)  # ورود موفق، نه ۵۰۰

    def test_failed_login_does_not_500_and_logs_warning(self):
        with override_settings(CACHES=self.caches):
            with self.assertLogs("apps.accounts.throttle", "WARNING") as logs:
                r = self.post("wrong-password")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any("کش اصلی" in m for m in logs.output))

    def test_protection_stays_on_via_database_fallback(self):
        with override_settings(CACHES=self.caches):
            with self.assertLogs("apps.accounts.throttle", "WARNING"):
                for _ in range(throttle.MAX_ATTEMPTS):
                    self.assertEqual(self.post("wrong-password").status_code, 200)
            self.assertGreater(ThrottleCacheEntry.objects.count(), 0)  # شمارنده‌ها در جدول دیتابیس نشسته‌اند
            r = self.post(STRONG)  # حتی رمز درست هم قفل است
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "تعداد تلاش‌های ناموفق زیاد بود")

    def test_warning_is_rate_limited(self):
        with override_settings(CACHES=self.caches):
            with self.assertLogs("apps.accounts.throttle", "WARNING") as logs:
                for _ in range(3):
                    self.post("wrong-password")
        self.assertEqual(len(logs.output), 1)

    def test_healthy_cache_does_not_touch_database_fallback(self):
        self.post("wrong-password")
        self.assertEqual(ThrottleCacheEntry.objects.count(), 0)


class NoFallbackTests(TestCase):
    def test_without_fallback_alias_the_error_is_not_swallowed(self):
        caches, blocker = broken_caches(with_fallback=False)
        self.addCleanup(os.unlink, blocker)
        req = RequestFactory().post("/")
        with override_settings(CACHES=caches), self.assertRaises(OSError):
            throttle.is_locked(req, "ali")

    def test_both_caches_broken_raises(self):
        caches, blocker = broken_caches()
        self.addCleanup(os.unlink, blocker)
        caches["throttle_fallback"] = {"BACKEND": "django.core.cache.backends.filebased.FileBasedCache", "LOCATION": os.path.join(blocker, "x")}
        req = RequestFactory().post("/")
        with override_settings(CACHES=caches), self.assertRaises(OSError):
            throttle.is_locked(req, "ali")


class PreflightCacheTests(TestCase):
    def run_preflight(self, **kw):
        out = StringIO()
        try:
            call_command("ravix_preflight", stdout=out)
        except SystemExit:
            pass
        return out.getvalue()

    def test_reports_working_cache(self):
        self.assertIn("کش اصلی کار می‌کند", self.run_preflight())

    def test_reports_broken_cache_as_error(self):
        caches, blocker = broken_caches()
        self.addCleanup(os.unlink, blocker)
        with override_settings(CACHES=caches):
            out = self.run_preflight()
        self.assertIn("✘", out)
        self.assertIn("پوشهٔ کش", out)
        self.assertIn("کش اصلی کار نمی‌کند", out)

    def test_reports_fallback_table_present(self):
        self.assertIn("جدول کش جایگزین", self.run_preflight())


class ProductionLoggingTests(TestCase):
    """تنظیمات واقعی production: ۵۰۰ باید با traceback کامل در stderr (journald) بیاید."""

    def test_500_traceback_goes_to_stderr(self):
        env = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.production", "DJANGO_SECRET_KEY": "x" * 60,
               "DJANGO_ALLOWED_HOSTS": "example.com", "DATABASE_URL": "postgres://u:XXXX@localhost/db"}
        code = (
            "import logging, logging.config, django\n"
            "from django.conf import settings\n"
            "django.setup()\n"
            "assert settings.CACHES['default']['BACKEND'].endswith('FileBasedCache')\n"
            "assert settings.CACHES['throttle_fallback']['BACKEND'].endswith('DatabaseCache')\n"
            "try:\n    1/0\nexcept ZeroDivisionError:\n    logging.getLogger('django.request').error('Internal Server Error: /x', exc_info=True)\n"
        )
        r = subprocess.run([sys.executable, "-c", code], cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Traceback (most recent call last)", r.stderr)
        self.assertIn("ZeroDivisionError", r.stderr)
