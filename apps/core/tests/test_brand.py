"""The official Ravix logo is used everywhere the product shows itself, and it never costs a page or an email."""
import re
import struct
from email import message_from_bytes
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree

from django.conf import settings
from django.core import mail
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.core import mailhtml
from apps.core.models import SiteSettings

ROOT = Path(settings.BASE_DIR)
BRAND = ROOT / "static" / "brand"


class BrandFilesTests(SimpleTestCase):
    def test_every_logo_file_is_present_and_valid(self):
        for name in ("ravix-icon", "ravix-icon-dark", "ravix-logo", "ravix-logo-dark", "ravix-wordmark", "ravix-wordmark-dark"):
            with self.subTest(name=name):
                root = ElementTree.parse(BRAND / f"{name}.svg").getroot()
                self.assertTrue(root.tag.endswith("svg"))
                self.assertNotIn("<text", (BRAND / f"{name}.svg").read_text())  # outlines only: no font needed
        for size in (16, 32, 48, 64, 128, 192, 256, 512, 1024):
            self.assertEqual(self.png_size(BRAND / "png" / f"ravix-icon-{size}.png"), (size, size))
        self.assertEqual(self.png_size(BRAND / "png" / "apple-touch-icon.png"), (180, 180))
        self.assertEqual(self.png_size(BRAND / "png" / "ravix-logo.png"), (1464, 480))
        ico = (BRAND / "favicon.ico").read_bytes()
        self.assertEqual(struct.unpack("<HHH", ico[:6]), (0, 1, 3))  # an icon with 16, 32 and 48 px images

    @staticmethod
    def png_size(path):
        data = path.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", path
        return struct.unpack(">II", data[16:24])

    def test_the_inline_copies_used_by_the_standalone_page_match_the_originals(self):
        for name in ("ravix-logo.svg", "ravix-logo-dark.svg"):
            self.assertEqual((ROOT / "templates" / "brand" / name).read_bytes(), (BRAND / name).read_bytes(), name)

    def test_brand_colours_are_css_variables_of_the_panel(self):
        css = (ROOT / "static" / "css" / "ravix.css").read_text()
        for name, value in (("navy", "#0F172A"), ("indigo", "#4F46E5"), ("amber", "#F59E0B")):
            self.assertRegex(css, rf"--brand-{name}:\s*{value};")
        self.assertIn("--c-primary: var(--brand-indigo);", css)

    def test_readme_shows_the_logo_for_light_and_dark_github(self):
        readme = (ROOT / "README.md").read_text()
        top = readme[:600]
        self.assertIn("<picture>", top)
        self.assertIn('media="(prefers-color-scheme: dark)" srcset="static/brand/ravix-logo-dark.svg"', top)
        self.assertIn('src="static/brand/ravix-logo.svg"', top)
        self.assertLess(readme.index("<picture>"), readme.index("# Ravix"))


class PanelAndAuthTests(TestCase):
    def test_panel_sidebar_shows_the_icon_and_keeps_the_site_link(self):
        self.client.force_login(make_user("brand-admin", Role.ADMIN))
        html = self.client.get(reverse("dashboard:home")).content.decode()
        sidebar = re.search(r'<aside class="sidebar".*?</aside>', html, re.S).group(0)
        self.assertIn("brand/ravix-icon.svg", sidebar)
        self.assertIn("is-ravix", sidebar)
        self.assertIn("data-view-site", sidebar)  # "View site" is still the link around the logo
        self.assertIn("data-view-site-new", sidebar)

    def test_an_uploaded_site_logo_still_wins_over_the_default_icon(self):
        cfg = SiteSettings.load()
        cfg.logo.name = "site/logo.png"
        cfg.save()
        self.client.force_login(make_user("brand-admin2", Role.ADMIN))
        sidebar = re.search(r'<aside class="sidebar".*?</aside>', self.client.get(reverse("dashboard:home")).content.decode(), re.S).group(0)
        self.assertIn("site/logo.png", sidebar)
        self.assertNotIn("ravix-icon.svg", sidebar)

    def test_login_and_password_reset_show_the_full_logo(self):
        for name in ("accounts:login", "accounts:password_reset"):
            with self.subTest(page=name):
                html = self.client.get(reverse(name)).content.decode()
                self.assertIn('class="brand-logo" src="', html)
                self.assertIn("brand/ravix-logo.svg", html)
                self.assertIn('alt="Ravix"', html)
                self.assertNotIn("brand/ravix-logo-dark.svg", html)  # these pages are light-only: the white-text logo would vanish

    def test_the_default_site_name_is_not_repeated_as_a_visible_title(self):
        html = self.client.get(reverse("accounts:login")).content.decode()
        self.assertRegex(html, r'<h1 class="sr-only">')  # "راویکس" / "Ravix": the logo already says it
        cfg = SiteSettings.load()
        cfg.site_name = "وبلاگ من"
        cfg.save()
        html = self.client.get(reverse("accounts:login")).content.decode()
        self.assertIn("<h1>وبلاگ من</h1>", html)

    def test_installer_page_shows_the_full_logo(self):
        with override_settings(RAVIX_INSTALLER_ENABLED=True, RAVIX_INSTALL_TOKEN="tok-1", DEBUG=False):
            from apps.installer import services

            services.reset_cache()
            self.addCleanup(services.reset_cache)
            response = self.client.get("/install/")
        self.assertIn("brand/ravix-logo.svg", response.content.decode())


class FaviconTests(TestCase):
    def test_panel_pages_without_a_site_favicon_use_the_ravix_icons(self):
        html = self.client.get(reverse("accounts:login")).content.decode()
        self.assertRegex(html, r'rel="icon" href="[^"]*brand/favicon[^"]*\.ico"')
        self.assertRegex(html, r'rel="icon" type="image/svg\+xml" href="[^"]*brand/ravix-icon[^"]*\.svg"')
        self.assertRegex(html, r'rel="apple-touch-icon" href="[^"]*brand/png/apple-touch-icon[^"]*\.png"')

    def test_a_site_favicon_replaces_them(self):
        cfg = SiteSettings.load()
        cfg.favicon.name = "site/fav.png"
        cfg.save()
        html = self.client.get(reverse("accounts:login")).content.decode()
        self.assertIn("site/fav.png", html)
        self.assertNotIn("apple-touch-icon", html)
        self.assertNotIn("brand/favicon", html)

    def test_the_public_site_uses_them_when_neither_site_nor_theme_has_one(self):
        html = self.client.get("/fa/").content.decode()
        self.assertRegex(html, r'rel="icon" href="[^"]*brand/favicon[^"]*\.ico"')
        self.assertRegex(html, r'rel="apple-touch-icon" href="[^"]*brand/png/apple-touch-icon[^"]*\.png"')
        self.assertNotIn('href="data:,"', html)


class StandalonePageTests(TestCase):
    def render(self):
        from django.test import RequestFactory

        from apps.core.errorpages import standalone_response

        return standalone_response(RequestFactory().get("/"), 503, "تعمیر", "پیام", refresh=30).content.decode()

    def test_maintenance_and_error_pages_carry_both_logos_inline_and_switch_in_dark_mode(self):
        html = self.render()
        self.assertEqual(html.count("<svg"), 2)  # light + dark, inline: no dependency on static files or the database
        self.assertIn('fill="#FFFFFF"', html)  # the dark logo's white text
        self.assertIn("@media (prefers-color-scheme:dark)", html)
        self.assertRegex(html, r"\.logo-light\{display:none\}\s*\.logo-dark\{display:block\}")
        self.assertNotIn("/static/", html)

    def test_the_site_error_page_has_the_logo(self):
        from django.test import RequestFactory

        from apps.core import views

        request = RequestFactory().get("/panel/nothing/")
        request.user = mock.Mock(is_authenticated=False)
        response = views.not_found(request, Exception())
        self.assertEqual(response.status_code, 404)
        self.assertIn("brand/ravix-logo.svg", response.content.decode())


class EmailTests(TestCase):
    def setUp(self):
        self.user = make_user("mailer", Role.USER)

    def parts(self, message):
        parsed = message_from_bytes(message.message().as_bytes())
        return {p.get_content_type(): p for p in parsed.walk()}, parsed

    def test_verification_email_has_text_html_and_an_inline_png_logo(self):
        from apps.accounts import emailing
        from django.test import RequestFactory

        request = RequestFactory().get("/")
        self.assertTrue(emailing.send_verification_email(request, self.user))
        msg = mail.outbox[0]
        self.assertIn("http", msg.body)  # the plain text is unchanged and complete
        parts, parsed = self.parts(msg)
        self.assertEqual(parsed.get_content_type(), "multipart/related")
        self.assertIn("text/plain", parts)
        html = parts["text/html"].get_payload(decode=True).decode()
        self.assertIn('src="cid:ravix-logo"', html)
        png = parts["image/png"]
        self.assertEqual(png["Content-ID"], "<ravix-logo>")
        self.assertEqual(png.get_payload(decode=True)[:8], b"\x89PNG\r\n\x1a\n")
        link = re.search(r"https?://\S+", msg.body).group(0)
        self.assertIn(f'<a href="{link}"', html)

    def test_password_reset_email_is_branded_too(self):
        self.client.post(reverse("accounts:password_reset"), {"email": "mailer@example.com"})
        msg = mail.outbox[0]
        parts, _ = self.parts(msg)
        self.assertIn("text/html", parts)
        self.assertIn("image/png", parts)
        self.assertIn("/accounts/password-reset/", msg.body)

    def test_html_part_is_escaped_and_follows_the_language_direction(self):
        from django.utils import translation

        with translation.override("fa"):
            html = mailhtml.build_message("s", "<script>alert(1)</script>\n\nhttp://a.test/x?a=1&b=2", ["x@example.com"]).alternatives[0][0]
        self.assertIn('dir="rtl"', html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn('href="http://a.test/x?a=1&amp;b=2"', html)
        with translation.override("en"):
            self.assertIn('dir="ltr"', mailhtml.build_message("s", "hi", ["x@example.com"]).alternatives[0][0])

    def test_a_missing_logo_file_still_sends_the_plain_message(self):
        with mock.patch.object(mailhtml, "LOGO_PATH", Path("/nonexistent/logo.png")), self.assertLogs("apps.core.mailhtml", "WARNING"):
            message = mailhtml.build_message("subject", "body", ["x@example.com"])
        self.assertEqual((message.subject, message.body, message.alternatives), ("subject", "body", []))
        self.assertEqual(message.send(), 1)
