"""Menu items can carry their own title and destination per language (fallback: the item's own values)."""
from django.test import TestCase
from django.urls import reverse

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.content.models import Category, Page
from apps.core.models import SiteSettings
from apps.themes import config
from apps.themes.models import ThemeConfig


def _item(**over):
    base = {"label": "درباره ما", "kind": "page", "target": 1, "url": "", "new_tab": False}
    base.update(over)
    return base


class SanitizeTests(TestCase):
    def test_old_items_without_translations_stay_valid(self):
        item = config.sanitize_menu_item({"label": "خانه", "kind": "home"})
        self.assertEqual(item["translations"], {})

    def test_translations_are_cleaned_per_kind(self):
        item = config.sanitize_menu_item(_item(translations={
            "en": {"label": "About", "target": 7, "url": "https://x.example"}, "DE": {"label": ""}, "zz-bad!": {"label": "x"}, "fr": {"label": "À propos"}}))
        self.assertEqual(item["translations"], {"en": {"label": "About", "target": 7}, "fr": {"label": "À propos"}})
        link = config.sanitize_menu_item({"label": "GitHub", "kind": "url", "url": "https://github.com/a", "translations": {"en": {"url": "javascript:alert(1)"}, "de": {"url": "https://github.com/b"}}})
        self.assertEqual(link["translations"], {"de": {"label": "", "url": "https://github.com/b"}})

    def test_long_titles_are_clipped(self):
        item = config.sanitize_menu_item(_item(translations={"en": {"label": "x" * 200}}))
        self.assertEqual(len(item["translations"]["en"]["label"]), 60)


class FormTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user("adm", role=Role.ADMIN))
        self.fa = Page.objects.create(title="درباره", slug="about-fa", body="x", status="published", language="fa")
        self.en = Page.objects.create(title="About", slug="about-en", body="x", status="published", language="en")

    def _post(self, translations):
        data = {"header-TOTAL_FORMS": 1, "header-INITIAL_FORMS": 0, "header-MIN_NUM_FORMS": 0, "header-MAX_NUM_FORMS": 20,
                "footer-TOTAL_FORMS": 0, "footer-INITIAL_FORMS": 0, "footer-MIN_NUM_FORMS": 0, "footer-MAX_NUM_FORMS": 20,
                "header-0-label": "درباره", "header-0-kind": "page", "header-0-page": self.fa.pk, "header-0-ORDER": 1,
                "header-0-translations": translations}
        return self.client.post(reverse("themes:menus"), data)

    def test_translations_round_trip_through_the_panel(self):
        self.assertEqual(self._post("en | About us | about-en\nde | Über uns").status_code, 302)
        item = ThemeConfig.load().draft["menus"]["header"][0]
        self.assertEqual(item["translations"], {"de": {"label": "Über uns"}, "en": {"label": "About us", "target": self.en.pk}})
        html = self.client.get(reverse("themes:menus")).content.decode()
        self.assertIn("en | About us | about-en", html)
        self.assertIn("de | Über uns | ", html)

    def test_unknown_language_or_slug_is_refused(self):
        self.assertEqual(self._post("xx | Nope").status_code, 200)
        self.assertEqual(self._post("en | About | no-such-page").status_code, 200)
        self.assertFalse(ThemeConfig.load().has_draft)


class RenderTests(TestCase):
    def setUp(self):
        settings = SiteSettings.load()
        settings.language = "fa"
        settings.save()
        self.fa = Page.objects.create(title="درباره", slug="about-fa", body="x", status="published", language="fa")
        self.en = Page.objects.create(title="About", slug="about-en", body="x", status="published", language="en")
        self.cat = Category.objects.create(name="News", slug="news")
        doc = config.sanitize({"menus": {"header": [
            _item(target=self.fa.pk, translations={"en": {"label": "About Ravix", "target": self.en.pk}, "de": {"label": "Über Ravix"}}),
            {"label": "GitHub", "kind": "url", "url": "https://github.com/ravixcms", "translations": {"en": {"url": "https://github.com/ravixcms/ravixcms"}}},
        ]}})
        row = ThemeConfig.load()
        row.published = doc
        row.save()

    def _links(self, lang):
        html = self.client.get(f"/{lang}/").content.decode()
        return html

    def test_each_language_gets_its_own_title_and_page(self):
        fa = self._links("fa")
        self.assertIn(">درباره ما<", fa)
        self.assertIn("/fa/about-fa/", fa)
        en = self._links("en")
        self.assertIn("About Ravix", en)
        self.assertIn("/en/about-en/", en)
        self.assertNotIn("درباره ما", en)
        self.assertIn("https://github.com/ravixcms/ravixcms", en)

    def test_a_language_without_a_translated_destination_falls_back_field_by_field(self):
        de = self._links("de")
        self.assertIn("Über Ravix", de)  # translated title, shared destination
        self.assertIn("/fa/about-fa/", de)

    def test_a_missing_translated_page_falls_back_to_the_shared_one(self):
        self.en.status = "draft"
        self.en.save()
        en = self._links("en")
        self.assertIn("About Ravix", en)
        self.assertIn("/fa/about-fa/", en)


class TranslatedTitleIsShownAsWrittenTests(TestCase):
    def test_a_title_equal_to_another_languages_default_text_is_not_rewritten(self):
        # "Articles" is how one interface language says the default "Posts"; as a title written for English it must stay "Articles"
        from django.utils import translation

        from apps.themes.config import localize_default_text

        clash = next((t for t in ("Articles", "Beiträge", "Entradas", "Posts") if localize_default_text(t) != t), None)
        if clash is None:
            self.skipTest("no default text clashes in this catalog")
        row = ThemeConfig.load()
        row.published = config.sanitize({"menus": {"header": [{"label": "نوشته‌ها", "kind": "blog", "translations": {"en": {"label": clash}}}]}})
        row.save()
        with translation.override("en"):
            html = self.client.get("/en/").content.decode()
        self.assertIn(f">{clash}<", html)
