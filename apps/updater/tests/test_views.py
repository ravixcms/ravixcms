"""پنل، API و دستورهای مدیریتی: دسترسی‌ها، خاموش‌بودن اعمال از وب، نشت‌نکردن اسرار/مسیرها، بازگشت."""
import io
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.updater import pipeline
from apps.updater.models import UpdateRun

from .helpers import PUB, SECRET_TEXT, UpdaterCase


class PanelTests(UpdaterCase):
    def client_for(self, user):
        c = Client()
        c.force_login(user)
        return c

    def test_anonymous_and_low_roles_denied(self):
        self.assertEqual(Client().get(reverse("updater:home")).status_code, 302)
        for role in (Role.USER, Role.AUTHOR):
            u = make_user(f"u_{role}", role)
            self.assertEqual(self.client_for(u).get(reverse("updater:home")).status_code, 403)

    def test_admin_can_view_history_but_not_manage(self):
        self.publish("0.7.0")
        run = UpdateRun.objects.create(component="core", from_version="0.5.0", to_version="0.6.1", status="succeeded")
        c = self.client_for(self.admin)
        self.assertEqual(c.get(reverse("updater:home")).status_code, 200)
        self.assertContains(c.get(reverse("updater:run", args=[run.pk])), "0.6.1")
        self.assertEqual(c.post(reverse("updater:apply", args=["core"])).status_code, 403)
        self.assertEqual(c.post(reverse("updater:rollback", args=[run.pk])).status_code, 403)

    def test_home_shows_new_version_compat_and_history(self):
        self.publish("0.7.0")
        r = self.client_for(self.superadmin).get(reverse("updater:home"))
        self.assertContains(r, "0.7.0")
        self.assertContains(r, "اعتبارسنجی و ارتقا به 0.7.0")

    def test_web_apply_disabled_by_default(self):
        self.publish("0.7.0")
        with override_settings(RAVIN_UPDATE_WEB_APPLY=False):
            c = self.client_for(self.superadmin)
            r = c.post(reverse("updater:apply", args=["core"]), follow=True)
            self.assertContains(r, "خاموش است")
            self.assertEqual(self.disk(), "0.6.1")
            self.assertNotContains(c.get(reverse("updater:home")), "اعتبارسنجی و ارتقا به")

    def test_apply_via_panel_when_enabled_records_run_and_audits(self):
        self.publish("0.7.0")
        c = self.client_for(self.superadmin)
        with mock.patch.object(pipeline, "SubprocessRunner", return_value=self.runner), \
                mock.patch("apps.updater.backup.default_db_backend", return_value=self.db):
            r = c.post(reverse("updater:apply", args=["core"]), {"version": "0.7.0"}, follow=True)
        self.assertEqual(r.status_code, 200)
        run = UpdateRun.objects.first()
        self.assertEqual((run.status, run.started_by), ("succeeded", self.superadmin))
        self.assertContains(r, "راه‌اندازی مجدد")

    def test_failed_apply_shows_error_but_not_secrets(self):
        self.publish("0.7.0", seed=None)
        c = self.client_for(self.superadmin)
        r = c.post(reverse("updater:apply", args=["core"]), follow=True)
        self.assertContains(r, "امضا")
        self.assertEqual(UpdateRun.objects.first().status, "failed")

    def test_pages_never_show_secrets_or_private_keys(self):
        from .helpers import SEED

        with override_settings(SECRET_KEY=SECRET_TEXT + "-django"):
            run = UpdateRun.objects.create(component="core", status="failed", error=pipeline.sanitize(f"x {SECRET_TEXT}-django -----BEGIN " + "PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"))
            c = self.client_for(self.superadmin)
            for url in (reverse("updater:home"), reverse("updater:run", args=[run.pk]), "/api/v1/system/versions/"):
                body = c.get(url).content.decode()
                self.assertNotIn(SECRET_TEXT, body)
                self.assertNotIn(SEED, body)
                self.assertNotIn("PRIVATE KEY", body)

    def test_check_view_refreshes_index(self):
        self.publish("0.7.0")
        r = self.client_for(self.admin).post(reverse("updater:check"), follow=True)
        self.assertContains(r, "اعتبارسنجی شد")

    def test_check_view_reports_bad_source_without_crashing(self):
        with override_settings(RAVIN_UPDATE_DIR=str(self.base / "empty")):
            r = self.client_for(self.admin).get(reverse("updater:home"))
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, "index.json")

    def test_rollback_view(self):
        self.publish("0.7.0")
        up = self.apply()
        c = self.client_for(self.superadmin)
        url = reverse("updater:rollback", args=[up.pk])
        with mock.patch("apps.updater.backup.default_db_backend", return_value=self.db), mock.patch.object(pipeline, "SubprocessRunner", return_value=self.runner), \
                mock.patch("apps.updater.pipeline.versions.app_root", return_value=self.root):
            r = c.post(url, {}, follow=True)
            self.assertContains(r, "تأیید")  # بدون تأیید صریح انجام نمی‌شود
            self.assertEqual(self.disk(), "0.7.0")
            c.post(url, {"confirm_data_loss": "on"}, follow=True)
        self.assertEqual(self.disk(), "0.6.1")

    def test_nav_item_only_for_capable_users(self):
        self.assertContains(self.client_for(self.admin).get("/panel/"), reverse("updater:home"))
        self.assertNotContains(self.client_for(make_user("ed1", Role.AUTHOR)).get("/panel/"), reverse("updater:home"))


class ApiTests(UpdaterCase):
    def test_versions_endpoint(self):
        r = self.client_for(self.admin).get("/api/v1/system/versions/")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["core"]["running"], "0.6.1")
        self.assertEqual(d["contracts"], {"plugin_api": 1, "theme_api": 1})
        self.assertIn("database_schema", d)
        self.assertNotIn("path", str(d["themes"]))
        self.assertNotIn(str(self.root), r.content.decode())

    def test_versions_requires_auth_and_capability(self):
        self.assertIn(Client().get("/api/v1/system/versions/").status_code, (401, 403))
        self.assertEqual(self.client_for(make_user("ed2", Role.AUTHOR)).get("/api/v1/system/versions/").status_code, 403)

    def client_for(self, user):
        c = Client()
        c.force_login(user)
        return c


class CommandTests(UpdaterCase):
    def run_cmd(self, *args):
        out = io.StringIO()
        call_command("ravin_update", *args, stdout=out)
        return out.getvalue()

    def test_status_history_check(self):
        self.publish("0.7.0")
        self.assertIn("0.6.1", self.run_cmd("status"))
        self.assertIn("0.7.0", self.run_cmd("check"))
        self.assertEqual(self.run_cmd("history").strip(), "")

    def test_apply_dry_run_command(self):
        self.publish("0.7.0")
        out = self.run_cmd("apply", "core", "--dry-run")
        self.assertIn("0.7.0", out)
        self.assertEqual(self.disk(), "0.6.1")

    def test_apply_unsigned_gives_command_error(self):
        self.publish("0.7.0", seed=None)
        with self.assertRaises(CommandError):
            self.run_cmd("apply", "core", "--dry-run")

    def test_rollback_requires_flag(self):
        with self.assertRaises(CommandError):
            self.run_cmd("rollback")

    def test_release_keygen_and_build_roundtrip(self):
        import os

        kf = self.base / "release.key"
        out = io.StringIO()
        call_command("ravin_release", "keygen", "--key-file", str(kf), stdout=out)
        pub = out.getvalue().strip().splitlines()[-1]
        seed = kf.read_text()
        self.assertNotIn(seed, out.getvalue())  # کلید خصوصی چاپ نمی‌شود
        if os.name == "posix":
            self.assertEqual(oct(kf.stat().st_mode & 0o777), "0o600")
        with self.assertRaises(FileExistsError):  # کلید موجود هرگز بازنویسی نمی‌شود
            call_command("ravin_release", "keygen", "--key-file", str(kf), stdout=io.StringIO())
        src = self.base / "relsrc"
        from .helpers import make_tree

        make_tree(src, "0.9.0")
        call_command("ravin_release", "core", "--source", str(src), "--out", str(self.dist), "--key-file", str(kf), stdout=io.StringIO())
        with override_settings(RAVIN_TRUSTED_PUBLIC_KEYS=[f"rel:{pub}"]):
            self.assertEqual(self.apply("0.9.0").status, "succeeded")
