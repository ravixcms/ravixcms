import json
import re
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.content.models import Category, Page, Post, PublishStatus, Tag
from apps.core.models import SiteSettings
from apps.themes.models import ThemeConfig
from apps.themes.tests.helpers import ThemeDirMixin

BODY = "## سرتیتر\n\nمتن آزمایشی برای نوشته با **تأکید**.\n\n[پیوند](https://example.com)"


def make_post(title, slug, *, status=PublishStatus.PUBLISHED, days=0, cats=(), tags=(), **extra):
    when = timezone.now() - timedelta(days=days)
    post = Post.objects.create(title=title, slug=slug, body=BODY, status=status, publish_at=when, **extra)
    post.categories.set(cats)
    post.tags.set(tags)
    return post


class PublicSiteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cat = Category.objects.create(name="فناوری", slug="tech", description="دستهٔ فناوری")
        cls.child = Category.objects.create(name="وب", slug="web", parent=cls.cat)
        cls.tag = Tag.objects.create(name="جنگو", slug="django")
        cls.post = make_post("نوشتهٔ اول", "first", cats=[cls.cat], tags=[cls.tag])
        cls.child_post = make_post("نوشتهٔ دوم", "second", days=1, cats=[cls.child])
        cls.draft = make_post("پیش‌نویس مخفی", "hidden-draft", status=PublishStatus.DRAFT)
        cls.future = make_post("نوشتهٔ آینده", "future", status=PublishStatus.SCHEDULED, days=-5)
        cls.page = Page.objects.create(title="درباره ما", slug="about", body="## ما\n\nمتن صفحه", status="published")
        cls.hidden_page = Page.objects.create(title="صفحهٔ مخفی", slug="secret", body="x", status="draft")

    def setUp(self):
        cache.clear()

    # ----------------------------------------------------------- صفحه‌ها
    def test_home_lists_published_posts_only(self):
        resp = self.client.get("/fa/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "نوشتهٔ اول")
        self.assertNotContains(resp, "پیش‌نویس مخفی")
        self.assertNotContains(resp, "نوشتهٔ آینده")
        self.assertContains(resp, 'lang="fa" dir="rtl"')
        self.assertEqual(len(re.findall(r"<h1[ >]", resp.content.decode())), 1)

    def test_home_is_usable_with_no_content(self):
        Post.objects.all().delete()
        resp = self.client.get("/fa/")
        self.assertEqual(resp.status_code, 200)

    def test_post_list_and_pagination(self):
        for i in range(12):
            make_post(f"نوشته {i}", f"p{i}", days=i + 2)
        resp = self.client.get(reverse("public:post_list"))
        self.assertContains(resp, 'rel="next"')
        self.assertEqual(self.client.get(reverse("public:post_list") + "?page=2").status_code, 200)
        for bad in ("?page=99", "?page=0", "?page=abc", "?page=-1"):
            self.assertEqual(self.client.get(reverse("public:post_list") + bad).status_code, 404, bad)

    def test_post_detail(self):
        resp = self.client.get(reverse("public:post_detail", args=["first"]))
        self.assertContains(resp, "<strong>تأکید</strong>", html=False)
        self.assertContains(resp, "جنگو")
        self.assertContains(resp, 'rel="noopener')  # پیوند بیرونی
        self.assertContains(resp, 'property="og:title"')
        self.assertContains(resp, 'rel="canonical"')
        self.assertContains(resp, "application/ld+json")

    def test_unpublished_content_is_404(self):
        for name, slug in (("post_detail", "hidden-draft"), ("post_detail", "future"), ("page_detail", "secret"),
                           ("post_detail", "nope"), ("category_detail", "nope"), ("tag_detail", "nope")):
            self.assertEqual(self.client.get(reverse(f"public:{name}", args=[slug])).status_code, 404, slug)

    def test_category_includes_children_and_tag_page(self):
        resp = self.client.get(reverse("public:category_detail", args=["tech"]))
        self.assertContains(resp, "نوشتهٔ اول")
        self.assertContains(resp, "نوشتهٔ دوم")  # از زیردسته
        self.assertContains(resp, "دستهٔ فناوری")
        resp = self.client.get(reverse("public:tag_detail", args=["django"]))
        self.assertContains(resp, "نوشتهٔ اول")
        self.assertNotContains(resp, "نوشتهٔ دوم")

    def test_static_page(self):
        resp = self.client.get(reverse("public:page_detail", args=["about"]))
        self.assertContains(resp, "درباره ما")
        self.assertContains(resp, "متن صفحه")

    def test_search(self):
        url = reverse("public:search")
        self.assertContains(self.client.get(url, {"q": "اول"}), "نوشتهٔ اول")
        self.assertNotContains(self.client.get(url, {"q": "مخفی"}), "پیش‌نویس مخفی")
        resp = self.client.get(url, {"q": "ما"})
        self.assertContains(resp, "درباره ما")
        resp = self.client.get(url, {"q": "a"})
        self.assertContains(resp, "حداقل دو نویسه")
        self.assertIn("noindex", resp.content.decode())
        self.assertContains(self.client.get(url, {"q": "<script>alert(1)</script>"}), "&lt;script&gt;")

    def test_404_page_is_themed_for_public_and_plain_for_system_paths(self):
        resp = self.client.get("/fa/does-not-exist/")
        self.assertEqual(resp.status_code, 404)
        self.assertContains(resp, "site-header", status_code=404)
        self.assertContains(resp, "noindex", status_code=404)
        self.client.force_login(make_user("adm", role=Role.ADMIN))
        resp = self.client.get("/panel/zzz-nothing/")
        self.assertEqual(resp.status_code, 404)
        self.assertNotContains(resp, "site-header", status_code=404)

    def test_scanner_paths_are_plain_404_without_redirect(self):
        for path in ("/wp-login.php", "/a.b/", "/.env"):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_panel_and_system_routes_not_shadowed_by_pages(self):
        self.assertEqual(self.client.get("/accounts/login/").status_code, 200)
        self.assertEqual(self.client.get("/panel/").status_code, 302)
        self.assertEqual(self.client.get("/robots.txt").status_code, 200)

    # ----------------------------------------------------------- سئو
    def test_sitemap_and_robots(self):
        resp = self.client.get(reverse("public:sitemap"))
        body = resp.content.decode()
        self.assertEqual(resp["Content-Type"].split(";")[0], "application/xml")
        self.assertIn("/fa/blog/first/", body)
        self.assertIn("/about/", body)
        self.assertNotIn("hidden-draft", body)
        self.assertNotIn("/future/", body)
        self.assertNotIn("secret", body)
        robots = self.client.get(reverse("seo:robots")).content.decode()
        self.assertIn("Disallow: /panel/", robots)
        self.assertIn("Sitemap:", robots)

    def test_json_ld_is_escaped(self):
        make_post("</script><script>alert(1)</script>", "evil", cats=[self.cat])
        html = self.client.get(reverse("public:post_detail", args=["evil"])).content.decode()
        for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
            json.loads(block)  # JSON معتبر
            self.assertNotIn("<", block)
        self.assertNotIn("<script>alert(1)", html)

    def test_site_settings_and_footer_are_shown(self):
        s = SiteSettings.load()
        s.site_name, s.tagline, s.footer_text, s.admin_email = "سایت آزمایشی", "شعار من", "حقوق محفوظ است", "hi@example.com"
        s.save()
        html = self.client.get("/fa/").content.decode()
        self.assertIn("سایت آزمایشی", html)
        self.assertIn("شعار من", html)
        self.assertIn("حقوق محفوظ است", html)
        self.assertIn("mailto:hi@example.com", html)

    # ----------------------------------------------------------- امنیت و کارایی
    def test_security_and_cache_headers(self):
        resp = self.client.get("/fa/")
        self.assertIn("default-src 'self'", resp["Content-Security-Policy"])
        self.assertIn("script-src 'self'", resp["Content-Security-Policy"])
        self.assertEqual(resp["X-Content-Type-Options"], "nosniff")
        self.assertTrue(resp.has_header("ETag"))
        self.assertNotIn("Vary: Cookie", str(resp.headers))
        self.assertEqual(self.client.get("/fa/", HTTP_IF_NONE_MATCH=resp["ETag"]).status_code, 304)

    def test_theme_assets_have_version_and_font_preload(self):
        html = self.client.get("/fa/").content.decode()
        self.assertRegex(html, r'themes/default/css/theme\.css\?v=\d+\.\d+\.\d+')
        self.assertIn('rel="preload"', html)
        scripts = re.findall(r"<script[^>]*>", html)
        self.assertTrue(all('type="application/ld+json"' in tag for tag in scripts), scripts)  # قالب پیش‌فرض بدون JS اجرایی

    def test_constant_query_count_on_lists(self):
        # MaintenanceMiddleware حداکثر هر ۱ ثانیه یک پرس‌وجو (UpdateLock) می‌زند؛ این پرس‌وجوی زمان‌محور عامل ناپایداری تست بود.
        from unittest import mock

        from apps.updater import middleware as maintenance

        with mock.patch.dict(maintenance._cache, {"at": float("inf"), "on": False}):
            self._check_constant_query_count_on_lists()

    def _check_constant_query_count_on_lists(self):
        for i in range(8):
            make_post(f"نوشته {i}", f"q{i}", days=i + 3, cats=[self.cat], tags=[self.tag])
        self.client.get("/fa/")  # گرم‌کردن کش‌های ساده
        self.client.get("/fa/blog/")  # گرم‌کردن کش خود مسیر؛ بدون آن، گاهی (۱ از ~۶ اجرا) شمارش اول یک کوئری بیشتر داشت
        with self.assertNumQueries(self._count("/fa/blog/")):
            self.client.get("/fa/blog/")
        for i in range(8, 16):
            make_post(f"نوشته {i}", f"q{i}", days=i + 3, cats=[self.cat], tags=[self.tag])
        with self.assertNumQueries(self._count("/fa/blog/")):
            self.client.get("/fa/blog/")

    def _count(self, path):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            self.client.get(path)
        return len(ctx)

    # ----------------------------------------------------------- ظاهر و پیکربندی
    def test_appearance_variables_and_menu_come_from_config(self):
        cfg = ThemeConfig.load()
        doc = dict(cfg.published)
        doc["appearance"] = {**doc["appearance"], "primary": "#0f766e", "font": "system"}
        doc["menus"] = {"header": [{"label": "درباره", "kind": "page", "target": self.page.pk, "url": "", "new_tab": False},
                                   {"label": "پیوند", "kind": "url", "target": None, "url": "https://example.org", "new_tab": True}],
                        "footer": []}
        doc["header"] = {**doc["header"], "sticky": False, "show_search": False, "cta_label": "شروع", "cta_url": "/about/"}
        cfg.published = doc
        cfg.save()
        html = self.client.get("/fa/").content.decode()
        self.assertIn("--rv-primary:#0f766e", html)
        self.assertIn("system-ui", html)
        self.assertIn('href="/fa/about/"', html)  # an internal custom URL gets the visitor's language prefix
        self.assertIn('target="_blank" rel="noopener noreferrer"', html)
        self.assertNotIn("is-sticky", html)
        self.assertNotIn('role="search"', html.split("<main")[0])
        self.assertIn("شروع", html)

    def test_menu_item_for_unpublished_page_is_hidden(self):
        cfg = ThemeConfig.load()
        doc = dict(cfg.published)
        doc["menus"] = {"header": [{"label": "مخفی‌منو", "kind": "page", "target": self.hidden_page.pk, "url": "", "new_tab": False}], "footer": []}
        cfg.published = doc
        cfg.save()
        self.assertNotContains(self.client.get("/fa/"), "مخفی‌منو")

    def test_home_sections_follow_config(self):
        cfg = ThemeConfig.load()
        doc = dict(cfg.published)
        sec = lambda **kw: {"type": "text", "enabled": True, "title": "", "subtitle": "", "body": "", "button_label": "", "button_url": "",
                            "category": None, "page": None, "count": 6, **kw}
        doc["home"] = {"sections": [
            sec(type="hero", title="عنوان قهرمان", subtitle="زیرعنوان"),
            sec(type="category_posts", category=self.cat.pk, count=3),
            sec(type="categories"),
            sec(type="page", page=self.page.pk),
            sec(type="text", title="متن آزاد", body="خط یک\nخط دو"),
            sec(type="text", title="پنهان", body="x", enabled=False),
        ]}
        cfg.published = doc
        cfg.save()
        html = self.client.get("/fa/").content.decode()
        for needle in ("عنوان قهرمان", "زیرعنوان", "نوشتهٔ اول", "دسته‌بندی‌ها", "متن صفحه", "متن آزاد"):
            self.assertIn(needle, html)
        self.assertNotIn("پنهان", html)
        self.assertLess(html.index("عنوان قهرمان"), html.index("متن آزاد"))

    def test_logo_and_favicon_use_media_library(self):
        from apps.media_library.models import Media
        from apps.core.testing import png_bytes
        from django.core.files.uploadedfile import SimpleUploadedFile
        import shutil, tempfile

        tmp = tempfile.mkdtemp()
        try:
            with override_settings(MEDIA_ROOT=tmp):
                media = Media(title="لوگو", kind=Media.Kind.IMAGE)
                media.file.save("logo.png", SimpleUploadedFile("logo.png", png_bytes(), "image/png"), save=False)
                media.save()
                cfg = ThemeConfig.load()
                doc = dict(cfg.published)
                doc["appearance"] = {**doc["appearance"], "logo_media": media.pk, "favicon_media": media.pk}
                cfg.published = doc
                cfg.save()
                html = self.client.get("/fa/").content.decode()
                self.assertIn('class="brand-logo"', html)
                self.assertIn('rel="icon" href="/media/', html)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class PreviewVisibilityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = make_user("adm", role=Role.ADMIN)
        cfg = ThemeConfig.load()
        draft = dict(cfg.published)
        draft["appearance"] = {**draft["appearance"], "primary": "#b91c1c"}
        cfg.draft = draft
        cfg.save()

    def test_preview_requires_flag_and_permission(self):
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get("/fa/"), "--rv-primary:#b91c1c")  # بدون شروع پیش‌نمایش
        self.client.post(reverse("themes:preview_start"))
        resp = self.client.get("/fa/")
        self.assertContains(resp, "--rv-primary:#b91c1c")
        self.assertContains(resp, "حالت پیش‌نمایش")
        self.assertEqual(resp["Cache-Control"], "private, no-store")
        self.client.post(reverse("themes:preview_stop"))
        self.assertNotContains(self.client.get("/fa/"), "--rv-primary:#b91c1c")

    def test_visitors_and_non_admins_never_see_draft(self):
        self.assertNotContains(self.client.get("/fa/"), "#b91c1c")
        author = make_user("auth", role=Role.AUTHOR)
        self.client.force_login(author)
        session = self.client.session
        session["ravix_preview"] = True  # حتی با دست‌کاری نشست
        session.save()
        resp = self.client.get("/fa/")
        self.assertNotContains(resp, "#b91c1c")
        self.assertNotContains(resp, "حالت پیش‌نمایش")
        self.assertNotIn('class="rv-preview"', resp.content.decode())

    def test_frame_view_shows_draft_for_managers_only(self):
        url = reverse("themes:frame")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(make_user("auth", role=Role.AUTHOR))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.admin)
        resp = self.client.get(url)
        self.assertContains(resp, "--rv-primary:#b91c1c")
        self.assertEqual(resp["X-Frame-Options"], "SAMEORIGIN")
        self.assertNotContains(resp, 'id="rv-toolbar"')

    def test_toolbar_for_logged_in_staff_in_normal_mode(self):
        self.client.force_login(self.admin)
        self.client.get("/fa/")  # نشست ایجاد شود
        resp = self.client.get("/fa/")
        self.assertContains(resp, "پنل مدیریت")
        self.assertEqual(resp["Cache-Control"], "private, no-store")


class ThemeFallbackAndIsolationTests(ThemeDirMixin, TestCase):
    def activate(self, slug):
        cfg = ThemeConfig.load()
        cfg.active_theme = slug
        cfg.published = {**cfg.published, "theme": slug}
        cfg.save()

    def test_missing_active_theme_falls_back_to_default(self):
        self.activate("ghost")
        resp = self.client.get("/fa/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-theme="default"')

    def test_broken_theme_falls_back(self):
        self.install_theme("broken", edit=lambda p: (p / "templates" / "home.html").write_text("{% if %}", encoding="utf-8"))
        self.activate("broken")
        self.assertContains(self.client.get("/fa/"), 'data-theme="default"')

    def test_custom_theme_renders_and_cannot_leak_server_data(self):
        def edit(path):
            home = path / "templates" / "home.html"
            home.write_text('{% extends "base.html" %}{% block content %}<p>[{{ request.path }}{{ settings.SECRET_KEY }}{{ user.email }}]</p>MARK{% endblock %}', encoding="utf-8")
        self.install_theme("custom", edit=edit)
        self.activate("custom")
        resp = self.client.get("/fa/")
        self.assertContains(resp, 'data-theme="custom"')
        self.assertContains(resp, "<p>[]</p>MARK")

    def test_custom_theme_templates_are_used_per_page_type(self):
        def edit(path):
            (path / "templates" / "404.html").write_text('{% extends "base.html" %}{% block content %}CUSTOM-404{% endblock %}', encoding="utf-8")
        self.install_theme("custom", edit=edit)
        self.activate("custom")
        self.assertContains(self.client.get("/fa/nothing-here/"), "CUSTOM-404", status_code=404)

    def test_theme_static_files_are_served_with_version(self):
        from django.contrib.staticfiles import finders

        self.assertIsNotNone(finders.find("themes/default/css/theme.css"))
        self.assertIsNotNone(finders.find("themes/default/screenshot.png"))
