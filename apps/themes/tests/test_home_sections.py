"""Home page sections: several hero buttons, the features section, Markdown text, and per-language texts."""
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.core.models import SiteSettings
from apps.themes import services
from apps.themes.config import localized_section, sanitize, sanitize_section
from apps.themes.constants import FEATURE_ICONS, MAX_FEATURE_ITEMS, MAX_SECTION_BUTTONS
from apps.themes.forms import items_to_text, parse_items
from apps.themes.models import ThemeConfig
from apps.themes.templatetags.ravix_theme import feature_icon


class SanitizeTests(SimpleTestCase):
    def test_buttons_need_label_and_safe_link_and_are_capped(self):
        s = sanitize_section({"type": "hero", "buttons": [
            {"label": "A", "url": "/a/"}, {"label": "", "url": "/b/"}, {"label": "C", "url": "javascript:alert(1)"},
            {"label": "D", "url": "https://example.com/"}, {"label": "E", "url": "/e/"}, {"label": "F", "url": "/f/"}]})
        self.assertEqual([b["label"] for b in s["buttons"]], ["A", "D", "E"])
        self.assertLessEqual(len(s["buttons"]), MAX_SECTION_BUTTONS)
        self.assertEqual((s["button_label"], s["button_url"]), ("A", "/a/"))  # first button mirrored for older themes

    def test_old_single_button_documents_still_work(self):
        s = sanitize_section({"type": "hero", "button_label": "Go", "button_url": "/blog/"})
        self.assertEqual(s["buttons"], [{"label": "Go", "url": "/blog/"}])
        again = sanitize_section(s)
        self.assertEqual(again, s)  # idempotent

    def test_link_without_text_is_kept_for_view_all(self):
        s = sanitize_section({"type": "latest_posts", "button_url": "/blog/"})
        self.assertEqual((s["button_label"], s["button_url"], s["buttons"]), ("", "/blog/", []))
        self.assertEqual(sanitize_section(s)["button_url"], "/blog/")

    def test_feature_items_icon_whitelist_and_caps(self):
        items = [{"title": f"T{i}", "icon": "zap", "text": "x"} for i in range(MAX_FEATURE_ITEMS + 5)]
        s = sanitize_section({"type": "features", "items": items + [{"title": "Bad", "icon": "<script>", "text": "y"}, {"icon": "zap"}]})
        self.assertEqual(len(s["items"]), MAX_FEATURE_ITEMS)
        bad = sanitize_section({"type": "features", "items": [{"title": "Bad", "icon": "<script>", "text": "y"}, {"icon": "zap"}]})
        self.assertEqual(bad["items"], [{"title": "Bad", "icon": "", "text": "y"}])  # unknown icon dropped, empty item dropped

    def test_translations_keep_only_supported_languages_and_text(self):
        s = sanitize_section({"type": "hero", "title": "Hi", "translations": {
            "de": {"title": "Hallo", "evil": "x", "button_labels": ["Los"]}, "xx": {"title": "?"}, "fa": {"title": ""}, "pt_BR": {"title": "Oi"}}})
        self.assertEqual(sorted(s["translations"]), ["de", "pt-br"])
        self.assertNotIn("evil", s["translations"]["de"])

    def test_localized_section_falls_back_field_by_field(self):
        s = sanitize_section({"type": "hero", "title": "Hi", "subtitle": "Sub", "buttons": [{"label": "Go", "url": "/a/"}, {"label": "More", "url": "/b/"}],
                              "translations": {"de": {"title": "Hallo", "button_labels": ["Los", ""]}}})
        de = localized_section(s, "de", "en")
        self.assertEqual((de["title"], de["subtitle"]), ("Hallo", "Sub"))
        self.assertEqual([b["label"] for b in de["buttons"]], ["Los", "More"])
        self.assertEqual(de["button_label"], "Los")
        self.assertEqual(localized_section(s, "en", "en")["title"], "Hi")  # the default language uses the base text
        self.assertEqual(localized_section(s, "fr", "en")["title"], "Hi")  # no translation: base text
        self.assertEqual(localized_section(s, "de", "de")["title"], "Hi")  # de is the default language here

    def test_default_config_is_valid(self):
        self.assertEqual(sanitize(sanitize({})), sanitize({}))


class FormParsingTests(SimpleTestCase):
    def test_parse_items(self):
        items, error = parse_items("zap | Fast | Very fast\n | No icon | text\nJust a title\nTitle | text only")
        self.assertEqual(error, "")
        self.assertEqual(items[0], {"title": "Fast", "icon": "zap", "text": "Very fast"})
        self.assertEqual(items[1]["icon"], "")
        self.assertEqual(items[2], {"title": "Just a title", "icon": "", "text": ""})
        self.assertEqual(items[3], {"title": "Title", "icon": "", "text": "text only"})
        self.assertEqual(parse_items(items_to_text(items))[0], items)

    def test_unknown_icon_is_an_error(self):
        self.assertTrue(parse_items("nope | T | x")[1])

    def test_icon_tag_draws_only_known_icons(self):
        for name in FEATURE_ICONS:
            self.assertIn("<svg", str(feature_icon(name)), name)
        self.assertEqual(feature_icon("<script>"), "")
        self.assertEqual(feature_icon(""), "")


FORM = {"sections-TOTAL_FORMS": 4, "sections-INITIAL_FORMS": 0, "sections-MIN_NUM_FORMS": 0, "sections-MAX_NUM_FORMS": 20}


class HomeEditorAndRenderingTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user("adm", role=Role.ADMIN))
        site = SiteSettings.load()
        site.language = "en"
        site.save()

    def save_sections(self):
        data = {**FORM,
                "sections-0-type": "hero", "sections-0-title": "Build it", "sections-0-subtitle": "Fast CMS", "sections-0-ORDER": 1,
                "sections-0-body": "Open **source** and <script>alert(1)</script> free",
                "sections-0-button_label": "Start", "sections-0-button_url": "/blog/",
                "sections-0-button2_label": "Docs", "sections-0-button2_url": "https://example.com/docs",
                "sections-1-type": "features", "sections-1-title": "Why", "sections-1-ORDER": 2,
                "sections-1-items": "zap | Fast | Light and quick\nshield | Secure | Locked templates",
                "sections-2-type": "text", "sections-2-title": "About", "sections-2-ORDER": 3,
                "sections-2-body": "Some **bold** text\n\n- one\n- two\n\n[link](https://example.com) <b onclick=x>b</b>",
                "sections-3-type": "latest_posts", "sections-3-ORDER": 4}
        resp = self.client.post(reverse("themes:home"), data)
        self.assertEqual(resp.status_code, 302, getattr(resp, "context", None) and resp.context["formset"].errors)
        self.client.post(reverse("themes:publish"))

    def test_structure_buttons_features_and_markdown_render(self):
        self.save_sections()
        s = ThemeConfig.load().published["home"]["sections"]
        self.assertEqual([b["label"] for b in s[0]["buttons"]], ["Start", "Docs"])
        self.assertEqual(s[1]["items"][1]["icon"], "shield")
        html = self.client.get("/en/").content.decode()
        self.assertIn(">Start<", html)
        self.assertIn(">Docs<", html)
        self.assertEqual(html.count('class="btn btn-lg'), 2)
        self.assertIn("Light and quick", html)
        self.assertIn('class="rv-icon"', html)
        self.assertIn("<strong>bold</strong>", html)  # Markdown in a text section
        self.assertIn("<li>one</li>", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("onclick", html)

    def test_features_without_theme_support_are_not_shown_but_kept(self):
        self.save_sections()
        from apps.public import builders

        doc = services.published_doc(ThemeConfig.load())
        shown = builders.build_sections(doc, ("hero", "text"), {"name": "N", "tagline": ""})
        self.assertEqual([x.type for x in shown], ["hero", "text"])

    def test_language_tabs_and_translated_texts(self):
        self.save_sections()
        page = self.client.get(reverse("themes:home"))
        self.assertContains(page, "?lang=de")
        resp = self.client.get(reverse("themes:home") + "?lang=de")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="texts-TOTAL_FORMS"')
        data = {"texts-TOTAL_FORMS": 4, "texts-INITIAL_FORMS": 0, "texts-MIN_NUM_FORMS": 0, "texts-MAX_NUM_FORMS": 20,
                "texts-0-title": "Baue es", "texts-0-button_label": "Los", "texts-0-button2_label": "",
                "texts-1-title": "Warum", "texts-1-items": "Schnell | Leicht und schnell\nSicher",
                "texts-2-title": "", "texts-3-title": ""}
        r = self.client.post(reverse("themes:home") + "?lang=de", data)
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["formset"].errors)
        self.assertIn("lang=de", r["Location"])
        draft = ThemeConfig.load().draft["home"]["sections"]
        self.assertEqual(draft[0]["translations"]["de"]["title"], "Baue es")
        self.assertEqual(draft[0]["title"], "Build it")  # structure and default text untouched
        self.assertNotIn("de", draft[2]["translations"])
        self.client.post(reverse("themes:publish"))
        de = self.client.get("/de/").content.decode()
        self.assertIn("Baue es", de)
        self.assertIn(">Los<", de)
        self.assertIn(">Docs<", de)  # button 2 has no German text: default text
        self.assertIn("Leicht und schnell", de)
        self.assertIn("Locked templates", de)  # item 2 had no German text
        en = self.client.get("/en/").content.decode()
        self.assertIn("Build it", en)
        self.assertNotIn("Baue es", en)

    def test_translations_follow_their_section_when_rows_are_reordered(self):
        self.save_sections()
        self.client.post(reverse("themes:home") + "?lang=de", {
            "texts-TOTAL_FORMS": 4, "texts-INITIAL_FORMS": 0, "texts-MIN_NUM_FORMS": 0, "texts-MAX_NUM_FORMS": 20, "texts-0-title": "Baue es",
            "texts-1-title": "", "texts-2-title": "", "texts-3-title": ""})
        form = self.client.get(reverse("themes:home")).context["formset"]
        data = {**FORM}
        for i, f in enumerate(form):
            for name, bound in f.fields.items():
                value = f[name].value()
                if value is None or value is False:
                    continue
                data[f"sections-{i}-{name}"] = value
        # move the hero (row 0) to the end
        data["sections-0-ORDER"], data["sections-1-ORDER"], data["sections-2-ORDER"], data["sections-3-ORDER"] = 4, 1, 2, 3
        self.assertEqual(self.client.post(reverse("themes:home"), data).status_code, 302)
        sections = ThemeConfig.load().draft["home"]["sections"]
        self.assertEqual(sections[-1]["type"], "hero")
        self.assertEqual(sections[-1]["translations"]["de"]["title"], "Baue es")

    def test_features_need_items_and_valid_icons(self):
        data = {**FORM, "sections-TOTAL_FORMS": 1, "sections-0-type": "features", "sections-0-ORDER": 1, "sections-0-items": ""}
        self.assertEqual(self.client.post(reverse("themes:home"), data).status_code, 200)
        data["sections-0-items"] = "nope | T | x"
        self.assertEqual(self.client.post(reverse("themes:home"), data).status_code, 200)
        data["sections-0-items"] = "zap | T | x"
        self.assertEqual(self.client.post(reverse("themes:home"), data).status_code, 302)

    def test_button_needs_text_and_link_together(self):
        data = {**FORM, "sections-TOTAL_FORMS": 1, "sections-0-type": "hero", "sections-0-ORDER": 1,
                "sections-0-button2_label": "Only text"}
        self.assertEqual(self.client.post(reverse("themes:home"), data).status_code, 200)
        data["sections-0-button2_url"] = "javascript:alert(1)"
        self.assertEqual(self.client.post(reverse("themes:home"), data).status_code, 200)

    def test_default_language_tab_is_the_normal_editor(self):
        resp = self.client.get(reverse("themes:home") + "?lang=en")  # en is this site's default language here
        self.assertContains(resp, 'name="sections-TOTAL_FORMS"')
        resp = self.client.get(reverse("themes:home") + "?lang=zz")
        self.assertContains(resp, 'name="sections-TOTAL_FORMS"')
