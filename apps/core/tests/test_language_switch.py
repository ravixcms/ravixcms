from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from apps.accounts.tests.test_auth import make_user
from apps.core.models import SiteSettings

COOKIE = settings.LANGUAGE_COOKIE_NAME


def html_root(response):
    text = response.content.decode()
    return text[text.index("<html"): text.index(">", text.index("<html")) + 1]


class SetLanguageViewTests(TestCase):
    url = "/i18n/setlang/"

    def test_post_sets_cookie_and_redirects_back(self):
        response = self.client.post(self.url, {"language": "de", "next": "/accounts/login/"})
        self.assertRedirects(response, "/accounts/login/", fetch_redirect_response=False)
        self.assertEqual(response.cookies[COOKIE].value, "de")
        self.assertEqual(response.cookies[COOKIE]["samesite"], "Lax")
        self.assertTrue(response.cookies[COOKIE]["httponly"])

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url, {"language": "de"}).status_code, 405)

    def test_external_next_is_ignored(self):
        response = self.client.post(self.url, {"language": "fr", "next": "https://evil.example/x"})
        self.assertEqual(response["Location"], "/")

    def test_unknown_language_changes_nothing(self):
        response = self.client.post(self.url, {"language": "xx", "next": "/accounts/login/"})
        self.assertNotIn(COOKIE, response.cookies)

    def test_code_variants_are_normalised(self):
        response = self.client.post(self.url, {"language": "pt_BR"})
        self.assertEqual(response.cookies[COOKIE].value, "pt-br")

    def test_signed_in_user_profile_is_updated(self):
        user = make_user()
        self.client.force_login(user)
        self.client.post(self.url, {"language": "tr", "next": "/panel/"})
        user.refresh_from_db()
        self.assertEqual(user.language, "tr")


class LanguageResolutionTests(TestCase):
    def render_login(self, **cookies):
        for key, value in cookies.items():
            self.client.cookies[key] = value
        return self.client.get(reverse("accounts:login"))

    def test_site_default_language(self):
        site = SiteSettings.load()
        site.language = "ru"
        site.save()
        root = html_root(self.client.get(reverse("accounts:login")))
        self.assertIn('lang="ru"', root)
        self.assertIn('dir="ltr"', root)

    def test_cookie_beats_site_default(self):
        site = SiteSettings.load()
        site.language = "ru"
        site.save()
        self.assertIn('lang="de"', html_root(self.render_login(**{COOKIE: "de"})))

    def test_direction_follows_language(self):
        for language, direction in (("fa", "rtl"), ("ar", "rtl"), ("en", "ltr"), ("tr", "ltr"), ("es", "ltr"), ("fr", "ltr"),
                                    ("de", "ltr"), ("ru", "ltr"), ("pt-br", "ltr"), ("zh-hans", "ltr")):
            with self.subTest(language=language):
                self.client.cookies[COOKIE] = language
                root = html_root(self.client.get(reverse("accounts:login")))
                self.assertIn(f'lang="{language}"', root)
                self.assertIn(f'dir="{direction}"', root)

    def test_profile_language_is_used_when_there_is_no_cookie(self):
        user = make_user()
        user.language = "es"
        user.save()
        self.client.force_login(user)
        response = self.client.get(reverse("accounts:profile"))
        self.assertIn('lang="es"', html_root(response))
        self.assertIn("Vary", response)
        self.assertIn("Cookie", response["Vary"])

    def test_cookie_beats_profile(self):
        user = make_user()
        user.language = "es"
        user.save()
        self.client.force_login(user)
        self.client.cookies[COOKIE] = "fr"
        self.assertIn('lang="fr"', html_root(self.client.get(reverse("accounts:profile"))))

    def test_invalid_cookie_falls_back(self):
        self.assertIn('lang="fa"', html_root(self.render_login(**{COOKIE: "klingon"})))

    def test_switcher_is_rendered_with_all_languages(self):
        text = self.client.get(reverse("accounts:login")).content.decode()
        self.assertIn('action="/i18n/setlang/"', text)
        for code, name in settings.LANGUAGES:
            self.assertIn(f'<option value="{code}"', text)
            self.assertIn(name, text)

    def test_javascript_catalog_follows_language(self):
        self.client.cookies[COOKIE] = "de"
        self.assertIn("Passwort verbergen", self.client.get(reverse("javascript-catalog")).content.decode())


class ProfileLanguageTests(TestCase):
    def test_saving_the_profile_language_updates_cookie(self):
        user = make_user()
        self.client.force_login(user)
        response = self.client.post(reverse("accounts:profile"), {
            "first_name": user.first_name, "last_name": user.last_name, "email": user.email, "phone": "", "bio": "", "language": "ar"})
        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertEqual(user.language, "ar")
        self.assertEqual(response.cookies[COOKIE].value, "ar")
