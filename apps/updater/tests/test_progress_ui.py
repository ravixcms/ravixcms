"""Stage 3: the live progress page and its status endpoint, the "update now" button with the pre-check, the dashboard card and the periodic check."""
import json
from unittest import mock

from django.conf import settings
from django.core.cache import caches
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import translation

from apps.updater import jobs, progress, sources
from apps.updater.models import UpdateJob, UpdateRun

from .helpers import UpdaterCase


def _run(status, steps):
    return UpdateRun.objects.create(component="core", from_version="0.9.0", to_version="0.10.0", status=status, steps=steps)


class ProgressModelTests(UpdaterCase):
    def make(self, status, steps):
        run = _run("running", steps)
        return UpdateJob.objects.create(component="core", target_version="0.10.0", status=status, run=run)

    def states(self, job):
        return [p["state"] for p in progress.view(job)["phases"]]

    def test_a_queued_job_has_nothing_done(self):
        job = UpdateJob.objects.create(component="core", status="queued")
        v = progress.view(job)
        self.assertEqual((v["percent"], v["finished"], v["ok"]), (0, False, False))
        self.assertEqual(set(self.states(job)), {"pending"})

    def test_a_running_job_shows_done_active_and_pending(self):
        job = self.make("running", [{"name": "download", "ok": True}, {"name": "backup", "ok": True}, {"name": "swap", "ok": True}])
        self.assertEqual(self.states(job), ["done", "done", "active", "pending", "pending", "pending"])
        self.assertTrue(0 < progress.view(job)["percent"] < 100)

    def test_a_finished_job_is_complete(self):
        job = self.make("succeeded", [{"name": "health", "ok": True}])
        v = progress.view(job)
        self.assertEqual((v["percent"], v["ok"], v["finished"]), (100, True, True))
        self.assertEqual(set(self.states(job)), {"done"})

    def test_a_failure_marks_the_phase_that_failed(self):
        job = self.make("rolled_back", [{"name": "backup", "ok": True}, {"name": "swap", "ok": True}, {"name": "migrate", "ok": False}, {"name": "rollback_files", "ok": True}])
        self.assertEqual(self.states(job), ["done", "done", "done", "failed", "pending", "pending"])
        self.assertTrue(progress.view(job)["rolling_back"])

    def test_a_cancelled_job_has_no_failed_phase(self):
        job = UpdateJob.objects.create(component="core", status="cancelled")
        self.assertNotIn("failed", self.states(job))

    def test_every_phase_label_is_translated_in_all_languages(self):
        for lang in ("fa", "ar", "de", "es", "fr", "pt-br", "ru", "tr", "zh-hans"):
            with translation.override(lang):
                labels = [str(label) for _k, label, _n in progress.PHASES]
                self.assertEqual(len(set(labels)), len(labels), lang)
                self.assertTrue(all(label not in {str(x) for x in ("",)} for label in labels))
            with translation.override("en"):
                english = [str(label) for _k, label, _n in progress.PHASES]
            with translation.override(lang):
                self.assertNotEqual([str(label) for _k, label, _n in progress.PHASES], english, lang)


@override_settings(RAVIX_UPDATE_EXECUTOR="systemd", RAVIX_UPDATE_HEARTBEAT_SECONDS=0)
class ProgressPageTests(UpdaterCase):
    def setUp(self):
        super().setUp()
        ov = override_settings(RAVIX_UPDATE_JOBS_DIR=self.base / "jobs")
        ov.enable()
        self.addCleanup(ov.disable)
        self.c = Client()
        self.c.force_login(self.superadmin)

    def test_queueing_creates_a_job_and_redirects_to_its_page(self):
        self.publish("0.7.0")
        r = self.c.post(reverse("updater:queue"), {"version": "0.7.0"})
        job = UpdateJob.objects.get()
        self.assertRedirects(r, reverse("updater:job", args=[job.pk]))
        self.assertEqual(job.target_version, "0.7.0")

    def test_queue_needs_the_manage_capability(self):
        self.assertEqual(self.client_for(self.admin).post(reverse("updater:queue"), {}).status_code, 403)
        self.assertEqual(UpdateJob.objects.count(), 0)

    def client_for(self, user):
        c = Client()
        c.force_login(user)
        return c

    def test_queue_is_post_only(self):
        self.assertEqual(self.c.get(reverse("updater:queue")).status_code, 405)

    def test_manual_files_need_an_explicit_acknowledgement(self):
        with mock.patch("apps.updater.views.manual_files", return_value={"modified": ["apps/core/x.py"], "added": []}):
            r = self.c.post(reverse("updater:queue"), {"version": "0.7.0"})
            self.assertEqual(UpdateJob.objects.count(), 0)
            self.assertRedirects(r, reverse("updater:home"))
            self.c.post(reverse("updater:queue"), {"version": "0.7.0", "acknowledge": "on"})
            self.assertEqual(UpdateJob.objects.count(), 1)

    def test_a_second_update_is_refused_while_one_is_active(self):
        self.c.post(reverse("updater:queue"), {})
        self.c.post(reverse("updater:queue"), {})
        self.assertEqual(UpdateJob.objects.count(), 1)

    def test_the_status_endpoint_returns_json_and_is_never_cached(self):
        job = jobs.enqueue(None, self.superadmin)
        r = self.c.get(reverse("updater:job_status", args=[job.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Cache-Control"], "no-store")
        data = json.loads(r.content)
        self.assertEqual((data["status"], len(data["phases"])), ("queued", 6))

    def test_the_status_endpoint_answers_during_maintenance(self):
        job = jobs.enqueue(None, self.superadmin)
        from apps.updater import pipeline

        with pipeline.update_lock("apply core"):
            r = self.c.get(reverse("updater:job_status", args=[job.pk]))
            self.assertEqual(r.status_code, 200)
            self.assertEqual(Client().get("/").status_code, 503)

    def test_the_status_endpoint_needs_a_login_and_the_view_capability(self):
        job = jobs.enqueue(None, self.superadmin)
        self.assertEqual(Client().get(reverse("updater:job_status", args=[job.pk])).status_code, 302)

    def test_a_queued_job_can_be_cancelled_but_not_a_running_one(self):
        job = jobs.enqueue(None, self.superadmin)
        self.c.post(reverse("updater:job_cancel", args=[job.pk]))
        job.refresh_from_db()
        self.assertEqual(job.status, "cancelled")
        running = UpdateJob.objects.create(component="core", status="running")
        self.c.post(reverse("updater:job_cancel", args=[running.pk]))
        running.refresh_from_db()
        self.assertEqual(running.status, "running")

    def test_the_page_renders_in_every_language_with_the_right_direction(self):
        job = jobs.enqueue("0.7.0", self.superadmin)
        for lang, rtl in (("fa", True), ("ar", True), ("en", False), ("tr", False), ("ru", False), ("es", False), ("fr", False), ("de", False), ("pt-br", False), ("zh-hans", False)):
            with self.subTest(lang=lang):
                self.c.cookies[settings.LANGUAGE_COOKIE_NAME] = lang
                r = self.c.get(reverse("updater:job", args=[job.pk]))
                self.assertEqual(r.status_code, 200)
                self.assertContains(r, 'id="update-progress"')
                self.assertContains(r, 'dir="rtl"' if rtl else 'dir="ltr"')

    def test_no_inline_script_is_used(self):
        job = jobs.enqueue(None, self.superadmin)
        html = self.c.get(reverse("updater:job", args=[job.pk])).content.decode()
        self.assertIn("js/update-progress.js", html)
        self.assertNotIn("onclick=", html)

    def test_home_offers_update_now_with_notes_in_the_admins_language(self):
        self.publish("0.7.0")
        entry = {"version": "0.7.0", "notes": "Plain", "notes_i18n": {"fa": "یادداشت فارسی", "en": "English notes"}}
        plan = {"current": "0.6.0", "latest": {**entry, "released": "", "migrations": False}, "problems": [], "warnings": [], "can_apply": True}
        for lang, expected in (("fa", "یادداشت فارسی"), ("en", "English notes")):
            with mock.patch("apps.updater.pipeline.plan_core", return_value=plan), mock.patch("apps.updater.views.manual_files", return_value=None):
                self.c.cookies[settings.LANGUAGE_COOKIE_NAME] = lang
                r = self.c.get(reverse("updater:home"))
            self.assertContains(r, expected)
            self.assertContains(r, reverse("updater:queue"))

    def test_home_warns_about_manual_files_and_asks_for_the_checkbox(self):
        self.publish("0.7.0")
        plan = {"current": "0.6.0", "latest": {"version": "0.7.0", "notes": "", "released": "", "migrations": False}, "problems": [], "warnings": [], "can_apply": True}
        with mock.patch("apps.updater.pipeline.plan_core", return_value=plan), mock.patch("apps.updater.views.manual_files", return_value={"modified": ["apps/core/edited.py"], "added": ["apps/extra.py"]}):
            r = self.c.get(reverse("updater:home"))
        self.assertContains(r, "apps/core/edited.py")
        self.assertContains(r, "apps/extra.py")
        self.assertContains(r, 'name="acknowledge"')


class DashboardCardTests(UpdaterCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(caches["throttle_fallback"].delete, sources.KNOWN_KEY)
        caches["throttle_fallback"].delete(sources.KNOWN_KEY)

    def index(self, version="9.0.0"):
        return {"core": [{"version": version, "released": "2026-11-01", "notes": "N", "notes_i18n": {"fa": "نکتهٔ فارسی"}, "migrations": False}], "themes": {}, "plugins": {}}

    def test_no_card_without_a_known_index(self):
        c = Client()
        c.force_login(self.superadmin)
        self.assertNotContains(c.get(reverse("dashboard:home")), "newver-title")

    def test_card_shows_a_known_newer_version_without_any_network_call(self):
        sources.remember(self.index())
        c = Client()
        c.force_login(self.superadmin)
        c.cookies[settings.LANGUAGE_COOKIE_NAME] = "fa"
        with mock.patch.object(sources, "get_source", side_effect=AssertionError("network")):
            r = c.get(reverse("dashboard:home"))
        self.assertContains(r, "newver-title")
        self.assertContains(r, "نکتهٔ فارسی")

    def test_card_hidden_when_not_newer(self):
        sources.remember(self.index("0.0.1"))
        c = Client()
        c.force_login(self.superadmin)
        self.assertNotContains(c.get(reverse("dashboard:home")), "newver-title")

    def test_card_hidden_from_a_user_without_the_view_capability(self):
        from apps.accounts.permissions import Role
        from apps.accounts.tests.test_auth import make_user

        sources.remember(self.index())
        c = Client()
        c.force_login(make_user("editor_x", Role.AUTHOR))
        r = c.get(reverse("dashboard:home"))
        self.assertNotContains(r, "newver-title")


@override_settings(RAVIX_UPDATE_AUTO_CHECK=True)
class AutoCheckTests(UpdaterCase):
    def setUp(self):
        super().setUp()
        caches["throttle_fallback"].delete(sources.KNOWN_KEY)
        self.addCleanup(caches["throttle_fallback"].delete, sources.KNOWN_KEY)

    def test_it_checks_then_stays_quiet_until_the_interval_passes(self):
        self.publish("0.7.0")
        with mock.patch.object(sources, "has_source", return_value=True):
            self.assertEqual(sources.auto_check(), "checked")
            with mock.patch.object(sources, "cached_index", side_effect=AssertionError("must not ask again")):
                self.assertEqual(sources.auto_check(), "fresh")

    def test_it_can_be_turned_off(self):
        with override_settings(RAVIX_UPDATE_AUTO_CHECK=False):
            self.assertEqual(sources.auto_check(), "skipped")

    def test_an_unreachable_source_never_raises(self):
        from apps.updater.errors import UpdateError

        with mock.patch.object(sources, "has_source", return_value=True), mock.patch.object(sources, "cached_index", side_effect=UpdateError("down")):
            self.assertEqual(sources.auto_check(), "unavailable")
