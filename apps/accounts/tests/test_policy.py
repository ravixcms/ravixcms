"""Account settings (sign-up mode, default role, verification, remember-me, terms) and invitations."""
import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts import policy
from apps.accounts.models import AccountPolicy, Invitation, SecurityProfile
from apps.accounts.permissions import Role
from apps.audit.models import AuditLog
from apps.content.models import Page, PublishStatus
from apps.core.models import SiteSettings

from .test_auth import STRONG, make_user

User = get_user_model()


def reg_data(email="new@example.com", **extra):
    return {"first_name": "N", "last_name": "R", "email": email, "password1": STRONG, "password2": STRONG, **extra}


class PolicyModelTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_defaults(self):
        p = AccountPolicy.load()
        self.assertEqual((p.default_role, p.remember_days, p.invitation_days, p.require_terms, p.invitations_enabled), ("user", 30, 7, False, True))
        self.assertFalse(p.verification_required)
        self.assertEqual(AccountPolicy.objects.count(), 1)

    @override_settings(RAVIX_REQUIRE_EMAIL_VERIFICATION=True)
    def test_the_environment_variable_forces_verification_on(self):
        AccountPolicy.objects.all().delete()
        self.assertTrue(AccountPolicy.load().verification_required)

    def test_a_role_outside_the_allowed_two_falls_back_to_member(self):
        p = AccountPolicy.load()
        p.default_role = "super_admin"
        self.assertEqual(p.new_user_role, "user")

    def test_the_three_modes_map_to_the_two_stored_flags(self):
        for mode, public, inv in (("open", True, True), ("invite", False, True), ("closed", False, False)):
            policy.set_registration_mode(mode)
            self.assertEqual((SiteSettings.load().registration_open, AccountPolicy.load().invitations_enabled), (public, inv))
            self.assertEqual(policy.registration_mode(), mode)


class PolicyPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = make_user("boss", Role.ADMIN)
        self.url = reverse("accounts:policy")
        self.base = {"registration_mode": "closed", "default_role": "user", "remember_days": 30, "invitation_days": 7,
                     "password_min_length": 10, "password_check_common": "on", "password_check_personal": "on"}

    def test_only_settings_managers_open_it(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        for role in (Role.USER, Role.AUTHOR):
            self.client.force_login(make_user(f"u-{role.value}", role))
            self.assertEqual(self.client.get(self.url).status_code, 403, role)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_saving_the_mode_changes_who_can_sign_up(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.url, {**self.base, "registration_mode": "open"}).status_code, 302)
        self.assertTrue(SiteSettings.load().registration_open)
        self.client.post(self.url, {**self.base, "registration_mode": "invite"})
        self.assertEqual(policy.registration_mode(), "invite")
        self.assertTrue(AuditLog.objects.filter(object_type="Account settings", actor=self.admin).exists() or AuditLog.objects.filter(actor=self.admin, action="update").exists())

    def test_the_form_starts_with_the_current_mode(self):
        policy.set_registration_mode("invite")
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url).context["form"].fields["registration_mode"].initial, "invite")

    def test_roles_above_author_cannot_be_the_sign_up_role(self):
        self.client.force_login(self.admin)
        r = self.client.post(self.url, {**self.base, "default_role": "admin"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(AccountPolicy.load().default_role, "user")

    def test_terms_need_a_published_page(self):
        self.client.force_login(self.admin)
        draft = Page.objects.create(title="T", slug="terms", body="x", status=PublishStatus.DRAFT, author=self.admin)
        r = self.client.post(self.url, {**self.base, "require_terms": "on"})
        self.assertEqual(r.status_code, 200)
        r = self.client.post(self.url, {**self.base, "require_terms": "on", "terms_page": draft.pk})
        self.assertEqual(r.status_code, 200)  # a draft is not offered
        live = Page.objects.create(title="T2", slug="terms2", body="x", status=PublishStatus.PUBLISHED, author=self.admin)
        r = self.client.post(self.url, {**self.base, "require_terms": "on", "terms_page": live.pk})
        self.assertEqual(r.status_code, 302)

    def test_site_settings_page_no_longer_holds_the_sign_up_switch_but_points_to_the_new_page(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("core:site_settings"))
        self.assertNotIn("registration_open", r.context["form"].fields)
        self.assertContains(r, self.url)

    def test_navigation_lists_both_pages_for_an_admin(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse("dashboard:home")).content.decode()
        self.assertIn(reverse("accounts:policy"), html)
        self.assertIn(reverse("accounts:invitations"), html)


class SignUpPolicyTests(TestCase):
    def setUp(self):
        cache.clear()
        policy.set_registration_mode("open")

    def test_the_chosen_role_is_given_to_new_sign_ups(self):
        AccountPolicy.load()
        AccountPolicy.objects.filter(pk=1).update(default_role="author")
        self.client.post(reverse("accounts:register"), reg_data())
        self.assertEqual(User.objects.get(email="new@example.com").role, "author")

    def test_terms_must_be_accepted_and_the_time_is_kept(self):
        live = Page.objects.create(title="Terms", slug="terms", body="x", status=PublishStatus.PUBLISHED, author=make_user("a1", Role.ADMIN))
        AccountPolicy.load()
        AccountPolicy.objects.filter(pk=1).update(require_terms=True, terms_page=live)
        page = self.client.get(reverse("accounts:register"))
        self.assertIn("accept_terms", page.context["form"].fields)
        self.assertContains(page, "/terms/")
        self.client.post(reverse("accounts:register"), reg_data())
        self.assertFalse(User.objects.filter(email="new@example.com").exists())
        self.client.post(reverse("accounts:register"), reg_data(accept_terms="on"))
        user = User.objects.get(email="new@example.com")
        self.assertIsNotNone(SecurityProfile.objects.get(user=user).terms_accepted_at)

    def test_no_terms_field_when_not_required(self):
        self.assertNotIn("accept_terms", self.client.get(reverse("accounts:register")).context["form"].fields)

    def test_a_draft_terms_page_is_not_linked(self):
        draft = Page.objects.create(title="Terms", slug="terms", body="x", status=PublishStatus.DRAFT, author=make_user("a1", Role.ADMIN))
        AccountPolicy.load()
        AccountPolicy.objects.filter(pk=1).update(require_terms=True, terms_page=draft)
        self.assertNotContains(self.client.get(reverse("accounts:register")), "/terms/")


class SignInPolicyTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("sara")
        self.url = reverse("accounts:login")

    def test_remember_me_lasts_the_configured_days(self):
        AccountPolicy.load()
        AccountPolicy.objects.filter(pk=1).update(remember_days=9)
        self.client.post(self.url, {"username": "sara", "password": STRONG, "remember_me": "on"})
        self.assertAlmostEqual(self.client.session.get_expiry_age(), 9 * 86400, delta=5)

    def test_without_remember_me_it_ends_with_the_browser(self):
        self.client.post(self.url, {"username": "sara", "password": STRONG})
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_the_panel_switch_requires_a_verified_email(self):
        AccountPolicy.load()
        AccountPolicy.objects.filter(pk=1).update(require_verified_email=True)
        r = self.client.post(self.url, {"username": "sara", "password": STRONG})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        User.objects.filter(pk=self.user.pk).update(email_verified_at=timezone.now())
        self.assertEqual(self.client.post(self.url, {"username": "sara", "password": STRONG}).status_code, 302)


class InvitationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = make_user("boss", Role.ADMIN)
        self.list_url = reverse("accounts:invitations")
        self.client.force_login(self.admin)

    def invite(self, email="guest@example.com", role="author"):
        mail.outbox.clear()
        r = self.client.post(self.list_url, {"email": email, "role": role})
        self.assertEqual(r.status_code, 302)
        body = mail.outbox[0].body
        token = re.search(r"/accounts/invite/([^/\s]+)/", body).group(1)
        return token, Invitation.objects.get(email=email, revoked_at__isnull=True)

    def accept_data(self, **extra):
        return {"first_name": "Gu", "last_name": "Est", "password1": STRONG, "password2": STRONG, **extra}

    def test_an_invitation_is_emailed_and_only_its_hash_is_stored(self):
        token, inv = self.invite()
        self.assertEqual(mail.outbox[0].to, ["guest@example.com"])
        self.assertEqual(inv.token_hash, Invitation.hash_token(token))
        self.assertNotIn(token, inv.token_hash)
        self.assertEqual(inv.state, "pending")
        self.assertAlmostEqual((inv.expires_at - timezone.now()).days, 6, delta=1)

    def test_accepting_creates_a_verified_account_with_the_invited_role_and_signs_nobody_in(self):
        token, inv = self.invite(role="author")
        url = reverse("accounts:invite_accept", args=[token])
        page = self.client.get(url)
        self.assertContains(page, "guest@example.com")
        self.client.logout()
        r = self.client.post(url, self.accept_data())
        self.assertRedirects(r, reverse("accounts:login"), fetch_redirect_response=False)
        user = User.objects.get(email="guest@example.com")
        self.assertEqual(user.role, "author")
        self.assertIsNotNone(user.email_verified_at)
        self.assertTrue(user.check_password(STRONG))
        self.assertNotIn("_auth_user_id", self.client.session)
        inv.refresh_from_db()
        self.assertEqual(inv.state, "used")

    def test_a_used_link_does_not_work_again(self):
        token, _ = self.invite()
        url = reverse("accounts:invite_accept", args=[token])
        self.client.logout()
        self.client.post(url, self.accept_data())
        before = User.objects.count()
        r = self.client.post(url, self.accept_data(first_name="Other"))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.context["usable"])
        self.assertEqual(User.objects.count(), before)

    def test_every_unusable_link_shows_the_same_page(self):
        token, inv = self.invite()
        self.client.logout()  # the success message of the invitation is not part of what a visitor sees
        pages = {"unknown": self.client.get(reverse("accounts:invite_accept", args=["x" * 40])).content}
        Invitation.objects.filter(pk=inv.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        pages["expired"] = self.client.get(reverse("accounts:invite_accept", args=[token])).content
        Invitation.objects.filter(pk=inv.pk).update(expires_at=timezone.now() + timedelta(days=1), revoked_at=timezone.now())
        pages["revoked"] = self.client.get(reverse("accounts:invite_accept", args=[token])).content
        Invitation.objects.filter(pk=inv.pk).update(revoked_at=None, used_at=timezone.now())
        pages["used"] = self.client.get(reverse("accounts:invite_accept", args=[token])).content
        normalize = lambda b: re.sub(rb"/accounts/invite/[^/\"]+/", b"/accounts/invite/X/", re.sub(rb'csrfmiddlewaretoken" value="[^"]+"', b"", b))  # noqa: E731
        import difflib
        base = normalize(pages["unknown"]).decode().splitlines()
        for name, content in pages.items():
            diff = [l for l in difflib.unified_diff(base, normalize(content).decode().splitlines(), lineterm="") if l[:1] in "+-" and l[:3] not in ("+++", "---")]
            self.assertEqual(diff, [], name)

    def test_a_new_invitation_replaces_the_old_one(self):
        old, _ = self.invite()
        new, _ = self.invite()
        self.client.logout()
        self.assertFalse(self.client.get(reverse("accounts:invite_accept", args=[old])).context["usable"])
        self.assertTrue(self.client.get(reverse("accounts:invite_accept", args=[new])).context["usable"])

    def test_withdrawing_stops_the_link(self):
        token, inv = self.invite()
        self.client.post(reverse("accounts:invitation_revoke", args=[inv.pk]))
        self.client.logout()
        self.assertFalse(self.client.get(reverse("accounts:invite_accept", args=[token])).context["usable"])

    def test_an_existing_address_cannot_be_invited(self):
        make_user("old", Role.USER)
        r = self.client.post(self.list_url, {"email": "old@example.com", "role": "user"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Invitation.objects.count(), 0)
        self.assertEqual(mail.outbox, [])

    def test_an_address_registered_after_the_invitation_makes_the_link_unusable(self):
        token, _ = self.invite()
        User.objects.create_user("g2", "guest@example.com", STRONG)
        self.client.logout()
        self.assertFalse(self.client.get(reverse("accounts:invite_accept", args=[token])).context["usable"])

    def test_an_admin_cannot_invite_to_a_role_above_their_own(self):
        r = self.client.post(self.list_url, {"email": "x@example.com", "role": "super_admin"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Invitation.objects.count(), 0)

    def test_closed_mode_refuses_new_and_old_invitations(self):
        token, _ = self.invite()
        policy.set_registration_mode("closed")
        r = self.client.post(self.list_url, {"email": "z@example.com", "role": "user"})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Invitation.objects.filter(email="z@example.com").exists())
        self.client.logout()
        self.assertFalse(self.client.get(reverse("accounts:invite_accept", args=[token])).context["usable"])

    def test_invitations_work_while_public_sign_up_is_closed(self):
        policy.set_registration_mode("invite")
        token, _ = self.invite()
        self.client.logout()
        self.assertTrue(self.client.get(reverse("accounts:invite_accept", args=[token])).context["usable"])

    def test_a_weak_password_is_refused_and_the_link_stays_valid(self):
        token, inv = self.invite()
        self.client.logout()
        url = reverse("accounts:invite_accept", args=[token])
        r = self.client.post(url, self.accept_data(password1="12345678", password2="12345678"))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(email="guest@example.com").exists())
        inv.refresh_from_db()
        self.assertEqual(inv.state, "pending")

    def test_terms_are_required_on_the_invitation_page_too(self):
        live = Page.objects.create(title="Terms", slug="terms", body="x", status=PublishStatus.PUBLISHED, author=self.admin)
        AccountPolicy.load()
        AccountPolicy.objects.filter(pk=1).update(require_terms=True, terms_page=live)
        token, _ = self.invite()
        self.client.logout()
        url = reverse("accounts:invite_accept", args=[token])
        self.client.post(url, self.accept_data())
        self.assertFalse(User.objects.filter(email="guest@example.com").exists())
        self.client.post(url, self.accept_data(accept_terms="on"))
        self.assertIsNotNone(SecurityProfile.objects.get(user__email="guest@example.com").terms_accepted_at)

    def test_only_user_managers_see_or_use_the_pages(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.list_url).status_code, 302)
        self.client.force_login(make_user("wr", Role.AUTHOR))
        self.assertEqual(self.client.get(self.list_url).status_code, 403)
        self.assertEqual(self.client.post(self.list_url, {"email": "q@example.com", "role": "user"}).status_code, 403)
