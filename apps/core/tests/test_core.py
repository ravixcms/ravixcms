import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.core.models import SiteSettings
from apps.core.templatetags.ravix_ui import fa_digits, icon, jalali


def png_bytes(size=(20, 20)):
    buf = io.BytesIO()
    Image.new("RGB", size, "red").save(buf, "PNG")
    return buf.getvalue()


class SiteSettingsModelTests(TestCase):
    def test_singleton(self):
        a = SiteSettings.load()
        SiteSettings.objects.create(site_name="x")  # pk اجباری ۱ می‌شود
        self.assertEqual(SiteSettings.objects.count(), 1)
        a.delete()
        self.assertEqual(SiteSettings.objects.count(), 1)


class SiteSettingsViewTests(TestCase):
    def setUp(self):
        self.admin = make_user("adm", Role.ADMIN)
        self.client.force_login(self.admin)
        self.url = reverse("core:site_settings")
        self.base = {"site_name": "سایت من", "tagline": "شعار", "description": "توضیح", "admin_email": "a@b.com", "registration_open": "on"}

    def test_save_settings(self):
        response = self.client.post(self.url, self.base, follow=True)
        self.assertContains(response, "با موفقیت ذخیره شد")
        site = SiteSettings.load()
        self.assertEqual(site.site_name, "سایت من")
        self.assertFalse(site.registration_open)  # the sign-up switch lives in the account settings now; this page leaves it alone

    def test_blank_name_rejected(self):
        response = self.client.post(self.url, {**self.base, "site_name": "   "})
        self.assertTrue(response.context["form"].errors.get("site_name"))

    def test_invalid_email_rejected(self):
        response = self.client.post(self.url, {**self.base, "admin_email": "not-an-email"})
        self.assertTrue(response.context["form"].errors.get("admin_email"))

    def test_site_name_shown_in_layout(self):
        self.client.post(self.url, {**self.base, "site_name": "نام‌آزمایشی"})
        self.assertContains(self.client.get(reverse("dashboard:home")), "نام‌آزمایشی")

    def test_logo_upload_and_validation(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            good = SimpleUploadedFile("logo.png", png_bytes(), content_type="image/png")
            response = self.client.post(self.url, {**self.base, "logo": good})
            self.assertEqual(response.status_code, 302)
            self.assertTrue(SiteSettings.load().logo.name.startswith("site/"))

            fake = SimpleUploadedFile("evil.png", b"<?php echo 1; ?>", content_type="image/png")
            response = self.client.post(self.url, {**self.base, "logo": fake})
            self.assertTrue(response.context["form"].errors.get("logo"))

            big = SimpleUploadedFile("big.png", png_bytes() + b"0" * (2 * 1024 * 1024), content_type="image/png")
            response = self.client.post(self.url, {**self.base, "logo": big})
            self.assertTrue(response.context["form"].errors.get("logo"))

    def test_remove_logo(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            self.client.post(self.url, {**self.base, "logo": SimpleUploadedFile("l.png", png_bytes(), content_type="image/png")})
            self.client.post(self.url, {**self.base, "logo-clear": "on"})
            self.assertFalse(SiteSettings.load().logo)


class ErrorPageTests(TestCase):
    def test_404_is_persian(self):
        with self.settings(DEBUG=False):
            response = self.client.get("/fa/no-such-page/")
        self.assertContains(response, "صفحه پیدا نشد", status_code=404)

    def test_root_is_public_site(self):
        # از مرحله ۳، آدرس «/» صفحه اصلی سایت عمومی است (نه تغییر مسیر به ورود/داشبورد)
        self.assertEqual(self.client.get("/fa/").status_code, 200)
        self.client.force_login(make_user("u"))
        self.assertEqual(self.client.get("/fa/").status_code, 200)


class TemplateTagTests(TestCase):
    def test_fa_digits(self):
        self.assertEqual(fa_digits(1405), "۱۴۰۵")

    def test_jalali_conversion(self):
        import datetime

        self.assertEqual(jalali(datetime.date(2026, 3, 21)), "۱۴۰۵/۰۱/۰۱")
        self.assertEqual(jalali(None), "—")

    def test_icon_renders_svg_and_falls_back(self):
        self.assertIn("<svg", str(icon("home")))
        self.assertIn("<circle", str(icon("does-not-exist")))
