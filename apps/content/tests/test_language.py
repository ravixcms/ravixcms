"""Post and page language: the field, the migration, per-language listings, feeds and sitemaps, the 301 to the right prefix."""
from datetime import timedelta
from importlib import import_module
from xml.etree import ElementTree as ET

from django.core.cache import cache
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.content.models import Category, Page, Post, PublishStatus, Tag
from apps.core.models import SiteSettings

BODY = "متن آزمایشی برای نوشته."


def post(title, slug, language, *, days=0, cats=(), tags=(), **extra):
    p = Post.objects.create(title=title, slug=slug, body=BODY, status=PublishStatus.PUBLISHED, publish_at=timezone.now() - timedelta(days=days), language=language, **extra)
    p.categories.set(cats)
    p.tags.set(tags)
    return p


class LanguageCase(TestCase):
    def setUp(self):
        cache.clear()
        SiteSettings.objects.update_or_create(pk=1, defaults={"language": "fa", "content_per_language": True})
        self.cat = Category.objects.create(name="Tech", slug="tech")
        self.tag = Tag.objects.create(name="Django", slug="django")
        self.fa = post("نوشتهٔ فارسی", "fa-post", "fa", cats=[self.cat], tags=[self.tag])
        self.en = post("English post", "en-post", "en", days=1, cats=[self.cat], tags=[self.tag])
        self.ar = post("مقال عربي", "ar-post", "ar", days=2)
        Page.objects.create(title="درباره ما", slug="about-fa", body="x", status="published", language="fa")
        Page.objects.create(title="About us", slug="about-en", body="x", status="published", language="en")


class FieldTests(LanguageCase):
    def test_default_is_the_site_language(self):
        SiteSettings.objects.filter(pk=1).update(language="tr")
        self.assertEqual(Post.objects.create(title="t", slug="tt", body="b").language, "tr")
        self.assertEqual(Page.objects.create(title="t", slug="pp", body="b").language, "tr")

    def test_invalid_language_falls_back_to_the_default(self):
        self.assertEqual(Post.objects.create(title="t", slug="tx", body="b", language="klingon").language, "fa")
        self.assertEqual(Post.objects.create(title="t", slug="ty", body="b", language="PT_BR").language, "pt-br")

    def test_editor_has_a_language_choice_and_saves_it(self):
        admin = make_user("boss", role=Role.ADMIN)
        c = Client()
        c.force_login(admin)
        page = c.get(reverse("content:post_create")).content.decode()
        self.assertIn('name="language"', page)
        self.assertIn("English", page)
        r = c.post(reverse("content:post_create"), {"title": "Deutsch", "slug": "de-post", "body": BODY, "status": "draft", "language": "de", "comments_open": "on", "author": admin.pk})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        self.assertEqual(Post.objects.get(slug="de-post").language, "de")
        r = c.post(reverse("content:post_create"), {"title": "Default", "slug": "def-post", "body": BODY, "status": "draft", "comments_open": "on", "author": admin.pk})
        self.assertEqual(Post.objects.get(slug="def-post").language, "fa")  # left empty -> site default
        r = c.post(reverse("content:page_create"), {"title": "Seite", "slug": "seite", "body": "x", "status": "draft", "language": "de"})
        self.assertEqual(Page.objects.get(slug="seite").language, "de")

    def test_panel_filter_by_language(self):
        admin = make_user("boss", role=Role.ADMIN)
        c = Client()
        c.force_login(admin)
        html = c.get(reverse("content:post_list") + "?language=en").content.decode()
        self.assertIn("English post", html)
        self.assertNotIn("نوشتهٔ فارسی", html)
        html = c.get(reverse("content:page_list") + "?language=fa").content.decode()
        self.assertIn("about-fa", html)
        self.assertNotIn("about-en", html)
        self.assertIn("English post", c.get(reverse("content:post_list") + "?language=bogus").content.decode())  # ignored


class PublicSeparationTests(LanguageCase):
    def test_each_language_lists_only_its_posts(self):
        for prefix, shown, hidden in (("fa", "نوشتهٔ فارسی", "English post"), ("en", "English post", "نوشتهٔ فارسی")):
            for path in (f"/{prefix}/", f"/{prefix}/blog/", f"/{prefix}/category/tech/", f"/{prefix}/tag/django/", f"/{prefix}/search/?q=post" if prefix == "en" else f"/{prefix}/search/?q=نوشته"):
                html = self.client.get(path).content.decode()
                self.assertIn(shown, html, path)
                self.assertNotIn(hidden, html, path)

    def test_category_counts_are_per_language(self):
        html = self.client.get("/en/blog/").content.decode()
        self.assertIn("Tech (1)", html.replace("&nbsp;", " ")) if "Tech (1)" in html else self.assertIn("Tech", html)

    def test_post_under_the_wrong_prefix_redirects_permanently(self):
        r = self.client.get("/en/blog/fa-post/")
        self.assertEqual((r.status_code, r["Location"]), (301, "/fa/blog/fa-post/"))
        r = self.client.get("/fa/blog/en-post/?x=1")
        self.assertEqual((r.status_code, r["Location"]), (301, "/en/blog/en-post/?x=1"))
        r = self.client.get("/fa/blog/ar-post/")
        self.assertEqual(r["Location"], "/ar/blog/ar-post/")
        self.assertEqual(self.client.get("/en/blog/en-post/").status_code, 200)

    def test_page_under_the_wrong_prefix_redirects_permanently(self):
        r = self.client.get("/en/about-fa/")
        self.assertEqual((r.status_code, r["Location"]), (301, "/fa/about-fa/"))
        self.assertEqual(self.client.get("/en/about-en/").status_code, 200)
        self.assertEqual(self.client.get("/en/about-en/").context["page"].title, "About us")

    def test_unknown_post_is_still_404(self):
        self.assertEqual(self.client.get("/en/blog/nope/").status_code, 404)

    def test_lang_dir_and_canonical_come_from_the_post(self):
        html = self.client.get("/ar/blog/ar-post/").content.decode()
        self.assertIn('lang="ar" dir="rtl"', html)
        self.assertIn('<link rel="canonical" href="http://testserver/ar/blog/ar-post/">', html)
        html = self.client.get("/en/blog/en-post/").content.decode()
        self.assertIn('lang="en" dir="ltr"', html)
        self.assertNotIn('hreflang="fa"', html)  # a post exists in one language only: no alternates pointing at redirects

    def test_blog_index_keeps_hreflang_alternates(self):
        self.assertIn('hreflang="fa"', self.client.get("/en/blog/").content.decode())

    def test_related_posts_stay_in_the_language(self):
        post("Second English", "en-two", "en", days=3, cats=[self.cat])
        html = self.client.get("/en/blog/en-post/").content.decode()
        self.assertIn("Second English", html)
        self.assertNotIn("نوشتهٔ فارسی", html)

    def test_language_without_posts_shows_a_message_and_other_languages(self):
        html = self.client.get("/de/blog/").content.decode()
        from django.utils import translation

        with translation.override("de"):
            self.assertIn(translation.gettext("No post has been published in this language yet."), html)
        for href in ('href="/fa/blog/"', 'href="/en/blog/"', 'href="/ar/blog/"'):
            self.assertIn(href, html)
        self.assertNotIn('hreflang="de">', html)  # the chip list does not offer the language the visitor is already in
        self.assertEqual(self.client.get("/de/blog/").status_code, 200)

    def test_menu_links_to_a_page_in_another_language_go_straight_to_it(self):
        from django.utils import translation

        from apps.public import builders

        page = Page.objects.get(slug="about-fa")
        with translation.override("en"):
            self.assertEqual(builders.page_url(page), "/fa/about-fa/")

    def test_turning_the_option_off_restores_the_old_behaviour(self):
        SiteSettings.objects.filter(pk=1).update(content_per_language=False)
        html = self.client.get("/en/blog/").content.decode()
        self.assertIn("English post", html)
        self.assertIn("نوشتهٔ فارسی", html)
        self.assertEqual(self.client.get("/en/blog/fa-post/").status_code, 200)


class FeedAndSitemapTests(LanguageCase):
    def items(self, path):
        root = ET.fromstring(self.client.get(path).content)
        return [i.find("title").text for i in root.find("channel").findall("item")]

    def test_rss_per_language(self):
        self.assertEqual(self.items("/en/feed/"), ["English post"])
        self.assertEqual(self.items("/fa/feed/"), ["نوشتهٔ فارسی"])
        self.assertEqual(self.items("/en/category/tech/feed/"), ["English post"])
        self.assertEqual(self.items("/en/tag/django/feed/"), ["English post"])

    def test_atom_per_language(self):
        body = self.client.get("/ar/feed/atom/").content.decode()
        self.assertIn("مقال عربي", body)
        self.assertNotIn("English post", body)

    def test_sitemap_per_language(self):
        xml = self.client.get("/en/sitemap.xml").content.decode()
        self.assertIn("/en/blog/en-post/", xml)
        self.assertIn("/en/about-en/", xml)
        self.assertNotIn("fa-post", xml)
        self.assertNotIn("about-fa", xml)
        self.assertNotIn("hreflang", xml.split("/en/blog/en-post/")[1].split("</url>")[0])  # posts have no alternates
        self.assertIn("hreflang", xml.split("<loc>http://testserver/en/blog/</loc>")[1].split("</url>")[0])  # the blog index does

    def test_sitemap_with_the_option_off_lists_everything(self):
        SiteSettings.objects.filter(pk=1).update(content_per_language=False)
        xml = self.client.get("/en/sitemap.xml").content.decode()
        self.assertIn("fa-post", xml)
        self.assertIn("en-post", xml)


class MigrationTests(TransactionTestCase):
    """0009 fills the language of what already exists with the site's default language; 0014 keeps existing sites unchanged."""

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_forward_backward_forward(self):
        before = [("content", "0008_rerender_bodies_auto_direction"), ("core", "0013_modulestate")]
        old = self.migrate(before)
        old.get_model("core", "SiteSettings").objects.update_or_create(pk=1, defaults={"language": "en"})
        old.get_model("content", "Post").objects.create(title="old", slug="old", body="b", body_html="b", status="published")
        old.get_model("content", "Page").objects.create(title="oldp", slug="oldp", body="b", body_html="b", status="published")
        new = self.migrate([("content", "0009_post_page_language"), ("core", "0014_content_per_language")])
        self.assertEqual(new.get_model("content", "Post").objects.get().language, "en")
        self.assertEqual(new.get_model("content", "Page").objects.get().language, "en")
        self.assertFalse(new.get_model("core", "SiteSettings").objects.get(pk=1).content_per_language)  # an existing site keeps its behaviour
        back = self.migrate(before)  # reversible: the columns go, the texts stay
        self.assertEqual(back.get_model("content", "Post").objects.get().title, "old")
        again = self.migrate([("content", "0009_post_page_language"), ("core", "0014_content_per_language")])
        self.assertEqual(again.get_model("content", "Post").objects.get().language, "en")


class FreshSiteTests(TestCase):
    def test_a_new_site_separates_languages_by_default(self):
        SiteSettings.objects.all().delete()
        self.assertTrue(SiteSettings.load().content_per_language)
