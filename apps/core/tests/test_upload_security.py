import os
import re

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.urls import reverse

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.content.models import Post
from apps.core.models import SiteSettings
from apps.core.testing import MediaTestCase, jpeg_bytes, png_bytes
from apps.core.validators import validate_image_file


class ExtensionMatchTests(SimpleTestCase):
    def check(self, name, content):
        validate_image_file(SimpleUploadedFile(name, content))

    def test_matching_extension_ok(self):
        self.check("a.png", png_bytes())
        self.check("a.JPG", jpeg_bytes())
        self.check("a.jpeg", jpeg_bytes())

    def test_dangerous_or_mismatched_extension_rejected_even_for_real_images(self):
        for name, content in (("x.html", png_bytes()), ("x.php", png_bytes()), ("x.svg", png_bytes()),
                              ("x.js", jpeg_bytes()), ("noext", png_bytes()), ("x.png", jpeg_bytes()), ("x.jpg", png_bytes())):
            with self.assertRaises(ValidationError, msg=name):
                self.check(name, content)

    def test_polyglot_image_with_html_extension_rejected(self):
        polyglot = png_bytes() + b"<script>alert(document.domain)</script>"
        with self.assertRaises(ValidationError):
            self.check("evil.html", polyglot)


class RandomNameTests(MediaTestCase):
    def setUp(self):
        self.admin = make_user("adm", Role.ADMIN)
        self.author = make_user("aut", Role.AUTHOR)

    def test_featured_image_stored_under_random_name(self):
        self.client.force_login(self.author)
        self.client.post(reverse("content:post_create"), {
            "title": "ت", "body": "ب", "status": "draft",
            "featured_image": SimpleUploadedFile("../../My Cover.PNG", png_bytes(), content_type="image/png")})
        name = Post.objects.get().featured_image.name
        self.assertRegex(name, r"^posts/[0-9a-f]{32}\.png$")

    def test_polyglot_featured_image_never_reaches_disk(self):
        self.client.force_login(self.author)
        before = sorted(f for _, _, fs in os.walk(self._media_dir) for f in fs)
        r = self.client.post(reverse("content:post_create"), {
            "title": "ت", "body": "ب", "status": "draft",
            "featured_image": SimpleUploadedFile("evil.html", png_bytes() + b"<script>1</script>", content_type="text/html")})
        self.assertTrue(r.context["form"].errors.get("featured_image"))
        self.assertEqual(Post.objects.count(), 0)
        self.assertEqual(sorted(f for _, _, fs in os.walk(self._media_dir) for f in fs), before)

    def test_avatar_and_logo_use_random_names(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("accounts:profile"), {
            "first_name": "ا", "last_name": "ب", "email": "adm@example.com",
            "avatar": SimpleUploadedFile("me.jpg", jpeg_bytes(), content_type="image/jpeg")})
        self.admin.refresh_from_db()
        self.assertRegex(self.admin.avatar.name, r"^avatars/[0-9a-f]{32}\.jpg$")
        self.client.post(reverse("core:site_settings"), {
            "site_name": "س", "logo": SimpleUploadedFile("Logo Final.png", png_bytes(), content_type="image/png")})
        self.assertRegex(SiteSettings.load().logo.name, r"^site/[0-9a-f]{32}\.png$")
