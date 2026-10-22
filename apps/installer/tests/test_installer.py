import os
import stat
import tempfile
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.permissions import Role
from apps.core import regional
from apps.core.models import SiteSettings
from apps.installer import services, token

GOOD = {"site_name": "سایت من", "language": "fa", "timezone": "Europe/Berlin", "username": "boss",
        "email": "Boss@Example.com", "password1": "Zq9#kLm2vX!pe", "password2": "Zq9#kLm2vX!pe"}


class InstallerBase(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ravix-inst-")
        self.tok_file = Path(self.tmp) / "install_token"
        self._ov = override_settings(RAVIX_INSTALLER_ENABLED=True, RAVIX_INSTALL_TOKEN="tok-123", DEBUG=False,
                                     RAVIX_INSTALL_TOKEN_FILE=self.tok_file)
        self._ov.enable()
        self.addCleanup(self._ov.disable)
        services.reset_cache()
        self.addCleanup(services.reset_cache)
        self.client = Client()

    def post(self, **over):
        data = {**GOOD, "install_token": "tok-123", **over}
        return self.client.post("/install/", data)


class GateTests(InstallerBase):
    def test_everything_redirects_to_installer_until_installed(self):
        for path in ("/", "/panel/", "/accounts/login/", "/anything/"):
            r = self.client.get(path)
            self.assertEqual((r.status_code, r["Location"]), (302, "/install/"), path)

    def test_healthz_and_static_are_exempt(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)

    def test_install_page_renders_with_token_field(self):
        r = self.client.get("/install/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'name="install_token"')
        self.assertContains(r, 'name="timezone"')
        self.assertContains(r, 'name="language"')

    def test_no_token_field_in_debug(self):
        with override_settings(DEBUG=True):
            self.assertNotContains(self.client.get("/install/"), 'name="install_token"')

    def test_disabled_installer_never_redirects(self):
        with override_settings(RAVIX_INSTALLER_ENABLED=False):
            self.assertEqual(self.client.get("/install/").status_code, 404)
            self.assertNotEqual(self.client.get("/panel/").get("Location"), "/install/")


class InstallFlowTests(InstallerBase):
    def test_successful_install(self):
        r = self.post()
        self.assertRedirects(r, "/panel/", fetch_redirect_response=False)
        u = User.objects.get(username="boss")
        self.assertEqual((u.role, u.email, u.is_superuser), (Role.SUPER_ADMIN, "boss@example.com", True))
        self.assertTrue(u.check_password(GOOD["password1"]))
        s = SiteSettings.objects.get(pk=1)
        self.assertEqual((s.site_name, s.language, s.timezone, s.admin_email), ("سایت من", "fa", "Europe/Berlin", "boss@example.com"))
        self.assertIsNotNone(s.installed_at)
        self.assertEqual(self.client.session["_auth_user_id"], str(u.pk))  # وارد شده است

    def test_locked_after_install(self):
        self.post()
        self.assertEqual(Client().get("/install/").status_code, 404)
        self.assertEqual(Client().post("/install/", {**GOOD, "install_token": "tok-123", "username": "evil", "email": "e@e.com"}).status_code, 404)
        self.assertFalse(User.objects.filter(username="evil").exists())
        self.assertNotEqual(Client().get("/").get("Location"), "/install/")  # گیت باز شد

    def test_service_refuses_second_install(self):
        self.post()
        with self.assertRaises(services.AlreadyInstalled):
            services.perform_install(site_name="x", language="fa", timezone_name="UTC", username="b2", email="b2@e.com", password="Zq9#kLm2vX!pe")

    def test_wrong_or_missing_token_creates_nothing(self):
        for t in ("wrong", ""):
            r = self.post(install_token=t)
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, "توکن نصب نادرست است")
        self.assertEqual(User.objects.count(), 0)
        self.assertIsNone(SiteSettings.load().installed_at)

    def test_password_mismatch_and_weak(self):
        self.assertContains(self.post(password2="other"), "دو رمز عبور یکسان نیستند")
        self.assertContains(self.post(password1="12345678", password2="12345678"), "error-text")
        self.assertEqual(User.objects.count(), 0)

    def test_bad_timezone_and_language_rejected(self):
        self.assertEqual(self.post(timezone="Mars/Base").status_code, 200)
        self.assertEqual(self.post(language="xx").status_code, 200)
        self.assertEqual(User.objects.count(), 0)

    def test_existing_site_with_users_is_never_installable(self):
        User.objects.create_user("old", "old@e.com", "pw-Aa1!xyz")
        self.assertTrue(services.is_installed())
        self.assertIsNotNone(SiteSettings.objects.get(pk=1).installed_at)  # ثبت شد
        self.assertEqual(self.client.get("/install/").status_code, 404)

    def test_failed_install_leaves_no_partial_state(self):
        from unittest import mock

        real_save = services.SiteSettings.save

        def flaky(obj, *a, **k):
            if obj.site_name == GOOD["site_name"]:  # فقط ذخیرهٔ نهایی نصب خراب شود (پس از ساخت کاربر)
                raise RuntimeError("boom")
            return real_save(obj, *a, **k)

        with mock.patch.object(services.SiteSettings, "save", flaky):
            r = self.post()
        self.assertContains(r, "نصب با خطا روبه‌رو شد")
        self.assertEqual(User.objects.count(), 0)
        self.assertIsNone(SiteSettings.objects.get(pk=1).installed_at)


class TokenTests(InstallerBase):
    def test_generated_token_file_is_private_and_removed_after_install(self):
        with override_settings(RAVIX_INSTALL_TOKEN=""):
            with self.assertLogs("ravix.installer", "WARNING") as cm:
                value = token.expected_token()
            self.assertTrue(self.tok_file.exists())
            self.assertEqual(stat.S_IMODE(self.tok_file.stat().st_mode), 0o600)
            self.assertIn(value, cm.output[0])
            self.assertEqual(token.expected_token(), value)  # پایدار
            self.assertTrue(token.check_token(value))
            self.assertFalse(token.check_token("nope"))
            services.perform_install(site_name="x", language="fa", timezone_name="UTC", username="b1", email="b1@e.com", password="Zq9#kLm2vX!pe")
            self.assertFalse(self.tok_file.exists())


class CommandTests(InstallerBase):
    def run_install(self, **env):
        out = StringIO()
        with __import__("unittest").mock.patch.dict(os.environ, env):
            call_command("ravix_install", "--username=cli", "--email=cli@e.com", "--password-env=RAVIX_T_PW",
                         "--site-name=CLI", "--timezone=UTC", stdout=out)
        return out.getvalue()

    def test_cli_install_then_locked(self):
        self.assertIn("نصب کامل شد", self.run_install(RAVIX_T_PW="Zq9#kLm2vX!pe"))
        self.assertEqual(SiteSettings.objects.get(pk=1).timezone, "UTC")
        with self.assertRaises(CommandError):
            self.run_install(RAVIX_T_PW="Zq9#kLm2vX!pe")

    def test_cli_rejects_weak_or_missing_password(self):
        with self.assertRaises(CommandError):
            self.run_install(RAVIX_T_PW="123")
        with self.assertRaises(CommandError):
            self.run_install()
        self.assertEqual(User.objects.count(), 0)

    def test_token_command(self):
        out = StringIO()
        call_command("ravix_install_token", stdout=out)
        self.assertEqual(out.getvalue().strip(), "tok-123")
        self.run_install(RAVIX_T_PW="Zq9#kLm2vX!pe")
        out = StringIO()
        call_command("ravix_install_token", stdout=out)
        self.assertIn("قبلاً", out.getvalue())


class RegionalTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("adm", "adm@e.com", "Zq9#kLm2vX!pe")

    def test_regional_for_defaults_and_invalid_values(self):
        self.assertEqual(regional.regional_for(SiteSettings.load()), ("fa", "Asia/Tehran"))
        s = SiteSettings.load()
        s.timezone, s.language = "Nope/Zone", "zz"
        self.assertEqual(regional.regional_for(s), ("fa", "Asia/Tehran"))
        self.assertEqual(regional.regional_for(None), ("fa", "Asia/Tehran"))

    def test_middleware_activates_site_timezone_and_restores(self):
        from types import SimpleNamespace

        from apps.core.middleware import SiteRegionalMiddleware

        s = SiteSettings.load()
        s.timezone = "Asia/Tokyo"
        s.save()
        seen = []
        mw = SiteRegionalMiddleware(lambda req: seen.append(timezone.get_current_timezone_name()) or None)
        req = SimpleNamespace()
        mw(req)
        self.assertEqual(seen, ["Asia/Tokyo"])
        self.assertEqual(req.site_settings.timezone, "Asia/Tokyo")  # برای context processor نگه داشته شد
        self.assertEqual(timezone.get_current_timezone_name(), "Asia/Tehran")

    def test_no_extra_query_for_html_pages(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.client.force_login(self.admin)
        with CaptureQueriesContext(connection) as ctx:
            self.client.get("/panel/")  # صفحهٔ HTML بدون ویوی SiteSettings: middleware + context processor باید یک خواندن مشترک داشته باشند
        loads = [q for q in ctx.captured_queries if 'FROM "core_sitesettings"' in q["sql"] and "SELECT" in q["sql"]]
        self.assertEqual(len(loads), 1, [q["sql"][:90] for q in loads])

    def test_settings_form_saves_timezone_rejects_invalid_and_keeps_when_omitted(self):
        self.client.force_login(self.admin)
        base = {"site_name": "S", "registration_open": "on", "comments_require_approval": "on"}
        r = self.client.post("/panel/settings/", {**base, "language": "fa", "timezone": "Mars/Base"})
        self.assertEqual(r.status_code, 200)
        self.client.post("/panel/settings/", {**base, "language": "fa", "timezone": "Europe/Paris"})
        self.assertEqual(SiteSettings.objects.get(pk=1).timezone, "Europe/Paris")
        self.client.post("/panel/settings/", {**base, "site_name": "S2"})  # کلاینت قدیمی: بدون زبان/منطقه
        s = SiteSettings.objects.get(pk=1)
        self.assertEqual((s.site_name, s.timezone, s.language), ("S2", "Europe/Paris", "fa"))
