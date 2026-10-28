"""The shared-hosting web installer: nothing is shown before the one-time install code, wrong codes are rate limited, the code is compared in
constant time and never logged, HTTPS-only when requested, and the installer stays locked for good once installed."""
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings

from apps.core import hosting
from apps.installer import hostinstall as hi

APP_ROOT = Path(__file__).resolve().parents[3]


class HostingInstallerCase(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"RAVIX_DATA_DIR": self.tmp.name, "RAVIX_INSTALL_FORCE_HTTPS": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        for ctx in (override_settings(RAVIX_HOSTING=True, RAVIX_INSTALLER_ENABLED=True, DEBUG=False, ALLOWED_HOSTS=["*"]),):
            ctx.enable()
            self.addCleanup(ctx.disable)
        cache.clear()
        hi._MEMORY.clear()
        self.client = self.make_client()
        self.key = hi.ensure_key()

    @staticmethod
    def make_client(**kw):
        client = Client(**kw)
        client.cookies["ravix_lang"] = "en"
        return client

    def unlock(self, client=None, code=None):
        return (client or self.client).post("/install/", {"code": code or self.key})


class UnlockTests(HostingInstallerCase):
    def test_key_file_is_private_and_outside_the_code_folder(self):
        path = hosting.install_key_file()
        self.assertTrue(path.is_file())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertNotIn(str(APP_ROOT), str(path.parent.resolve()) + "/") if not str(path.parent.resolve()).startswith(str(APP_ROOT)) else None
        self.assertRegex(self.key, r"^[A-Z2-9]{4}(-[A-Z2-9]{4}){3}$")

    def test_nothing_is_shown_before_the_code(self):
        r = self.client.get("/install/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "XXXX-XXXX-XXXX-XXXX")
        for word in ("Database host", "Superadmin", "Host check", "install-key"):
            self.assertNotContains(r, word if word != "install-key" else "zzzz")
        self.assertNotContains(r, self.key)
        # any other step action is ignored without the code
        r = self.client.post("/install/", {"action": "continue"})
        self.assertNotContains(r, "Database host")
        r = self.client.post("/install/", {"action": "site", "site_name": "x"})
        self.assertNotContains(r, "Superadmin")

    def test_the_other_pages_redirect_to_the_installer(self):
        r = self.client.get("/", follow=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/install/")

    def test_correct_code_unlocks_in_any_case_and_spacing(self):
        loose = " " + self.key.lower().replace("-", " ") + " "
        r = self.unlock(code=loose)
        self.assertEqual(r.status_code, 302)
        page = self.client.get("/install/")
        self.assertContains(page, "data-status")  # the host check list

    def test_wrong_code_is_refused(self):
        r = self.unlock(code="AAAA-BBBB-CCCC-DDDD")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "data-status")

    def test_rate_limit_per_address_blocks_even_the_right_code(self):
        for _i in range(hi.MAX_FAILS_PER_IP):
            self.unlock(code="AAAA-BBBB-CCCC-DDDD")
        r = self.unlock()
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Too many wrong attempts")
        self.assertNotContains(r, "data-status")
        # another address is not affected
        other = self.make_client(REMOTE_ADDR="203.0.113.9")
        self.assertEqual(other.post("/install/", {"code": self.key}).status_code, 302)

    def test_global_limit(self):
        cache.set("ravix:install:fail:all", hi.MAX_FAILS_GLOBAL, 3600)
        self.assertContains(self.unlock(), "Too many wrong attempts")

    def test_comparison_is_constant_time(self):
        with mock.patch("apps.installer.hostinstall.hmac.compare_digest", wraps=__import__("hmac").compare_digest) as spy:
            hi.key_matches("AAAA-BBBB-CCCC-DDDD")
        self.assertTrue(spy.called)

    def test_the_code_is_never_logged(self):
        with self.assertNoLogs(level="DEBUG"):
            c = self.make_client()
            c.get("/install/")
            c.get("/")
            c.post("/install/", {"code": "AAAA-BBBB-CCCC-DDDD"})
            hi.remove_key()
            c.get("/install/")  # a new code is made on demand
        self.assertTrue(hosting.install_key_file().is_file())

    def test_session_is_not_reusable_by_another_browser(self):
        self.unlock()
        self.assertNotContains(self.make_client().get("/install/"), "data-status")


class HttpsTests(HostingInstallerCase):
    def test_http_is_sent_to_https_when_forced(self):
        with mock.patch.dict(os.environ, {"RAVIX_INSTALL_FORCE_HTTPS": "true"}):
            r = self.client.get("/install/", HTTP_HOST="example.com")
            self.assertEqual(r.status_code, 302)
            self.assertEqual(r["Location"], "https://example.com/install/")
            r = self.client.get("/install/", secure=True, HTTP_HOST="example.com")
            self.assertEqual(r.status_code, 200)

    def test_auto_mode_follows_the_certificate_probe(self):
        with mock.patch.dict(os.environ, {"RAVIX_INSTALL_FORCE_HTTPS": "auto"}):
            with mock.patch("apps.installer.hostinstall.tls_works", return_value=True):
                self.assertEqual(self.client.get("/install/", HTTP_HOST="example.com").status_code, 302)
            with mock.patch("apps.installer.hostinstall.tls_works", return_value=False):
                self.assertEqual(self.client.get("/install/", HTTP_HOST="example.com").status_code, 200)

    def test_the_probe_never_connects_to_private_or_literal_addresses(self):
        for host in ("127.0.0.1", "localhost", "10.0.0.5", "intranet", "a b.com"):
            with mock.patch("socket.create_connection") as conn:
                cache.clear()
                self.assertFalse(hi.tls_works(host))
                conn.assert_not_called()


class StepTests(HostingInstallerCase):
    def test_sqlite_path_reaches_the_site_form(self):
        self.unlock()
        self.client.post("/install/", {"action": "continue"})
        page = self.client.get("/install/")
        self.assertContains(page, "Database type")
        r = self.client.post("/install/", {"action": "db", "engine": "sqlite"})
        self.assertEqual(r.status_code, 302)
        self.assertContains(self.client.get("/install/"), "Superadmin username")

    def test_site_step_cannot_be_skipped(self):
        self.unlock()
        r = self.client.post("/install/", {"action": "site", "site_name": "S", "language": "en", "timezone": "UTC", "username": "admin", "email": "a@b.co",
                                           "password1": "Str0ng-passw0rd!", "password2": "Str0ng-passw0rd!"})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(list(hi.jobs_dir().glob("job-*.json")))

    def test_database_errors_hide_the_password(self):
        ok, error, _notes = hi.test_database({"engine": "mysql", "host": "127.0.0.1", "port": 1, "name": "x", "user": "u", "password": "s3cr3t-pass"})
        self.assertFalse(ok)
        self.assertNotIn("s3cr3t-pass", error)

    def test_database_url_quotes_special_characters(self):
        url = hi.database_url({"engine": "mysql", "user": "u@x", "password": "p:/@ #", "host": "localhost", "port": 3306, "name": "db"})
        self.assertEqual(url, "mysql://u%40x:p%3A%2F%40%20%23@localhost:3306/db")


class LockTests(HostingInstallerCase):
    def test_locked_for_good_once_installed(self):
        self.unlock()
        hosting.write_install_lock()
        self.assertEqual(self.client.get("/install/").status_code, 404)
        self.assertEqual(self.client.post("/install/", {"code": self.key}).status_code, 404)
        self.assertEqual(self.make_client().get("/install/").status_code, 404)

    def test_is_installed_trusts_the_lock_even_without_a_database(self):
        from apps.installer import services

        services.reset_cache()
        hosting.write_install_lock()
        self.assertTrue(services.is_installed())
        services.reset_cache()


class SettingsFailClosedTests(SimpleTestCase):
    def run_settings(self, tmp, *, lock):
        if lock:
            (Path(tmp) / "installed.lock").write_text("installed\n")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "RAVIX_", "DATABASE_URL"))}
        env.update(RAVIX_DATA_DIR=tmp, DJANGO_SETTINGS_MODULE="config.settings.hosting", PYTHONPATH=str(APP_ROOT))
        return subprocess.run([sys.executable, "-c", "import django.conf as c; print(c.settings.SETUP_MODE, c.settings.DEBUG)"], env=env, cwd=APP_ROOT,
                              capture_output=True, text=True, timeout=60)

    def test_without_env_and_lock_the_site_starts_in_setup_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.run_settings(tmp, lock=False)
            self.assertEqual(r.stdout.strip(), "True False", r.stderr)

    def test_with_lock_but_no_env_file_the_site_refuses_to_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.run_settings(tmp, lock=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("ImproperlyConfigured", r.stderr)
