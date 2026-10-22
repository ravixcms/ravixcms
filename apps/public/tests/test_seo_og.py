"""سئوی نوشته (seo_title/seo_description/noindex)، Open Graph، Twitter Card و تصویر پیش‌فرض اشتراک‌گذاری."""
import re

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.content.models import Post, PublishStatus
from apps.core.models import SiteSettings
from apps.core.testing import MediaTestCase, png_bytes

from .test_public import BODY


def meta(html, attr, name):
    m = re.search(rf'<meta {attr}="{re.escape(name)}" content="([^"]*)"', html)
    return m.group(1) if m else None


class PostSeoTests(MediaTestCase):
    def setUp(self):
        s = SiteSettings.load()
        s.site_name = "راویکس"
        s.save()
        self.post = Post.objects.create(title="عنوان اصلی", slug="p1", body=BODY, excerpt="خلاصهٔ نوشته", status=PublishStatus.PUBLISHED,
                                        publish_at="2020-01-01T00:00:00Z")

    def head(self, path="/blog/p1/"):
        return self.client.get(path).content.decode()

    def test_defaults_without_seo_fields(self):
        html = self.head()
        self.assertIn("<title>عنوان اصلی | راویکس</title>", html)
        self.assertEqual(meta(html, "name", "description"), "خلاصهٔ نوشته")
        self.assertNotIn('name="robots"', html)

    def test_seo_title_and_description_override(self):
        Post.objects.filter(pk=self.post.pk).update(seo_title="عنوان سئو", seo_description="توضیح سئوی اختصاصی")
        html = self.head()
        self.assertIn("<title>عنوان سئو | راویکس</title>", html)
        self.assertEqual(meta(html, "name", "description"), "توضیح سئوی اختصاصی")
        self.assertEqual(meta(html, "property", "og:title"), "عنوان سئو | راویکس")
        self.assertEqual(meta(html, "name", "twitter:description"), "توضیح سئوی اختصاصی")

    def test_noindex_meta_and_sitemap(self):
        self.assertIn("/blog/p1/", self.client.get("/sitemap.xml").content.decode().replace("%", "%"))
        Post.objects.filter(pk=self.post.pk).update(noindex=True)
        self.assertEqual(meta(self.head(), "name", "robots"), "noindex,follow")
        self.assertNotIn("p1", self.client.get("/sitemap.xml").content.decode())
        # خود نوشته همچنان در دسترس است (noindex ≠ پنهان)
        self.assertEqual(self.client.get("/blog/p1/").status_code, 200)

    def test_open_graph_article_and_twitter_tags(self):
        html = self.head()
        self.assertEqual(meta(html, "property", "og:type"), "article")
        self.assertEqual(meta(html, "property", "og:site_name"), "راویکس")
        self.assertEqual(meta(html, "property", "og:url"), "http://testserver/blog/p1/")
        self.assertRegex(meta(html, "property", "article:published_time"), r"^2020-01-01T")
        self.assertTrue(meta(html, "property", "article:modified_time"))
        self.assertEqual(meta(html, "name", "twitter:title"), "عنوان اصلی | راویکس")
        self.assertEqual(meta(html, "name", "twitter:card"), "summary")  # بدون تصویر
        self.assertIsNone(meta(html, "property", "og:image"))
        self.assertIsNone(meta(html, "name", "twitter:image"))

    def test_featured_image_is_og_image(self):
        self.post.featured_image.save("a.png", SimpleUploadedFile("a.png", png_bytes(), "image/png"))
        html = self.head()
        og = meta(html, "property", "og:image")
        self.assertTrue(og.startswith("http://testserver/media/posts/"), og)
        self.assertEqual(meta(html, "name", "twitter:image"), og)
        self.assertEqual(meta(html, "name", "twitter:card"), "summary_large_image")

    def test_default_share_image_fallback(self):
        s = SiteSettings.load()
        s.default_share_image.save("share.png", SimpleUploadedFile("share.png", png_bytes(), "image/png"))
        default = SiteSettings.load().default_share_image.url
        # نوشته بدون تصویر شاخص، صفحه، فهرست و صفحهٔ اصلی همه تصویر پیش‌فرض را می‌گیرند
        for path in ("/blog/p1/", "/blog/", "/"):
            self.assertEqual(meta(self.head(path), "property", "og:image"), "http://testserver" + default, path)
        # تصویر شاخص بر پیش‌فرض اولویت دارد
        self.post.featured_image.save("b.png", SimpleUploadedFile("b.png", png_bytes(), "image/png"))
        self.assertIn("/media/posts/", meta(self.head(), "property", "og:image"))

    def test_html_in_seo_fields_is_escaped(self):
        Post.objects.filter(pk=self.post.pk).update(seo_title='x"><script>alert(1)</script>', seo_description='"><img src=x onerror=1>')
        html = self.head()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<img src=x", html)


class PostSeoPanelTests(MediaTestCase):
    def setUp(self):
        self.admin = make_user("adm", Role.ADMIN)
        self.client.force_login(self.admin)

    def data(self, **kw):
        d = {"title": "نوشته", "slug": "", "excerpt": "", "body": "متن", "status": "published", "author": self.admin.pk}
        d.update(kw)
        return d

    def test_form_saves_seo_fields(self):
        r = self.client.post(reverse("content:post_create"), self.data(seo_title="سئو", seo_description="توضیح", noindex="on"))
        self.assertEqual(r.status_code, 302)
        post = Post.objects.get()
        self.assertEqual((post.seo_title, post.seo_description, post.noindex), ("سئو", "توضیح", True))

    def test_length_limits_validated(self):
        r = self.client.post(reverse("content:post_create"), self.data(seo_title="ا" * 71, seo_description="ب" * 161))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Post.objects.exists())
        self.assertTrue(r.context["form"].errors["seo_title"] and r.context["form"].errors["seo_description"])

    def test_form_page_shows_seo_section(self):
        self.assertContains(self.client.get(reverse("content:post_create")), "سئو و اشتراک‌گذاری")

    def test_site_settings_accepts_default_share_image(self):
        sa = make_user("sa", Role.SUPER_ADMIN)
        self.client.force_login(sa)
        url = reverse("core:site_settings")
        page = self.client.get(url)
        self.assertContains(page, "تصویر پیش‌فرض اشتراک‌گذاری")
        data = {"site_name": "سایت", "tagline": "", "description": "", "admin_email": "", "contact_phone": "", "address": "",
                "footer_text": "", "registration_open": "on",
                "default_share_image": SimpleUploadedFile("s.png", png_bytes(), "image/png")}
        r = self.client.post(url, data)
        self.assertEqual(r.status_code, 302)
        self.assertTrue(SiteSettings.load().default_share_image.name.startswith("site/"))

    def test_non_image_share_image_rejected(self):
        sa = make_user("sa", Role.SUPER_ADMIN)
        self.client.force_login(sa)
        data = {"site_name": "سایت", "default_share_image": SimpleUploadedFile("s.png", b"not-an-image", "image/png")}
        r = self.client.post(reverse("core:site_settings"), data)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(SiteSettings.load().default_share_image)
