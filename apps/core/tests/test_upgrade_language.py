"""Upgrading a Persian site from a version without multilingual support must not change its language.

The test runner forces LANGUAGE_CODE="fa" for the (Persian-written) suite; here it is put back to the new default ("en") and the CLI
language to its default, which is what a real upgraded installation has when its .env says nothing about languages."""
import importlib
import re
from xml.etree import ElementTree as ET

from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone, translation
from django.utils.translation import trans_real

from apps.accounts.models import Role, User
from apps.content.models import Page, Post, PublishStatus, Redirect
from apps.core.models import SiteSettings


class UpgradedPersianSiteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # What an old installation contains: the language column holds "fa" (default of migration core.0007), no per-language
        # texts, no user language, Persian content and an old automatic slug redirect stored without a language prefix.
        site = SiteSettings.load()
        site.site_name, site.installed_at = "سایت قدیمی من", timezone.now()
        site.save()
        cls.user = User.objects.create_user(username="boss", email="boss@example.com", password="Str0ng-Pass-9812", role=Role.SUPER_ADMIN)
        User.objects.filter(pk=cls.user.pk).update(email_verified_at=timezone.now())
        cls.post = Post.objects.create(title="نوشتهٔ قدیمی", slug="old-post", body="متن", excerpt="خلاصه", status=PublishStatus.PUBLISHED)
        cls.page = Page.objects.create(title="درباره", slug="about", body="x", status="published")
        cls.page.slug = "about-us"
        cls.page.save()

    def setUp(self):
        self._settings = override_settings(LANGUAGE_CODE="en", RAVIX_CLI_LANG="")
        self._settings.enable()
        self.addCleanup(self._settings.disable)
        trans_real._default = None
        self.addCleanup(setattr, trans_real, "_default", None)
        translation.deactivate_all()
        self.addCleanup(translation.deactivate_all)

    def persian(self, response):
        html = response.content.decode()
        self.assertIn('lang="fa" dir="rtl"', html)
        return html

    def test_the_stored_default_is_persian(self):
        self.assertEqual(SiteSettings.load().language, "fa")
        self.assertEqual(settings.LANGUAGE_CODE, "en")

    def test_home_page_and_post_stay_persian(self):
        html = self.persian(self.client.get("/fa/"))
        self.assertIn("سایت قدیمی من", html)
        self.assertIn("خانه", html)  # the stock menu
        self.persian(self.client.get("/fa/blog/old-post/"))

    def test_old_unprefixed_urls_redirect_into_persian(self):
        for old, new in (("/", "/fa/"), ("/blog/", "/fa/blog/"), ("/blog/old-post/", "/fa/blog/old-post/"), ("/feed/", "/fa/feed/"),
                         ("/about/", "/fa/about-us/")):  # the last one goes through the old slug redirect
            r = self.client.get(old)
            self.assertEqual((r.status_code, r["Location"]), (301, new), old)

    def test_panel_and_login_stay_persian(self):
        self.persian(self.client.get("/accounts/login/"))
        self.client.login(username="boss", password="Str0ng-Pass-9812")
        for path in ("/panel/", "/panel/posts/", "/panel/settings/", f"/panel/posts/{self.post.pk}/edit/"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.persian(r)

    def test_feed_and_sitemaps_stay_persian(self):
        feed = self.client.get("/fa/feed/")
        self.assertEqual(feed.status_code, 200)
        body = feed.content.decode()
        self.assertIn("سایت قدیمی من", body)
        self.assertRegex(body, r"<language>fa")
        index = ET.fromstring(self.client.get("/sitemap.xml").content)
        locs = [e.text for e in index.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
        self.assertEqual(locs[0], "http://testserver/fa/sitemap.xml")  # the site default language comes first
        sm = self.client.get("/fa/sitemap.xml").content.decode()
        self.assertIn("http://testserver/fa/blog/old-post/", sm)

    def test_password_reset_email_is_persian(self):
        mail.outbox.clear()
        self.client.post("/accounts/password-reset/", {"email": "boss@example.com"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("بازیابی رمز عبور", mail.outbox[0].subject)
        self.assertIn("سلام", mail.outbox[0].body)

    def test_404_page_and_unknown_old_url(self):
        r = self.client.get("/fa/no-such/")
        self.assertEqual(r.status_code, 404)
        self.assertIn('lang="fa"', r.content.decode())

    def test_command_output_follows_cli_language_not_the_site(self):
        from io import StringIO

        out = StringIO()
        call_command("ravix_fix_slugs", "--dry-run", stdout=out)
        self.assertNotRegex(out.getvalue(), "[؀-ۿ]")  # the operator sees English; the site stays Persian


class PinLanguageMigrationTests(TestCase):
    """Data migration core.0012: an installed site with an empty/invalid default language is pinned to Persian."""

    def run_migration(self):
        module = importlib.import_module("apps.core.migrations.0012_site_language_keep_persian")
        from django.apps import apps

        module.pin_language(apps, None)

    def site(self, language, installed=True):
        site = SiteSettings.load()
        SiteSettings.objects.filter(pk=1).update(language=language, installed_at=timezone.now() if installed else None)
        return site

    def test_blank_or_invalid_language_becomes_persian(self):
        for value in ("", "xx", "  "):
            self.site(value)
            self.run_migration()
            self.assertEqual(SiteSettings.objects.get(pk=1).language, "fa", repr(value))

    def test_valid_languages_are_untouched(self):
        for value in ("en", "de", "pt-br", "fa"):
            self.site(value)
            self.run_migration()
            self.assertEqual(SiteSettings.objects.get(pk=1).language, value)

    def test_not_installed_site_is_untouched(self):
        self.site("", installed=False)
        self.run_migration()
        self.assertEqual(SiteSettings.objects.get(pk=1).language, "")
