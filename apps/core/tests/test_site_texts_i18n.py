"""Site name, tagline, description and footer text per language; installer suggests the name in the chosen language."""
import os
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from apps.accounts.models import Role
from apps.accounts.tests.test_auth import make_user
from apps.core.models import SiteSettings
from apps.installer import services


class LocalizedTextTests(TestCase):
    def setUp(self):
        site = SiteSettings.load()
        site.site_name, site.tagline, site.footer_text, site.language = "نام فارسی", "شعار فارسی", "پاورقی فارسی", "fa"
        site.translations = {"en": {"site_name": "English name", "tagline": "English tagline"}, "de": {"site_name": "Deutscher Name"}}
        site.save()

    def test_accessor_and_fallback(self):
        site = SiteSettings.load()
        self.assertEqual(site.localized("site_name", "en"), "English name")
        self.assertEqual(site.localized("site_name", "fa"), "نام فارسی")  # default language: the plain field
        self.assertEqual(site.localized("site_name", "ru"), "نام فارسی")  # no value → default language
        self.assertEqual(site.localized("footer_text", "en"), "پاورقی فارسی")  # missing field → default
        self.assertEqual(site.localized("site_name", "pt_BR"), "نام فارسی")
        with translation.override("de"):
            self.assertEqual(site.localized("site_name"), "Deutscher Name")
            self.assertEqual(site.localized("tagline"), "شعار فارسی")

    def test_public_pages_use_the_visitors_language(self):
        en = self.client.get("/en/").content.decode()
        self.assertIn("English name", en)
        self.assertNotIn("نام فارسی", en.split("<select")[0] + en.split("</select>")[-1])
        self.assertIn("Deutscher Name", self.client.get("/de/").content.decode())
        fa = self.client.get("/fa/").content.decode()
        self.assertIn("نام فارسی", fa)
        self.assertIn("English name", self.client.get("/en/feed/").content.decode())

    def test_default_language_change_keeps_fallback_working(self):
        site = SiteSettings.load()
        site.language = "en"
        site.save()
        self.assertEqual(site.localized("site_name", "en"), "نام فارسی")  # plain field is now the English (default) text

    def test_panel_shows_name_in_the_users_language(self):
        admin = make_user("adm", Role.ADMIN)
        self.client.force_login(admin)
        self.client.cookies["ravix_lang"] = "en"
        self.assertContains(self.client.get(reverse("dashboard:home")), "English name")


class SettingsFormTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user("adm", Role.ADMIN))
        self.url = reverse("core:site_settings")
        self.base = {"site_name": "سایت", "tagline": "", "description": "", "admin_email": "a@b.com", "language": "fa", "timezone": "UTC"}

    def test_save_and_clear_translations(self):
        data = {**self.base, "tr_en__site_name": "My site", "tr_en__tagline": " Tagline ", "tr_de__footer_text": "Fußzeile",
                "tr_fa__site_name": "ignored: default language"}
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        site = SiteSettings.load()
        self.assertEqual(site.translations, {"en": {"site_name": "My site", "tagline": "Tagline"}, "de": {"footer_text": "Fußzeile"}})
        page = self.client.get(self.url).content.decode()
        self.assertIn('value="My site"', page)
        data = {**self.base, "tr_de__footer_text": "Fußzeile"}  # emptied fields are removed
        self.client.post(self.url, data)
        self.assertEqual(SiteSettings.load().translations, {"de": {"footer_text": "Fußzeile"}})

    def test_pt_br_field_names(self):
        self.client.post(self.url, {**self.base, "tr_pt_br__site_name": "Meu site", "tr_zh_hans__site_name": "我的网站"})
        self.assertEqual(SiteSettings.load().translations, {"pt-br": {"site_name": "Meu site"}, "zh-hans": {"site_name": "我的网站"}})


class InstallerNameTests(TestCase):
    def test_suggested_name_follows_language(self):
        self.assertEqual(services.default_site_name("fa"), "راویکس")
        self.assertEqual(services.default_site_name("en"), "Ravix")

    def test_installer_form_starts_in_the_pages_language(self):
        from apps.installer.forms import InstallForm

        with translation.override("fa"):
            form = InstallForm()
            self.assertEqual((form.fields["language"].initial, form.fields["site_name"].initial), ("fa", "راویکس"))
        with translation.override("de"):
            form = InstallForm()
            self.assertEqual((form.fields["language"].initial, form.fields["timezone"].initial, form.fields["site_name"].initial), ("de", "UTC", "Ravix"))

    def test_untouched_suggested_name_is_rewritten_for_the_chosen_language(self):
        services.perform_install(site_name="راویکس", language="de", timezone_name="UTC", username="b", email="b@e.com", password="Zq9#kLm2vX!pe")
        self.assertEqual(SiteSettings.load().site_name, "Ravix")

    def test_custom_name_is_kept(self):
        services.perform_install(site_name="سایت من", language="de", timezone_name="UTC", username="b", email="b@e.com", password="Zq9#kLm2vX!pe")
        self.assertEqual(SiteSettings.load().site_name, "سایت من")

    def test_cli_default_name_uses_the_language(self):
        with mock.patch.dict(os.environ, {"RAVIX_T_PW": "Zq9#kLm2vX!pe"}):
            call_command("ravix_install", "--username=cli", "--email=cli@e.com", "--password-env=RAVIX_T_PW", "--language=fa",
                         "--timezone=UTC", stdout=StringIO())
        self.assertEqual(SiteSettings.load().site_name, "راویکس")


class FileInputStringsTests(TestCase):
    """The panel replaces the browser's English «Choose File / No file chosen» with these (static/js/ravix.js)."""

    STRINGS = ["Choose file", "Choose files", "No file chosen", "No files chosen", "Files selected: %(n)s", "Drop the file here",
               "Drop the files here", "This file type is not accepted here."]

    def test_every_language_translates_them(self):
        from django.conf import settings
        import gettext as gettext_module

        from django.utils.translation import to_locale

        source = (settings.BASE_DIR / "static/js/ravix.js").read_text(encoding="utf-8")
        for text in self.STRINGS:
            self.assertIn(f'gettext("{text}")', source)
        for code, _name in settings.LANGUAGES:
            if code == "en":
                continue
            catalog = gettext_module.translation("djangojs", settings.BASE_DIR / "locale", [to_locale(code)])
            for text in self.STRINGS:
                self.assertNotEqual(catalog.gettext(text), text, f"{code}: {text}")


class TranslationFieldStyleTests(TestCase):
    def test_per_language_fields_use_the_panel_input_style(self):
        """Without the class a textarea keeps its native width (cols=40) and overflows a phone screen."""
        from apps.core.forms import SiteSettingsForm

        form = SiteSettingsForm(instance=SiteSettings.load())
        for key in form.fields:
            if key.startswith("tr_"):
                self.assertIn("input", form.fields[key].widget.attrs.get("class", ""), key)


class DefaultMenuTextTests(TestCase):
    """The default menu/section texts are stored in the language active when the site was set up; they follow the visitor."""

    def test_default_labels_follow_the_visitor_language(self):
        from apps.themes.config import localize_default_text

        for lang, home in (("fa", "خانه"), ("de", "Startseite"), ("en", "Home")):
            with translation.override(lang):
                self.assertEqual(localize_default_text("Home"), home)
                self.assertEqual(localize_default_text("خانه"), home)  # stored in Persian
                self.assertEqual(localize_default_text("Startseite"), home)  # stored in German
        with translation.override("de"):
            self.assertEqual(localize_default_text("My own label"), "My own label")  # owner text is never touched
            self.assertEqual(localize_default_text(""), "")

    def test_public_header_menu_in_each_language(self):
        for lang, home in (("fa", "خانه"), ("en", "Home"), ("ru", "Главная")):
            html = self.client.get(f"/{lang}/").content.decode()
            self.assertIn(f">{home}<", html, lang)
