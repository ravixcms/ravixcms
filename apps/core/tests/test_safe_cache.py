"""The file cache must not stop the site when its folder is missing/read-only, and importing settings must not write to disk."""
import logging
import os
import subprocess
import sys
import tempfile
from io import StringIO
from pathlib import Path

from django.core.cache import caches
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.accounts import throttle
from apps.accounts.tests.test_auth import STRONG, make_user  # noqa: F401
from apps.accounts.tests.test_throttle_fallback import broken_caches
from apps.core import sitehealth
from apps.core.cache import SafeFileBasedCache

ROOT = Path(__file__).resolve().parents[3]


def safe_caches(with_fallback=True):
    conf, blocker = broken_caches(with_fallback)
    conf["default"]["BACKEND"] = "apps.core.cache.SafeFileBasedCache"
    return conf, blocker


class SafeFileBasedCacheTests(SimpleTestCase):
    def setUp(self):
        self.blocker = tempfile.NamedTemporaryFile(delete=False)
        self.blocker.close()
        self.addCleanup(os.unlink, self.blocker.name)
        self.broken_dir = os.path.join(self.blocker.name, "cache")  # below a regular file: the folder can never be created

    def test_constructing_does_not_raise_or_write(self):
        c = SafeFileBasedCache(self.broken_dir, {})
        self.assertTrue(c.broken)
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "not", "yet")
            SafeFileBasedCache(target, {})
            self.assertTrue(os.path.isdir(target))  # created lazily by the first use at the latest; here by _createdir on a writable path

    def test_operations_never_raise_when_the_folder_is_broken(self):
        with self.assertLogs("apps.core.cache", level=logging.ERROR):
            c = SafeFileBasedCache(self.broken_dir, {})  # the failure is logged once, at the first use
        c.set("a", 1)
        self.assertIsNone(c.get("a"))
        self.assertEqual(c.get("a", "dflt"), "dflt")
        self.assertFalse(c.add("b", 1))
        self.assertFalse(c.has_key("a"))
        self.assertFalse(c.delete("a"))
        c.clear()
        self.assertTrue(c.broken)
        with self.assertRaises(ValueError):  # incr on a missing key keeps Django's contract
            c.incr("zzz")

    def test_works_and_clears_the_flag_when_the_folder_is_fine(self):
        with tempfile.TemporaryDirectory() as d:
            c = SafeFileBasedCache(os.path.join(d, "c"), {})
            c.set("k", "v")
            self.assertEqual(c.get("k"), "v")
            self.assertFalse(c.broken)
            self.assertTrue(c.add("n", 0))
            self.assertEqual(c.incr("n"), 1)

    def test_warning_is_rate_limited(self):
        with self.assertLogs("apps.core.cache", level=logging.ERROR) as cm:
            c = SafeFileBasedCache(self.broken_dir, {})
            for i in range(5):
                c.set(f"k{i}", i)
        self.assertEqual(len(cm.records), 1)


class BrokenSafeCacheSiteTests(TestCase):
    def setUp(self):
        throttle._last_warn = 0.0
        self.caches, self.blocker = safe_caches()
        self.addCleanup(os.unlink, self.blocker)

    def test_login_throttling_uses_the_database_table_and_the_site_stays_up(self):
        user = make_user("sam")
        with override_settings(CACHES=self.caches):
            url = reverse("accounts:login")
            for _ in range(throttle.MAX_ATTEMPTS):
                self.assertEqual(self.client.post(url, {"username": "sam", "password": "wrong"}).status_code, 200)
            r = self.client.post(url, {"username": "sam", "password": STRONG})
            self.assertEqual(r.status_code, 200)  # locked, but no 500 and not silently unthrottled
            self.assertNotIn("_auth_user_id", self.client.session)
            self.assertEqual(self.client.get("/fa/").status_code, 200)
        del user

    def test_health_page_and_preflight_name_the_cache_folder(self):
        with override_settings(CACHES=self.caches):
            caches["default"].set("x", 1)  # records the failure
            [check] = sitehealth.check_cache()
            self.assertEqual(check.status, sitehealth.CRITICAL)
            out = StringIO()
            try:
                call_command("ravix_preflight", stdout=out)
            except SystemExit:
                pass
        self.assertIn("✘", out.getvalue())
        self.assertIn("پوشهٔ کش", out.getvalue())


class SettingsDoNotWriteTests(SimpleTestCase):
    def run_py(self, code, **env):
        base = {"PATH": os.environ.get("PATH", ""), "DJANGO_SECRET_KEY": "k9Qz3vR7tY2wX8mN4bC6hJ1pL5sD0fGaE-uI_oTyVxZrKqWcBnMlHjAeSdFgPz", "DJANGO_ALLOWED_HOSTS": "example.com",
                "DATABASE_URL": "postgres://u:p@localhost/db", "DJANGO_SETTINGS_MODULE": "config.settings.production", "PYTHONDONTWRITEBYTECODE": "1"}
        base.update(env)
        return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=base, capture_output=True, text=True, timeout=120)

    def test_importing_production_settings_and_django_setup_create_no_cache_folder(self):
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "must-not-exist")
            r = self.run_py("import django; django.setup(); from django.conf import settings; settings.INSTALLED_APPS", RAVIX_CACHE_DIR=target)
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            self.assertFalse(os.path.exists(target))

    def test_check_deploy_survives_an_unwritable_cache_folder(self):
        with tempfile.NamedTemporaryFile() as blocker:
            r = self.run_py("import django; django.setup(); from django.core.management import call_command; call_command('check', '--deploy', '--fail-level', 'WARNING')",
                            RAVIX_CACHE_DIR=os.path.join(blocker.name, "cache"))
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
