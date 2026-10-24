"""The logo and name at the top of the panel sidebar lead to the public site, in the viewer's language."""
import re
from html import unescape

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user

CODES = [code for code, _name in settings.LANGUAGES]


def sidebar_brand(html):
    match = re.search(r'<aside class="sidebar" id="sidebar".*?<div class="sidebar-brand">(.*?)</div>\s*(?:<form|<nav)', html, re.S)
    assert match, "sidebar brand block not found"
    return match.group(1)


def anchors(block):
    return {m.group("kind"): m.group(0) for m in re.finditer(r'<a [^>]*data-view-site(?P<kind>-new)?[^>]*>', block)}


class PanelSiteLinkTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user("viewer", Role.ADMIN))

    def get(self, lang=None, name="dashboard:home"):
        if lang:
            self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = lang
        return self.client.get(reverse(name))

    def test_the_brand_links_to_the_front_page_in_the_same_tab_with_a_title(self):
        for lang in CODES:
            with self.subTest(lang=lang):
                html = self.get(lang).content.decode()
                tags = anchors(sidebar_brand(html))
                main = tags[None]
                with translation.override(lang):
                    expected = reverse("public:home")
                self.assertIn(f'href="/{lang}/"', main)
                self.assertEqual(expected, f"/{lang}/")
                self.assertNotIn("target=", main)  # same tab
                with translation.override(lang):
                    from django.utils.translation import gettext
                    title = gettext("View site")
                self.assertIn(f'title="{title}"', main)

    def test_a_new_tab_icon_opens_the_same_page_safely_with_an_accessible_name(self):
        for lang in CODES:
            with self.subTest(lang=lang):
                html = self.get(lang).content.decode()
                new = anchors(sidebar_brand(html))["-new"]
                self.assertIn(f'href="/{lang}/"', new)
                self.assertIn('target="_blank"', new)
                self.assertIn('rel="noopener"', new)
                with translation.override(lang):
                    from django.utils.translation import gettext
                    label = gettext("View site in a new tab")
                self.assertIn(f'aria-label="{label}"', new)
                self.assertIn(f'title="{label}"', new)
                self.assertIn("<svg", sidebar_brand(html).split(new)[1])

    def test_both_texts_are_translated_in_every_language(self):
        from django.utils.translation import gettext

        for lang in CODES:
            if lang == "en":
                continue
            with self.subTest(lang=lang), translation.override(lang):
                for msgid in ("View site", "View site in a new tab"):
                    self.assertNotEqual(gettext(msgid), msgid)
        with translation.override("fa"):
            self.assertEqual(gettext("View site"), "مشاهدهٔ سایت")

    def test_the_link_sits_in_the_sidebar_that_the_mobile_menu_opens(self):
        html = self.get("fa").content.decode()
        sidebar = re.search(r'<aside class="sidebar" id="sidebar".*?</aside>', html, re.S).group(0)
        self.assertIn("data-view-site", sidebar)
        self.assertIn("data-sidebar-toggle", html)  # the menu button toggles that same element (aria-controls="sidebar")
        self.assertIn('aria-controls="sidebar"', html)

    def test_the_visible_name_still_reads_site_name_and_admin_panel(self):
        html = unescape(self.get("en").content.decode())
        block = sidebar_brand(html)
        self.assertRegex(block, r"<strong>[^<]+</strong><small>Admin panel</small>")

    def test_it_works_on_other_panel_pages_too(self):
        for name in ("accounts:profile", "dashboard:home"):
            with self.subTest(page=name):
                self.assertIn('href="/de/"', anchors(sidebar_brand(self.get("de", name).content.decode()))[None])
