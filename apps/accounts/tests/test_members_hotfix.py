"""Regression tests of the accounts hot-fix: a member (role "user") has no panel, public sign-up starts closed, and the sign-up form cannot be used to find out who has an account."""
from importlib import import_module

from django.apps import apps as global_apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.permissions import CAPABILITIES, Role
from apps.audit.models import AuditLog
from apps.core.models import SiteSettings

from .test_auth import STRONG, make_user

User = get_user_model()


class MemberHasNoPanelTests(TestCase):
    def setUp(self):
        cache.clear()
        self.member = make_user("member", Role.USER)

    def test_a_member_holds_no_panel_capability(self):
        held = {cap for cap in CAPABILITIES if self.member.has_cap(cap)}
        self.assertEqual(held, {"profile.manage"})
        self.assertFalse(self.member.has_cap("dashboard.view"))

    def test_other_roles_are_untouched(self):
        for role in (Role.AUTHOR, Role.ADMIN, Role.SUPER_ADMIN):
            user = make_user(f"r-{role.value}", role)
            self.assertTrue(user.has_cap("dashboard.view"), role)
            self.assertTrue(user.has_cap("profile.manage"), role)
        self.assertTrue(make_user("auth2", Role.AUTHOR).has_cap("content.write"))
        self.assertTrue(make_user("adm2", Role.ADMIN).has_cap("settings.manage"))

    def test_the_panel_sends_a_member_to_their_own_account_page(self):
        self.client.force_login(self.member)
        r = self.client.get(reverse("dashboard:home"))
        self.assertRedirects(r, reverse("accounts:profile"))
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 200)

    def test_signing_in_as_a_member_ends_on_the_account_page(self):
        r = self.client.post(reverse("accounts:login"), {"username": "member", "password": STRONG}, follow=True)
        self.assertEqual(r.redirect_chain[-1][0], reverse("accounts:profile"))
        self.assertEqual(r.status_code, 200)

    def test_panel_pages_refuse_a_member(self):
        self.client.force_login(self.member)
        for name in ("core:site_settings", "accounts:user_list"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        for path in ("/panel/posts/", "/panel/pages/", "/panel/media/", "/panel/studio/", "/panel/plugins/", "/panel/themes/"):
            self.assertIn(self.client.get(path).status_code, (403, 404), path)

    def test_a_member_gets_no_admin_bar_on_the_public_site(self):
        from apps.public.rendering import _build_viewer

        self.client.force_login(self.member)
        request = self.client.get("/fa/").wsgi_request
        self.assertIsNone(_build_viewer(request))


class RegistrationStartsClosedTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_a_fresh_install_has_public_registration_closed(self):
        self.assertFalse(settings.REGISTRATION_DEFAULT_OPEN)
        SiteSettings.objects.all().delete()
        self.assertFalse(SiteSettings.load().registration_open)

    def test_closed_registration_creates_nobody(self):
        r = self.client.post(reverse("accounts:register"), {"first_name": "N", "last_name": "R", "email": "n@example.com", "password1": STRONG, "password2": STRONG})
        self.assertRedirects(r, reverse("accounts:login"))
        self.assertFalse(User.objects.filter(email="n@example.com").exists())


class SignUpDoesNotRevealAccountsTests(TestCase):
    def setUp(self):
        cache.clear()
        SiteSettings.objects.update_or_create(pk=1, defaults={"registration_open": True})
        self.url = reverse("accounts:register")
        self.existing = make_user("taken")

    def data(self, email, **extra):
        return {"first_name": "N", "last_name": "R", "email": email, "password1": STRONG, "password2": STRONG, **extra}

    def test_new_and_known_addresses_get_the_same_answer(self):
        new = self.client.post(self.url, self.data("fresh@example.com"))
        known = self.client.post(self.url, self.data("taken@example.com"))
        self.assertEqual((new.status_code, new.url), (known.status_code, known.url))
        page_new = self.client.get(new.url).content
        page_known = self.client.get(known.url).content
        self.assertEqual(page_new, page_known)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_a_known_address_is_matched_in_any_case_and_creates_no_second_account(self):
        before = User.objects.count()
        self.client.post(self.url, self.data("TAKEN@Example.com"))
        self.assertEqual(User.objects.count(), before)

    def test_the_owner_of_a_known_address_is_told_and_gets_no_verification_link(self):
        self.client.post(self.url, self.data("taken@example.com"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["taken@example.com"])
        self.assertNotIn("/verify-email/", mail.outbox[0].body)
        self.assertIn(reverse("accounts:password_reset"), mail.outbox[0].body)

    def test_a_new_address_gets_the_verification_email(self):
        self.client.post(self.url, self.data("fresh@example.com"))
        self.assertEqual([m.to for m in mail.outbox], [["fresh@example.com"]])
        self.assertIn("/verify-email/", mail.outbox[0].body)
        self.assertIsNone(User.objects.get(email="fresh@example.com").email_verified_at)

    def test_there_is_no_username_to_clash(self):
        form_fields = self.client.get(self.url).context["form"].fields
        self.assertNotIn("username", form_fields)
        r = self.client.post(self.url, self.data("fresh@example.com", username="taken"))  # a posted username is ignored
        self.assertEqual(r.status_code, 302)
        self.assertEqual(User.objects.get(email="fresh@example.com").username, "fresh")

    def test_equal_local_parts_still_register_with_the_same_answer(self):
        a = self.client.post(self.url, self.data("taken@other.org"))
        b = self.client.post(self.url, self.data("taken@third.org"))
        self.assertEqual((a.status_code, a.url), (b.status_code, b.url))
        names = set(User.objects.filter(email__in=["taken@other.org", "taken@third.org"]).values_list("username", flat=True))
        self.assertEqual(len(names), 2)
        self.assertTrue(all(n.startswith("taken") for n in names))

    def test_no_error_message_names_an_existing_address(self):
        r = self.client.post(self.url, self.data("taken@example.com"), follow=True)
        text = r.content.decode()
        self.assertNotIn("قبلاً", text.split("<form")[0] if "<form" in text else text)
        self.assertNotIn("already", text.lower())

    @override_settings(RAVIX_REGISTER_LIMIT_PER_IP=100)
    def test_the_known_address_path_is_rate_limited_silently(self):
        for _ in range(12):
            r = self.client.post(self.url, self.data("taken@example.com"))
            self.assertEqual(r.url, reverse("accounts:register_done"))
        self.assertLess(len(mail.outbox), 12)


class MembersMigrationTests(TestCase):
    def setUp(self):
        self.mod = import_module("apps.core.migrations.0016_members_no_panel_registration_closed")

    def test_existing_members_are_counted_listed_and_shown_to_the_admin(self):
        make_user("m1", Role.USER)
        make_user("m2", Role.USER)
        make_user("off", Role.USER, is_active=False)
        make_user("aut", Role.AUTHOR)
        self.mod.report_members(global_apps, None)
        self.assertEqual(SiteSettings.load().members_notice_count, 2)
        entry = AuditLog.objects.get(object_type="Role")
        self.assertIn("m1", entry.message)
        self.assertIn("m2", entry.message)
        self.assertNotIn("aut", entry.message)

        admin = make_user("boss", Role.ADMIN)
        self.client.force_login(admin)
        html = self.client.get(reverse("dashboard:home")).content.decode()
        self.assertIn(reverse("dashboard:members_notice_dismiss"), html)
        self.client.post(reverse("dashboard:members_notice_dismiss"))
        self.assertEqual(SiteSettings.load().members_notice_count, 0)
        self.assertNotIn(reverse("dashboard:members_notice_dismiss"), self.client.get(reverse("dashboard:home")).content.decode())

    def test_no_members_no_notice(self):
        make_user("aut", Role.AUTHOR)
        self.mod.report_members(global_apps, None)
        self.assertEqual(SiteSettings.load().members_notice_count, 0)
        self.assertFalse(AuditLog.objects.filter(object_type="Role").exists())

    def test_only_a_user_manager_sees_the_notice_or_can_dismiss_it(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={"members_notice_count": 3})
        self.client.force_login(make_user("aut", Role.AUTHOR))
        self.assertNotIn(reverse("dashboard:members_notice_dismiss"), self.client.get(reverse("dashboard:home")).content.decode())
        self.assertEqual(self.client.post(reverse("dashboard:members_notice_dismiss")).status_code, 403)
        self.assertEqual(SiteSettings.load().members_notice_count, 3)
