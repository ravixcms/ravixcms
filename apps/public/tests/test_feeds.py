"""فید RSS/Atom: همهٔ نوشته‌ها، دسته، برچسب؛ تاریخ‌ها، حذف پیش‌نویس/آینده، لینک <head>، ترتیب مسیرها."""
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from xml.etree import ElementTree as ET

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone

from apps.content.models import Category, Page, Post, PublishStatus, Tag
from apps.core.models import SiteSettings

from .test_public import BODY, make_post

ATOM = "{http://www.w3.org/2005/Atom}"


def rss_items(response):
    root = ET.fromstring(response.content)
    return root, root.find("channel").findall("item")


class FeedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        s = SiteSettings.load()
        s.site_name, s.description = "راویکس آزمایشی", "توضیح سایت"
        s.save()
        cls.cat = Category.objects.create(name="فناوری", slug="tech", description="دستهٔ فناوری")
        cls.child = Category.objects.create(name="وب", slug="web", parent=cls.cat)
        cls.other = Category.objects.create(name="ورزش", slug="sport")
        cls.tag = Tag.objects.create(name="جنگو", slug="django")
        cls.published_at = datetime(2026, 10, 20, 10, 0, 0, tzinfo=dt_timezone.utc)
        cls.first = Post.objects.create(title="نوشتهٔ اول", slug="first", body=BODY, excerpt="خلاصهٔ اول", status=PublishStatus.PUBLISHED,
                                        publish_at=cls.published_at)
        cls.first.categories.set([cls.cat])
        cls.first.tags.set([cls.tag])
        cls.second = make_post("نوشتهٔ دوم", "second", days=5, cats=[cls.child])
        cls.sport = make_post("خبر ورزشی", "sport-news", days=2, cats=[cls.other])
        cls.draft = make_post("پیش‌نویس مخفی", "hidden-draft", status=PublishStatus.DRAFT)
        cls.future = make_post("نوشتهٔ آینده", "future", status=PublishStatus.SCHEDULED, days=-5)
        cls.due = make_post("زمان‌بندی‌شدهٔ رسیده", "due", status=PublishStatus.SCHEDULED, days=0)
        Post.objects.filter(pk=cls.due.pk).update(publish_at=timezone.now() - timedelta(minutes=5))
        Page.objects.create(title="درباره", slug="about", body="x", status="published")

    def setUp(self):
        cache.clear()

    # ----------------------------------------------------------- RSS کل سایت
    def test_site_rss_is_valid_xml_with_channel_data(self):
        r = self.client.get("/feed/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("application/rss+xml", r["Content-Type"])
        root, items = rss_items(r)
        self.assertEqual(root.tag, "rss")
        ch = root.find("channel")
        self.assertEqual(ch.findtext("title"), "راویکس آزمایشی")
        self.assertEqual(ch.findtext("description"), "توضیح سایت")
        self.assertEqual(ch.findtext("language"), "fa")
        self.assertTrue(ch.findtext("link").startswith("http"))

    def test_only_live_posts_newest_first(self):
        _, items = rss_items(self.client.get("/feed/"))
        titles = [i.findtext("title") for i in items]
        self.assertNotIn("پیش‌نویس مخفی", titles)
        self.assertNotIn("نوشتهٔ آینده", titles)
        self.assertIn("زمان‌بندی‌شدهٔ رسیده", titles)  # زمان‌بندی‌شدهٔ رسیده هم منتشرشده حساب می‌شود
        self.assertEqual(len(titles), 4)
        dates = [Post.objects.get(title=t).publish_at for t in titles]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_pub_date_is_publish_at_in_rfc2822_with_correct_offset(self):
        _, items = rss_items(self.client.get("/feed/"))
        first = next(i for i in items if i.findtext("title") == "نوشتهٔ اول")
        self.assertEqual(first.findtext("pubDate"), "Tue, 20 Oct 2026 10:00:00 +0000")

    def test_pub_date_not_created_at_for_late_published_post(self):
        old = make_post("قدیمی‌ساخت، تازه‌منتشر", "late", days=0)
        Post.objects.filter(pk=old.pk).update(created_at=datetime(2020, 1, 1, tzinfo=dt_timezone.utc))
        _, items = rss_items(self.client.get("/feed/"))
        item = next(i for i in items if i.findtext("title") == "قدیمی‌ساخت، تازه‌منتشر")
        self.assertNotIn("2020", item.findtext("pubDate"))

    def test_item_fields(self):
        _, items = rss_items(self.client.get("/feed/"))
        first = next(i for i in items if i.findtext("title") == "نوشتهٔ اول")
        self.assertEqual(first.findtext("link"), "http://testserver/blog/first/")
        self.assertEqual(first.findtext("guid"), "http://testserver/blog/first/")
        self.assertEqual(first.findtext("description"), "خلاصهٔ اول")
        self.assertEqual(sorted(c.text for c in first.findall("category")), ["جنگو", "فناوری"])

    def test_last_build_date_matches_newest_item(self):
        root, _ = rss_items(self.client.get("/feed/"))
        newest = max(Post.objects.live(), key=lambda p: p.updated_at).updated_at
        got = root.find("channel").findtext("lastBuildDate")
        self.assertRegex(got, r"^\w{3}, \d{2} \w{3} \d{4} \d{2}:\d{2}:\d{2} [+-]\d{4}$")
        self.assertIn(str(newest.year), got)

    def test_last_modified_and_conditional_get(self):
        r = self.client.get("/feed/")
        self.assertTrue(r.has_header("Last-Modified"))
        r2 = self.client.get("/feed/", HTTP_IF_NONE_MATCH=r["ETag"])
        self.assertEqual(r2.status_code, 304)
        self.assertEqual(r["Cache-Control"], "public, max-age=0, must-revalidate")

    def test_item_limit_setting(self):
        with override_settings(RAVIN_FEED_ITEMS=2):
            _, items = rss_items(self.client.get("/feed/"))
        self.assertEqual(len(items), 2)

    def test_empty_feed_is_valid(self):
        Post.objects.all().delete()
        r = self.client.get("/feed/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(rss_items(r)[1], [])

    # ----------------------------------------------------------- Atom
    def test_atom_feed(self):
        r = self.client.get("/feed/atom/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("application/atom+xml", r["Content-Type"])
        root = ET.fromstring(r.content)
        self.assertEqual(root.tag, ATOM + "feed")
        self.assertEqual(root.findtext(ATOM + "title"), "راویکس آزمایشی")
        entries = root.findall(ATOM + "entry")
        self.assertEqual(len(entries), 4)
        e = next(x for x in entries if x.findtext(ATOM + "title") == "نوشتهٔ اول")
        self.assertEqual(e.findtext(ATOM + "published"), "2026-10-20T10:00:00+00:00")
        self.assertTrue(e.findtext(ATOM + "updated").endswith(("Z", "+00:00", "+03:30")))
        self.assertTrue(e.find(ATOM + "author").findtext(ATOM + "name"))  # Atom بدون نویسنده نامعتبر است
        self.assertEqual(e.find(ATOM + "link").get("href"), "http://testserver/blog/first/")

    # ----------------------------------------------------------- دسته و برچسب
    def test_category_feed_includes_descendants_only(self):
        _, items = rss_items(self.client.get("/category/tech/feed/"))
        titles = {i.findtext("title") for i in items}
        self.assertEqual(titles, {"نوشتهٔ اول", "نوشتهٔ دوم"})  # «وب» زیردستهٔ «فناوری» است؛ «ورزش» نه
        root, _ = rss_items(self.client.get("/category/tech/feed/"))
        self.assertEqual(root.find("channel").findtext("title"), "راویکس آزمایشی — فناوری")
        self.assertEqual(root.find("channel").findtext("description"), "دستهٔ فناوری")

    def test_child_category_feed_excludes_parent_posts(self):
        _, items = rss_items(self.client.get("/category/web/feed/"))
        self.assertEqual({i.findtext("title") for i in items}, {"نوشتهٔ دوم"})

    def test_tag_feed(self):
        r = self.client.get("/tag/django/feed/")
        root, items = rss_items(r)
        self.assertEqual({i.findtext("title") for i in items}, {"نوشتهٔ اول"})
        self.assertIn("جنگو", root.find("channel").findtext("title"))

    def test_category_and_tag_atom(self):
        for url, want in (("/category/tech/feed/atom/", 2), ("/tag/django/feed/atom/", 1)):
            root = ET.fromstring(self.client.get(url).content)
            self.assertEqual(len(root.findall(ATOM + "entry")), want, url)

    def test_unknown_slugs_are_404(self):
        for url in ("/category/nope/feed/", "/tag/nope/feed/", "/category/nope/feed/atom/", "/tag/nope/feed/atom/"):
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_post_only_methods(self):
        self.assertEqual(self.client.post("/feed/").status_code, 405)
        self.assertEqual(self.client.head("/feed/").status_code, 200)

    # ----------------------------------------------------------- مسیرها
    def test_routes_resolve_to_feeds_not_page_catch_all(self):
        for url, name in (("/feed/", "feed"), ("/feed/atom/", "feed_atom"), ("/category/tech/feed/", "category_feed"),
                          ("/tag/django/feed/", "tag_feed")):
            self.assertEqual(resolve(url).view_name, f"public:{name}", url)
        self.assertEqual(reverse("public:feed"), "/feed/")
        self.assertEqual(resolve("/about/").view_name, "public:page_detail")  # صفحه ثابت عادی هنوز کار می‌کند

    def test_feed_slug_is_reserved_for_pages(self):
        from apps.core.utils import RESERVED_PAGE_SLUGS

        self.assertIn("feed", RESERVED_PAGE_SLUGS)

    # ----------------------------------------------------------- لینک <head>
    def test_default_theme_head_links_site_feeds(self):
        html = self.client.get("/").content.decode()
        self.assertIn('<link rel="alternate" type="application/rss+xml" title="راویکس آزمایشی (RSS)" href="http://testserver/feed/">', html)
        self.assertIn('type="application/atom+xml" title="راویکس آزمایشی (Atom)" href="http://testserver/feed/atom/"', html)
        self.assertLess(html.index("application/rss+xml"), html.index("</head>"))

    def test_category_page_head_lists_category_feed_first(self):
        html = self.client.get("/category/tech/").content.decode()
        hrefs = re.findall(r'type="application/rss\+xml"[^>]*href="([^"]+)"', html)
        self.assertEqual(hrefs, ["http://testserver/category/tech/feed/", "http://testserver/feed/"])

    def test_tag_page_head_lists_tag_feed(self):
        html = self.client.get("/tag/django/").content.decode()
        self.assertIn('href="http://testserver/tag/django/feed/"', html)

    def test_post_page_and_404_page_also_have_site_feed(self):
        self.assertIn('href="http://testserver/feed/"', self.client.get("/blog/first/").content.decode())
        self.assertIn('href="http://testserver/feed/"', self.client.get("/no-such-page/").content.decode())

    @override_settings(SITE_URL="https://cms.example.org")
    def test_links_use_site_url_not_host_header(self):
        r = self.client.get("/feed/", HTTP_HOST="localhost")
        root, items = rss_items(r)
        self.assertEqual(root.find("channel").findtext("link"), "https://cms.example.org/")
        self.assertTrue(all(i.findtext("link").startswith("https://cms.example.org/blog/") for i in items))
        head = self.client.get("/", HTTP_HOST="localhost").content.decode()
        self.assertIn('href="https://cms.example.org/feed/"', head)

    def test_feed_title_with_special_characters_is_escaped(self):
        s = SiteSettings.load()
        s.site_name = 'سایت "x" <b>&'
        s.save()
        r = self.client.get("/feed/")
        ET.fromstring(r.content)  # XML معتبر می‌ماند
        head = self.client.get("/").content.decode()
        self.assertNotIn("<b>&", head.split("</head>")[0].split("application/rss+xml")[1][:80])

    def test_robots_and_sitemap_unaffected(self):
        self.assertEqual(self.client.get("/robots.txt").status_code, 200)
        self.assertEqual(self.client.get("/sitemap.xml").status_code, 200)
