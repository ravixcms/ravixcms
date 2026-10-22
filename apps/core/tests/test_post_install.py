"""بعد از نصب: محتوای نمونه، راهنمای داشبورد، صفحه‌های خطا و «سلامت سایت»."""
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.content import samples
from apps.content.models import Page, Post, PublishStatus
from apps.core import sitehealth
from apps.core.models import SiteSettings
from apps.themes.models import ThemeConfig


class SampleContentTests(TestCase):
    def test_create_is_idempotent_and_public(self):
        admin = make_user("boss", Role.SUPER_ADMIN)
        self.assertTrue(samples.create_samples(admin)["created"])
        self.assertFalse(samples.create_samples(admin)["created"])
        self.assertEqual(Post.objects.filter(is_sample=True).count(), 1)
        self.assertEqual(Page.objects.filter(is_sample=True).count(), 2)
        c = Client()
        self.assertEqual(c.get("/about/").status_code, 200)
        self.assertEqual(c.get("/contact/").status_code, 200)
        self.assertContains(c.get("/"), "درباره ما")  # در منوی سربرگ

    def test_edit_clears_sample_flag_and_delete_keeps_edited(self):
        admin = make_user("boss", Role.SUPER_ADMIN)
        samples.create_samples(admin)
        c = Client()
        c.force_login(admin)
        about = Page.objects.get(slug="about")
        r = c.post(reverse("content:page_update", args=[about.pk]),
                   {"title": "درباره ما", "slug": "about", "body": "متن واقعی من", "status": "published", "seo_title": "", "seo_description": ""})
        self.assertEqual(r.status_code, 302)
        about.refresh_from_db()
        self.assertFalse(about.is_sample)
        r = c.post(reverse("dashboard:samples_delete"))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Page.objects.filter(slug="about").exists())
        self.assertFalse(Page.objects.filter(slug="contact").exists())
        self.assertFalse(Post.objects.exists())
        self.assertEqual(Client().get("/contact/").status_code, 404)

    def test_delete_requires_permission_and_post(self):
        make_user("boss", Role.SUPER_ADMIN)
        author = make_user("writer", Role.AUTHOR)
        c = Client()
        c.force_login(author)
        self.assertEqual(c.post(reverse("dashboard:samples_delete")).status_code, 403)
        admin = make_user("adm", Role.ADMIN)
        c.force_login(admin)
        self.assertEqual(c.get(reverse("dashboard:samples_delete")).status_code, 405)

    def test_installer_creates_samples(self):
        from apps.installer import services

        services.reset_cache()
        self.addCleanup(services.reset_cache)
        services.perform_install(site_name="س", language="fa", timezone_name="Asia/Tehran", username="boss", email="b@example.com",
                                 password="Zq9#kLm2vX!pe")
        self.assertEqual(Post.objects.filter(is_sample=True).count(), 1)
        self.assertEqual(Page.objects.filter(is_sample=True).count(), 2)


class OnboardingTests(TestCase):
    def setUp(self):
        self.admin = make_user("adm", Role.ADMIN)
        self.c = Client()
        self.c.force_login(self.admin)
        self.url = reverse("dashboard:home")

    def test_card_shown_then_steps_autodetect(self):
        r = self.c.get(self.url)
        self.assertContains(r, "شروع سریع")
        self.assertContains(r, "لوگوی سایت")
        Post.objects.create(title="واقعی", body="x", status=PublishStatus.PUBLISHED)
        self.assertContains(self.c.get(self.url), "۱ از ۳ گام")
        Post.objects.create(title="نمونه", body="x", status=PublishStatus.PUBLISHED, is_sample=True)
        self.assertContains(self.c.get(self.url), "۱ از ۳ گام")  # نمونه حساب نمی‌شود

    def test_hidden_when_all_done_or_dismissed(self):
        r = self.c.post(reverse("dashboard:onboarding_dismiss"))
        self.assertEqual(r.status_code, 302)
        self.assertNotContains(self.c.get(self.url), "شروع سریع")
        SiteSettings.objects.update(onboarding_dismissed=False)
        site = SiteSettings.load()
        site.logo = "site/x.png"
        site.save()
        Post.objects.create(title="واقعی", body="x", status=PublishStatus.PUBLISHED)
        ThemeConfig.load()
        ThemeConfig.objects.update(published_at=timezone.now())
        self.assertNotContains(self.c.get(self.url), "شروع سریع")

    def test_not_visible_to_plain_user_and_dismiss_forbidden(self):
        user = make_user("plain", Role.USER)
        c = Client()
        c.force_login(user)
        self.assertNotContains(c.get(self.url), "شروع سریع")
        self.assertEqual(c.post(reverse("dashboard:onboarding_dismiss")).status_code, 403)


class ErrorPageTests(TestCase):
    def test_404_public_is_persian_themed(self):
        r = Client().get("/no/such/thing/")
        self.assertEqual(r.status_code, 404)
        self.assertContains(r, "این صفحه پیدا نشد", status_code=404)
        self.assertContains(r, "error-box", status_code=404)

    def test_500_is_standalone_without_db_or_static(self):
        from django.test import RequestFactory

        from apps.core import views

        with mock.patch("django.db.backends.utils.CursorWrapper.execute", side_effect=RuntimeError("db down")):
            r = views.server_error(RequestFactory().get("/"))
        self.assertEqual(r.status_code, 500)
        body = r.content.decode()
        self.assertIn("مشکلی پیش آمد", body)
        self.assertIn("۵۰۰", body)
        self.assertNotIn("/static/", body)
        self.assertNotIn("<link", body)

    def test_500_falls_back_if_template_fails(self):
        from django.test import RequestFactory

        from apps.core import errorpages

        with mock.patch.object(errorpages, "render_to_string", side_effect=RuntimeError):
            r = errorpages.standalone_response(RequestFactory().get("/"), 500, "عنوان", "پیام")
        self.assertEqual(r.status_code, 500)
        self.assertIn("عنوان", r.content.decode())

    def test_maintenance_page_html_and_plain(self):
        from apps.updater.middleware import reset_cache
        from apps.updater.models import UpdateLock

        UpdateLock.objects.create(name="global", holder="h", operation="apply-core")
        reset_cache()
        self.addCleanup(reset_cache)
        r = Client().get("/")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r["Retry-After"], "30")
        self.assertIn("text/html", r["Content-Type"])
        self.assertContains(r, "سایت در حال تعمیر است", status_code=503)
        self.assertContains(r, 'http-equiv="refresh"', status_code=503)
        r = Client().get("/", HTTP_ACCEPT="application/json")
        self.assertEqual(r.status_code, 503)
        self.assertIn("text/plain", r["Content-Type"])


class SiteHealthTests(TestCase):
    def setUp(self):
        cache.clear()
        self.url = reverse("core:site_health")

    def test_access_control(self):
        self.assertEqual(Client().get(self.url).status_code, 302)
        c = Client()
        c.force_login(make_user("plain", Role.USER))
        self.assertEqual(c.get(self.url).status_code, 403)
        c.force_login(make_user("writer", Role.AUTHOR))
        self.assertEqual(c.get(self.url).status_code, 403)
        c.force_login(make_user("adm", Role.ADMIN))
        r = c.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "سلامت سایت")

    def test_page_renders_all_groups(self):
        c = Client()
        c.force_login(make_user("adm", Role.ADMIN))
        body = c.get(self.url).content.decode()
        for needle in ("HTTPS", "ایمیل", "کش", "پشتیبان", "نسخه"):
            self.assertIn(needle, body)

    @override_settings(DEBUG=False, SITE_URL="", EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_critical_https_and_email(self):
        by = {c.key: c for c in sitehealth.run_all(None)}
        self.assertEqual(by["https"].status, sitehealth.CRITICAL)
        self.assertEqual(by["email"].status, sitehealth.CRITICAL)
        self.assertTrue(by["https"].fix)

    @override_settings(DEBUG=False, SITE_URL="https://example.com", EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
                       EMAIL_HOST="smtp.example.com", DEFAULT_FROM_EMAIL="Site <hi@example.com>")
    def test_good_https_and_email(self):
        by = {c.key: c for c in sitehealth.run_all(None)}
        self.assertEqual(by["https"].status, sitehealth.GOOD)
        self.assertEqual(by["email"].status, sitehealth.GOOD)

    def test_cache_check_detects_broken_cache(self):
        self.assertEqual(sitehealth.check_cache()[0].status in (sitehealth.GOOD, sitehealth.RECOMMENDED), True)
        with mock.patch("django.core.cache.cache.set", side_effect=ConnectionError):
            self.assertEqual(sitehealth.check_cache()[0].status, sitehealth.CRITICAL)

    def test_timers_unknown_without_systemd_and_critical_when_inactive(self):
        with mock.patch("apps.core.management.commands.ravix_preflight.systemd_unit_state", return_value=None):
            t = [c for c in sitehealth.check_timers() if c.key.endswith(".timer")]
            self.assertTrue(t and all(c.status == sitehealth.UNKNOWN for c in t))
        with mock.patch("apps.core.management.commands.ravix_preflight.systemd_unit_state", return_value=("disabled", "inactive")):
            t = [c for c in sitehealth.check_timers() if c.key.endswith(".timer")]
            self.assertTrue(all(c.status == sitehealth.CRITICAL for c in t))
        with mock.patch("apps.core.management.commands.ravix_preflight.systemd_unit_state", return_value=("enabled", "active")):
            t = [c for c in sitehealth.check_timers() if c.key.endswith(".timer")]
            self.assertTrue(all(c.status == sitehealth.GOOD for c in t))

    def test_overdue_scheduled_post_flagged(self):
        Post.objects.create(title="p", body="x", status=PublishStatus.SCHEDULED, publish_at=timezone.now() - timedelta(hours=1))
        with mock.patch("apps.core.management.commands.ravix_preflight.systemd_unit_state", return_value=None):
            keys = {c.key for c in sitehealth.check_timers()}
        self.assertIn("scheduled_overdue", keys)

    def test_backup_ages(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.assertEqual(sitehealth.check_backup([d])[0].status, sitehealth.CRITICAL)  # پوشه هست، پشتیبان نیست
            self.assertEqual(sitehealth.check_backup([d / "nope"])[0].status, sitehealth.UNKNOWN)
            (d / "ravix-20260101-000000.sha256").write_text("x")
            self.assertEqual(sitehealth.check_backup([d])[0].status, sitehealth.GOOD)
            now = timezone.now()
            self.assertEqual(sitehealth.check_backup([d], now=now + timedelta(hours=40))[0].status, sitehealth.RECOMMENDED)
            self.assertEqual(sitehealth.check_backup([d], now=now + timedelta(days=9))[0].status, sitehealth.CRITICAL)

    def test_docker_last_ok_marker(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".last_ok").write_text("2026")
            self.assertEqual(sitehealth.check_backup([Path(d)])[0].status, sitehealth.GOOD)

    def test_versions_checks_ok(self):
        by = {c.key: c for c in sitehealth.check_versions()}
        self.assertEqual(by["migrations"].status, sitehealth.GOOD)
        self.assertEqual(by["python"].status, sitehealth.GOOD)

    def test_one_failing_check_does_not_break_page(self):
        c = Client()
        c.force_login(make_user("adm", Role.ADMIN))
        with mock.patch.object(sitehealth, "check_cache", side_effect=RuntimeError("boom")):
            r = c.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "یکی از بررسی‌ها اجرا نشد")

    def test_summary_scoring(self):
        mk = lambda s: sitehealth.Check("k", "g", "t", s, "d")  # noqa: E731
        self.assertEqual(sitehealth.summarize([mk("good")] * 2)["tone"], "good")
        self.assertEqual(sitehealth.summarize([mk("good"), mk("recommended")])["score"], 75)
        self.assertEqual(sitehealth.summarize([mk("good"), mk("critical")])["tone"], "critical")

    def test_test_email_goes_to_own_address_only_and_is_throttled(self):
        adm = make_user("adm", Role.ADMIN)
        c = Client()
        c.force_login(adm)
        url = reverse("core:site_health_test_email")
        self.assertEqual(c.get(url).status_code, 405)
        c.post(url, {"to": "victim@example.com"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [adm.email])
        c.post(url)
        self.assertEqual(len(mail.outbox), 1)  # ۳۰ ثانیه محدودیت

    def test_test_email_failure_reported(self):
        c = Client()
        c.force_login(make_user("adm", Role.ADMIN))
        with mock.patch("django.core.mail.EmailMessage.send", side_effect=OSError("smtp down")):
            r = c.post(reverse("core:site_health_test_email"), follow=True)
        self.assertContains(r, "ارسال ناموفق بود")

    def test_nav_item_visible_to_admin(self):
        c = Client()
        c.force_login(make_user("adm", Role.ADMIN))
        self.assertContains(c.get(reverse("dashboard:home")), reverse("core:site_health"))
