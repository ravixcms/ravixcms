"""Language URL prefix on the public site: /fa/, /en/, /de/ … redirects for old URLs, hreflang, per-language sitemaps."""
import re
from xml.etree import ElementTree as ET

from django.conf import settings
from django.test import TestCase
from django.urls import reverse, translate_url

from apps.content.models import Page, Post, PublishStatus
from apps.core.models import SiteSettings
from apps.core.regional import split_language_prefix, swap_language_prefix

from .test_public import BODY

LANGS = [code for code, _ in settings.LANGUAGES]
COOKIE = settings.LANGUAGE_COOKIE_NAME


def alternates(html):
    return dict(re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)">', html))


class PrefixHelperTests(TestCase):
    def test_split_and_swap(self):
        self.assertEqual(split_language_prefix("/de/blog/x/"), ("de", "/blog/x/"))
        self.assertEqual(split_language_prefix("/de"), ("de", "/"))
        self.assertEqual(split_language_prefix("/pt-br/"), ("pt-br", "/"))
        self.assertEqual(split_language_prefix("/blog/x/"), ("", "/blog/x/"))
        self.assertEqual(split_language_prefix("/xx/blog/"), ("", "/xx/blog/"))
        self.assertEqual(swap_language_prefix("/en/blog/x/", "zh-hans"), "/zh-hans/blog/x/")
        self.assertIsNone(swap_language_prefix("/blog/x/", "en"))

    def test_reverse_is_prefixed_and_translatable(self):
        self.assertEqual(reverse("public:post_list"), "/fa/blog/")
        self.assertEqual(translate_url("/fa/blog/x/", "de"), "/de/blog/x/")
        self.assertEqual(translate_url("/fa/", "pt-br"), "/pt-br/")

    def test_page_slugs_equal_to_a_language_code_are_reserved(self):
        from apps.core.utils import RESERVED_PAGE_SLUGS

        for code in LANGS:
            self.assertIn(code, RESERVED_PAGE_SLUGS)


class PublicPrefixTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        SiteSettings.objects.update_or_create(pk=1, defaults={"content_per_language": False})  # these tests are about the URL prefix, not about separating content
        cls.post = Post.objects.create(title="نوشتهٔ اول", slug="first", body=BODY, excerpt="خلاصه", status=PublishStatus.PUBLISHED,
                                       publish_at="2020-01-01T00:00:00Z")
        cls.page = Page.objects.create(title="درباره", slug="about", body="x", status="published")

    def set_default(self, code):
        site = SiteSettings.load()
        site.language = code
        site.save()

    # ----------------------------------------------------------- old URLs redirect
    def test_unprefixed_urls_redirect_to_the_site_default(self):
        for old, new in (("/", "/fa/"), ("/blog/", "/fa/blog/"), ("/blog/first/", "/fa/blog/first/"), ("/about/", "/fa/about/"),
                         ("/feed/", "/fa/feed/")):
            r = self.client.get(old)
            self.assertEqual((r.status_code, r["Location"]), (301, new), old)

    def test_default_follows_site_settings(self):
        self.set_default("de")
        r = self.client.get("/blog/first/")
        self.assertEqual((r.status_code, r["Location"]), (301, "/de/blog/first/"))
        self.assertIn("max-age", r["Cache-Control"])  # the default can change, so not cached forever

    def test_cookie_beats_site_default_and_is_not_permanent(self):
        self.set_default("de")
        self.client.cookies[COOKIE] = "es"
        r = self.client.get("/blog/")
        self.assertEqual((r.status_code, r["Location"]), (302, "/es/blog/"))

    def test_query_string_survives(self):
        self.assertEqual(self.client.get("/blog/?page=2")["Location"], "/fa/blog/?page=2")

    def test_missing_trailing_slash_is_one_hop(self):
        self.assertEqual(self.client.get("/blog")["Location"], "/fa/blog/")
        r = self.client.get("/en/blog")
        self.assertEqual((r.status_code, r["Location"]), (301, "/en/blog/"))
        self.assertEqual(self.client.get("/en")["Location"], "/en/")

    def test_unknown_pages_stay_404(self):
        self.assertEqual(self.client.get("/fa/no-such/").status_code, 404)
        self.assertEqual(self.client.get("/en/blog/nope/").status_code, 404)
        self.assertEqual(self.client.get("/wp-login.php").status_code, 404)

    def test_post_to_unprefixed_url_is_not_redirected(self):
        self.assertEqual(self.client.post("/blog/first/comment/", {}).status_code, 404)

    def test_system_urls_are_not_prefixed(self):
        self.assertEqual(self.client.get("/accounts/login/").status_code, 200)
        self.assertEqual(self.client.get("/panel/").status_code, 302)
        self.assertEqual(self.client.get("/robots.txt").status_code, 200)
        self.assertEqual(self.client.get("/sitemap.xml").status_code, 200)
        self.assertEqual(self.client.get("/healthz").status_code, 200)

    # ----------------------------------------------------------- the prefix chooses the language
    def test_each_prefix_serves_its_language_and_direction(self):
        for code in LANGS:
            r = self.client.get(f"/{code}/blog/first/")
            self.assertEqual(r.status_code, 200, code)
            html = r.content.decode()
            rtl = code in ("fa", "ar")
            self.assertIn(f'lang="{code}"', html)
            self.assertIn(f'dir="{"rtl" if rtl else "ltr"}"', html, code)

    def test_prefix_beats_cookie_and_site_default(self):
        self.set_default("de")
        self.client.cookies[COOKIE] = "es"
        self.assertIn('lang="ar"', self.client.get("/ar/").content.decode())

    def test_links_inside_the_page_keep_the_language(self):
        html = self.client.get("/en/blog/first/").content.decode()
        self.assertIn('href="/en/blog/"', html)
        self.assertNotIn('href="/fa/', html)

    def test_custom_internal_menu_url_gets_the_language_prefix(self):
        from apps.themes.models import ThemeConfig
        from apps.themes import services

        cfg = ThemeConfig.load()
        doc = services.published_doc(cfg)
        doc["header"] = {**doc["header"], "cta_label": "Go", "cta_url": "/about/"}
        cfg.published = doc
        cfg.save()
        self.assertIn('href="/de/about/"', self.client.get("/de/").content.decode())
        self.assertIn('href="/fa/about/"', self.client.get("/fa/").content.decode())

    def test_old_slug_redirect_keeps_the_visitors_language(self):
        self.page.slug = "about-us"
        self.page.save()
        for old, new in (("/de/about/", "/de/about-us/"), ("/about/", "/fa/about-us/")):
            r = self.client.get(old)
            self.assertEqual((r.status_code, r["Location"]), (301, new), old)

    def test_switcher_translates_the_current_url(self):
        r = self.client.post(reverse("set_language"), {"language": "de", "next": "/en/blog/first/"})
        self.assertEqual(r["Location"], "/de/blog/first/")

    # ----------------------------------------------------------- hreflang / canonical
    def test_hreflang_alternates_cover_every_language(self):
        html = self.client.get("/en/blog/first/").content.decode()
        found = alternates(html)
        self.assertEqual(len(found), len(LANGS) + 1)
        self.assertEqual(found["de"], "http://testserver/de/blog/first/")
        self.assertEqual(found["pt-BR"], "http://testserver/pt-br/blog/first/")
        self.assertEqual(found["zh-Hans"], "http://testserver/zh-hans/blog/first/")
        self.assertEqual(found["x-default"], "http://testserver/fa/blog/first/")  # the site default language
        self.assertIn('<link rel="canonical" href="http://testserver/en/blog/first/">', html)

    def test_x_default_follows_the_site_default(self):
        self.set_default("tr")
        self.assertEqual(alternates(self.client.get("/en/").content.decode())["x-default"], "http://testserver/tr/")

    def test_noindex_pages_have_no_alternates(self):
        Post.objects.filter(pk=self.post.pk).update(noindex=True)
        self.assertEqual(alternates(self.client.get("/en/blog/first/").content.decode()), {})

    def test_404_page_has_no_alternates(self):
        self.assertEqual(alternates(self.client.get("/en/nothing/").content.decode()), {})

    # ----------------------------------------------------------- sitemaps / robots
    def test_sitemap_index_lists_one_sitemap_per_language(self):
        r = self.client.get("/sitemap.xml")
        root = ET.fromstring(r.content)
        locs = [e.text for e in root.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
        self.assertEqual(locs, [f"http://testserver/{code}/sitemap.xml" for code in LANGS])

    def test_language_sitemap_has_prefixed_urls_and_alternates(self):
        r = self.client.get("/de/sitemap.xml")
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        root = ET.fromstring(r.content)
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9", "x": "http://www.w3.org/1999/xhtml"}
        locs = [e.text for e in root.findall("s:url/s:loc", ns)]
        self.assertIn("http://testserver/de/blog/first/", locs)
        self.assertIn("http://testserver/de/about/", locs)
        self.assertTrue(all(loc.startswith("http://testserver/de/") for loc in locs))
        first = next(u for u in root.findall("s:url", ns) if u.findtext("s:loc", namespaces=ns).endswith("/blog/first/"))
        links = {l.get("hreflang"): l.get("href") for l in first.findall("x:link", ns)}
        self.assertEqual(len(links), len(LANGS) + 1)
        self.assertEqual(links["en"], "http://testserver/en/blog/first/")
        self.assertEqual(links["x-default"], "http://testserver/fa/blog/first/")
        self.assertNotIn("/panel/", body)

    def test_home_and_blog_in_every_language_sitemap_carry_hreflang_for_all_languages(self):
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9", "x": "http://www.w3.org/1999/xhtml"}
        for code in ("fa", "en", "zh-hans"):
            root = ET.fromstring(self.client.get(f"/{code}/sitemap.xml").content)
            by_loc = {u.findtext("s:loc", namespaces=ns): u for u in root.findall("s:url", ns)}
            for tail in ("", "blog/"):
                links = {l.get("hreflang"): l.get("href") for l in by_loc[f"http://testserver/{code}/{tail}"].findall("x:link", ns)}
                self.assertEqual(len(links), len(LANGS) + 1, (code, tail))
                self.assertEqual(links["x-default"], f"http://testserver/fa/{tail}")
                self.assertEqual(links["pt-BR"], f"http://testserver/pt-br/{tail}")

    def test_robots_points_to_the_index_and_blocks_internal_urls_in_every_language(self):
        text = self.client.get("/robots.txt").content.decode()
        self.assertIn("Sitemap: http://testserver/sitemap.xml", text)
        for code in LANGS:
            self.assertIn(f"Disallow: /{code}/search/", text)
        self.assertIn("Disallow: /panel/", text)


class MixedDirectionContentTests(TestCase):
    """A Persian post in an English page (and the reverse) is ordered and aligned by its own text, not by the page direction."""

    @classmethod
    def setUpTestData(cls):
        SiteSettings.objects.update_or_create(pk=1, defaults={"content_per_language": False})
        Post.objects.create(title="عنوان فارسی", slug="fa-post", body="متن **فارسی**\n\n- یک\n- دو", excerpt="خلاصه",
                            status=PublishStatus.PUBLISHED, publish_at="2020-01-01T00:00:00Z")

    def test_title_excerpt_and_body_blocks_have_dir_auto(self):
        html = self.client.get("/en/blog/fa-post/").content.decode()
        self.assertIn('dir="ltr"', html)  # the page itself stays English/LTR
        self.assertIn('<h1 itemprop="headline" dir="auto">', html)
        self.assertIn('<p class="article-lead" dir="auto">', html)
        self.assertIn('<p dir="auto">', html)
        self.assertIn('<ul dir="auto">', html)

    def test_listing_rows_have_dir_auto(self):
        html = self.client.get("/en/blog/").content.decode()
        self.assertRegex(html, r'<h2 class="row-title" dir="auto">')
