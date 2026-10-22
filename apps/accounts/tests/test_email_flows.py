"""بازیابی رمز با ایمیل، تأیید ایمیل، محدودیت نرخ و send_test_email."""
import re
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts import emailing
from apps.audit.models import AuditLog

from .test_auth import STRONG, make_user

NEW_PASS = "Brand-New-Pass-987!"


def link_in(message):
    m = re.search(r"https?://\S+", message.body)
    assert m, message.body
    return m.group(0)


class PasswordResetTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("ali")
        self.url = reverse("accounts:password_reset")

    def request_reset(self, email):
        return self.client.post(self.url, {"email": email}, follow=False)

    def test_pages_render_in_persian(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "بازیابی رمز عبور")
        self.assertContains(self.client.get(reverse("accounts:login")), "رمز عبور را فراموش کرده‌اید؟")

    def test_same_response_for_existing_and_unknown_email(self):
        a = self.request_reset("ali@example.com")
        b = self.request_reset("nobody@example.com")
        self.assertEqual((a.status_code, a.url), (b.status_code, b.url))
        self.assertEqual(self.client.get(a.url).content, self.client.get(b.url).content)
        self.assertEqual(len(mail.outbox), 1)  # فقط برای ایمیل موجود
        self.assertEqual(mail.outbox[0].to, ["ali@example.com"])

    def test_email_is_persian_and_has_one_hour_note(self):
        self.request_reset("ali@example.com")
        msg = mail.outbox[0]
        self.assertIn("بازیابی رمز", msg.subject)
        self.assertIn("یک ساعت", msg.body)
        self.assertIn("/accounts/password-reset/", link_in(msg))

    def test_full_reset_flow_and_link_is_single_use(self):
        self.request_reset("ali@example.com")
        link = link_in(mail.outbox[0])
        r = self.client.get(link, follow=True)  # جنگو توکن را به نشست می‌برد و به آدرس set-password می‌رود
        self.assertContains(r, "تعیین رمز عبور جدید")
        form_url = r.redirect_chain[-1][0]
        done = self.client.post(form_url, {"new_password1": NEW_PASS, "new_password2": NEW_PASS})
        self.assertRedirects(done, reverse("accounts:password_reset_complete"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASS))
        self.assertIsNotNone(self.user.email_verified_at)  # رسیدن به صندوق = تأیید مالکیت ایمیل
        self.assertTrue(AuditLog.objects.filter(message__contains="بازیابی شد").exists())
        # استفادهٔ دوباره از همان پیوند
        again = self.client.get(link, follow=True)
        self.assertContains(again, "پیوند معتبر نیست")

    def test_expired_link_rejected(self):
        self.request_reset("ali@example.com")
        link = link_in(mail.outbox[0])
        with override_settings(PASSWORD_RESET_TIMEOUT=-1):
            r = self.client.get(link, follow=True)
        self.assertContains(r, "پیوند معتبر نیست")

    def test_weak_password_rejected_and_not_changed(self):
        self.request_reset("ali@example.com")
        r = self.client.get(link_in(mail.outbox[0]), follow=True)
        r = self.client.post(r.redirect_chain[-1][0], {"new_password1": "12345678", "new_password2": "12345678"})
        self.assertEqual(r.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(STRONG))

    def test_inactive_user_gets_no_email_but_same_response(self):
        self.user.is_active = False
        self.user.save()
        r = self.request_reset("ali@example.com")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(RAVIX_MAIL_LIMIT_PER_EMAIL=2, RAVIX_MAIL_LIMIT_PER_IP=100)
    def test_per_email_rate_limit_is_silent_and_uniform(self):
        responses = [self.request_reset("ali@example.com") for _ in range(4)]
        self.assertEqual(len({(r.status_code, r.url) for r in responses}), 1)  # کاربر محدودیت را نمی‌بیند
        self.assertEqual(len(mail.outbox), 2)

    @override_settings(RAVIX_MAIL_LIMIT_PER_EMAIL=100, RAVIX_MAIL_LIMIT_PER_IP=3)
    def test_per_ip_rate_limit_applies_across_different_emails(self):
        for i in range(6):
            make_user(f"u{i}")
            self.request_reset(f"u{i}@example.com")
        self.assertEqual(len(mail.outbox), 3)

    def test_smtp_failure_does_not_leak_or_500(self):
        with mock.patch("django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("smtp down")):
            with self.assertLogs("django.contrib.auth", "ERROR"):
                known = self.request_reset("ali@example.com")
            unknown = self.request_reset("nobody@example.com")
        self.assertEqual((known.status_code, known.url), (unknown.status_code, unknown.url))

    @override_settings(SITE_URL="https://cms.example.org")
    def test_link_uses_site_url_not_host_header(self):
        self.client.post(self.url, {"email": "ali@example.com"}, HTTP_HOST="localhost")
        self.assertTrue(link_in(mail.outbox[0]).startswith("https://cms.example.org/accounts/password-reset/"))


class EmailVerificationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("sara")

    def verify_url(self, user=None):
        return reverse("accounts:verify_email", args=[emailing.make_verify_token(user or self.user)])

    def test_registration_sends_verification_email_and_user_is_unverified(self):
        data = {"username": "newbie", "first_name": "نوید", "last_name": "رضایی", "email": "newbie@example.com",
                "password1": STRONG, "password2": STRONG}
        r = self.client.post(reverse("accounts:register"), data)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["newbie@example.com"])
        self.assertIn("تأیید ایمیل", mail.outbox[0].subject)
        from django.contrib.auth import get_user_model

        self.assertIsNone(get_user_model().objects.get(username="newbie").email_verified_at)

    def test_get_does_not_verify_post_does(self):
        url = self.verify_url()
        r = self.client.get(url)
        self.assertContains(r, "تأیید ایمیل")
        self.user.refresh_from_db()
        self.assertIsNone(self.user.email_verified_at)  # اسکنر ایمیل پیوند را مصرف نمی‌کند
        r = self.client.post(url)
        self.assertContains(r, "ایمیل تأیید شد")
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.email_verified_at)
        self.assertTrue(AuditLog.objects.filter(message__contains="ایمیل حساب تأیید شد").exists())

    def test_link_is_single_use(self):
        url = self.verify_url()
        self.client.post(url)
        stamp = self.user.__class__.objects.get(pk=self.user.pk).email_verified_at
        r = self.client.post(url)
        self.assertContains(r, "قبلاً تأیید شده")
        self.assertEqual(self.user.__class__.objects.get(pk=self.user.pk).email_verified_at, stamp)

    def test_expired_link_rejected(self):
        url = self.verify_url()
        with override_settings(RAVIX_EMAIL_VERIFY_MAX_AGE=-1):
            r = self.client.post(url)
        self.assertContains(r, "پیوند معتبر نیست")
        self.user.refresh_from_db()
        self.assertIsNone(self.user.email_verified_at)

    def test_tampered_or_garbage_token_rejected(self):
        for tok in ("abc", emailing.make_verify_token(self.user)[:-3] + "xyz"):
            r = self.client.post(reverse("accounts:verify_email", args=[tok]))
            self.assertContains(r, "پیوند معتبر نیست")

    def test_link_dies_after_email_change(self):
        url = self.verify_url()
        self.user.email = "other@example.com"
        self.user.save()
        self.assertContains(self.client.post(url), "پیوند معتبر نیست")
        self.user.refresh_from_db()
        self.assertIsNone(self.user.email_verified_at)

    def test_inactive_user_cannot_verify(self):
        url = self.verify_url()
        self.user.is_active = False
        self.user.save()
        self.assertContains(self.client.post(url), "پیوند معتبر نیست")

    def test_profile_email_change_resets_verification_and_sends_mail(self):
        self.user.email_verified_at = timezone.now()
        self.user.save()
        self.client.force_login(self.user)
        r = self.client.post(reverse("accounts:profile"), {"first_name": "سارا", "last_name": "الف", "email": "sara2@example.com", "phone": "", "bio": ""})
        self.assertEqual(r.status_code, 302)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.email_verified_at)
        self.assertEqual(mail.outbox[-1].to, ["sara2@example.com"])

    def test_profile_without_email_change_keeps_verification(self):
        self.user.email_verified_at = timezone.now()
        self.user.save()
        self.client.force_login(self.user)
        self.client.post(reverse("accounts:profile"), {"first_name": "سارا", "last_name": "الف", "email": self.user.email, "phone": "", "bio": ""})
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.email_verified_at)
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_is_uniform_and_only_sends_to_unverified(self):
        url = reverse("accounts:verify_resend")
        a = self.client.post(url, {"email": "sara@example.com"})
        b = self.client.post(url, {"email": "ghost@example.com"})
        self.assertEqual((a.status_code, a.url), (b.status_code, b.url))
        self.assertEqual(len(mail.outbox), 1)
        self.user.email_verified_at = timezone.now()
        self.user.save()
        self.client.post(url, {"email": "sara@example.com"})
        self.assertEqual(len(mail.outbox), 1)  # تأییدشده: ایمیل نمی‌رود، پاسخ همان است

    @override_settings(RAVIX_MAIL_LIMIT_PER_EMAIL=2, RAVIX_MAIL_LIMIT_PER_IP=100)
    def test_resend_rate_limited(self):
        for _ in range(5):
            self.client.post(reverse("accounts:verify_resend"), {"email": "sara@example.com"})
        self.assertEqual(len(mail.outbox), 2)

    @override_settings(RAVIX_REQUIRE_EMAIL_VERIFICATION=True)
    def test_login_blocked_until_verified_when_required(self):
        r = self.client.post(reverse("accounts:login"), {"username": "sara", "password": STRONG})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "ایمیل این حساب هنوز تأیید نشده است")
        self.client.post(self.verify_url())
        r = self.client.post(reverse("accounts:login"), {"username": "sara", "password": STRONG})
        self.assertEqual(r.status_code, 302)

    @override_settings(RAVIX_REQUIRE_EMAIL_VERIFICATION=True)
    def test_registration_does_not_auto_login_when_required(self):
        data = {"username": "newbie", "first_name": "ن", "last_name": "ر", "email": "newbie@example.com", "password1": STRONG, "password2": STRONG}
        r = self.client.post(reverse("accounts:register"), data)
        self.assertRedirects(r, reverse("accounts:login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_default_does_not_require_verification(self):
        r = self.client.post(reverse("accounts:login"), {"username": "sara", "password": STRONG})
        self.assertEqual(r.status_code, 302)

    def test_createsuperuser_style_admin_is_verified(self):
        from django.contrib.auth import get_user_model

        admin = get_user_model().objects.create_superuser("root", "root@example.com", STRONG)
        self.assertIsNotNone(admin.email_verified_at)

    def test_smtp_failure_on_registration_does_not_500(self):
        data = {"username": "newbie", "first_name": "ن", "last_name": "ر", "email": "newbie@example.com", "password1": STRONG, "password2": STRONG}
        with mock.patch("django.core.mail.EmailMessage.send", side_effect=OSError("smtp down")):
            with self.assertLogs("apps.accounts.emailing", "ERROR"):
                r = self.client.post(reverse("accounts:register"), data)
        self.assertEqual(r.status_code, 302)


class ExistingUsersMigrationTests(TestCase):
    def test_data_migration_marks_only_unverified_rows(self):
        from importlib import import_module

        from django.apps import apps as global_apps

        mod = import_module("apps.accounts.migrations.0004_mark_existing_emails_verified")
        old = make_user("old")
        stamp = timezone.now() - timedelta(days=30)
        keep = make_user("kept")
        keep.email_verified_at = stamp
        keep.save()
        mod.mark_existing(global_apps, None)
        old.refresh_from_db()
        keep.refresh_from_db()
        self.assertIsNotNone(old.email_verified_at)
        self.assertEqual(keep.email_verified_at, stamp)  # مقدار موجود دست نمی‌خورد


class SendTestEmailCommandTests(TestCase):
    def run_cmd(self, *args):
        out = StringIO()
        call_command("send_test_email", *args, stdout=out)
        return out.getvalue()

    def test_sends_message(self):
        out = self.run_cmd("a@example.com", "--subject", "سلام")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual((mail.outbox[0].to, mail.outbox[0].subject), (["a@example.com"], "سلام"))
        self.assertIn("backend:", out)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="smtp.example.invalid", EMAIL_HOST_PASSWORD="TOP-SECRET-PW")
    def test_smtp_error_is_reported_and_password_never_printed(self):
        out = StringIO()
        with mock.patch("django.core.mail.backends.smtp.EmailBackend.open", side_effect=OSError("connection refused")):
            with self.assertRaises(CommandError) as ctx:
                call_command("send_test_email", "a@example.com", stdout=out)
        self.assertIn("OSError", str(ctx.exception))
        self.assertNotIn("TOP-SECRET-PW", out.getvalue() + str(ctx.exception))
        self.assertIn("smtp.example.invalid", out.getvalue())

    def test_invalid_address_rejected(self):
        with self.assertRaises(CommandError):
            call_command("send_test_email", "not-an-email", stdout=StringIO())
