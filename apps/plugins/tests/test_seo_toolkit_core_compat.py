"""سازگاری افزونهٔ seo_toolkit با سئوی داخلی هسته (Post.seo_title/seo_description/noindex، OG پیش‌فرض، ریدایرکت).

این آزمون‌ها رفتار واقعی را قفل می‌کنند: چه چیزی بر چه چیزی غالب است و کجا تداخل می‌ماند.
"""
import re
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.content.models import Post, PublishStatus, Redirect
from apps.core.models import SiteSettings
from apps.core.testing import png_bytes
from apps.plugins.models import PluginRecord

from .helpers import PluginTestCase


def meta(html, attr, name):
    m = re.search(rf'<meta {attr}="{re.escape(name)}" content="([^"]*)"', html)
    return m.group(1) if m else None


class SeoToolkitWithCoreSeoTests(PluginTestCase):
    copy_samples = True

    def setUp(self):
        super().setUp()
        self.install_activate("seo_toolkit")
        s = SiteSettings.load()
        s.site_name = "راویکس"
        s.save()
        self.post = Post.objects.create(title="عنوان", slug="p1", body="متن", excerpt="خلاصه", status=PublishStatus.PUBLISHED,
                                        publish_at=timezone.now() - timedelta(days=1))
        self.edit = reverse("plugins:page", args=["seo_toolkit", "edit"])

    def head(self):
        return Client().get("/fa/blog/p1/").content.decode()

    def plugin_meta(self, **data):
        self.login(self.admin)
        r = self.client.post(self.edit, {"kind": "post", "id": self.post.pk, **data})
        self.assertEqual(r.status_code, 302)
        self.invalidate()

    def test_core_fields_work_when_plugin_has_no_meta_for_the_post(self):
        Post.objects.filter(pk=self.post.pk).update(seo_title="هسته", seo_description="توضیح هسته", noindex=True)
        html = self.head()
        self.assertIn("<title>هسته | راویکس</title>", html)
        self.assertEqual(meta(html, "name", "description"), "توضیح هسته")
        self.assertEqual(meta(html, "name", "robots"), "noindex,follow")

    def test_plugin_meta_wins_over_core_title_and_description(self):
        Post.objects.filter(pk=self.post.pk).update(seo_title="هسته", seo_description="توضیح هسته")
        self.plugin_meta(title="افزونه", description="توضیح افزونه")
        html = self.head()
        self.assertIn("<title>افزونه | راویکس</title>", html)
        self.assertEqual(meta(html, "name", "description"), "توضیح افزونه")
        self.assertEqual(meta(html, "name", "twitter:title"), "افزونه | راویکس")  # twitter از خروجی نهایی ساخته می‌شود

    def test_core_noindex_not_undone_by_plugin(self):
        """noindex هسته هرگز با «index» افزونه (پیش‌فرض robots یا متای بدون noindex) باطل نمی‌شود."""
        Post.objects.filter(pk=self.post.pk).update(noindex=True)
        PluginRecord.objects.filter(slug="seo_toolkit").update(config={"default_robots": "index,follow"})
        self.invalidate()
        self.plugin_meta(title="افزونه")
        self.assertEqual(meta(self.head(), "name", "robots"), "noindex,follow")

    def test_plugin_noindex_applies_even_if_core_allows_index(self):
        self.plugin_meta(title="x", noindex="on")
        self.assertEqual(meta(self.head(), "name", "robots"), "noindex,follow")

    def test_plugin_relative_og_image_becomes_absolute(self):
        self.plugin_meta(title="x", og_image="/media/custom.jpg")
        html = self.head()
        self.assertEqual(meta(html, "property", "og:image"), "http://testserver/media/custom.jpg")
        self.assertEqual(meta(html, "name", "twitter:image"), "http://testserver/media/custom.jpg")
        self.assertEqual(meta(html, "name", "twitter:card"), "summary_large_image")

    def test_plugin_og_image_beats_featured_and_default_images(self):
        s = SiteSettings.load()
        s.default_share_image.save("d.png", SimpleUploadedFile("d.png", png_bytes(), "image/png"))
        self.post.featured_image.save("f.png", SimpleUploadedFile("f.png", png_bytes(), "image/png"))
        self.plugin_meta(title="x", og_image="https://cdn.example.com/o.jpg")
        self.assertEqual(meta(self.head(), "property", "og:image"), "https://cdn.example.com/o.jpg")

    def test_without_plugin_og_image_the_core_image_is_used(self):
        self.post.featured_image.save("f.png", SimpleUploadedFile("f.png", png_bytes(), "image/png"))
        self.assertIn("/media/posts/", meta(self.head(), "property", "og:image"))

    def test_slug_change_redirect_coexists_and_plugin_meta_follows_the_post(self):
        self.plugin_meta(title="افزونه")
        self.post.slug = "p2"
        self.post.save()
        self.assertTrue(Redirect.objects.filter(old_path="/blog/p1/", new_path="/blog/p2/").exists())
        html = Client().get("/fa/blog/p2/").content.decode()
        self.assertIn("<title>افزونه | راویکس</title>", html)  # SeoMeta به شناسه وابسته است نه slug
        self.assertEqual(Client().get("/fa/blog/p1/").status_code, 301)

    def test_deactivated_plugin_leaves_core_seo_intact(self):
        from apps.plugins import services

        Post.objects.filter(pk=self.post.pk).update(seo_title="هسته", noindex=True)
        self.plugin_meta(title="افزونه")
        services.deactivate("seo_toolkit", self.admin)
        html = self.head()
        self.assertIn("<title>هسته | راویکس</title>", html)
        self.assertEqual(meta(html, "name", "robots"), "noindex,follow")
