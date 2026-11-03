"""Argon2id with automatic upgrade, the password rules (length setting, local common-password list, site/personal words) and the forced password change."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, identify_hasher, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts import password_validators as pv
from apps.accounts.models import AccountPolicy
from apps.accounts.permissions import Role
from apps.audit.models import AuditLog
from apps.core.models import SiteSettings

from .test_auth import STRONG, make_user

User = get_user_model()

REAL_HASHERS = ["apps.accounts.hashers.RavixArgon2PasswordHasher", "django.contrib.auth.hashers.PBKDF2PasswordHasher"]
GOOD = "correct horse battery staple"


def rejected(password, user=None):
    try:
        validate_password(password, user)
    except ValidationError:
        return True
    return False


@override_settings(PASSWORD_HASHERS=REAL_HASHERS)
class Argon2Tests(TestCase):
    def setUp(self):
        cache.clear()

    def test_new_passwords_are_argon2id(self):
        u = make_user("neo")
        self.assertTrue(u.password.startswith("argon2$argon2id$"), u.password[:20])
        self.assertTrue(u.check_password(STRONG))
        self.assertFalse(u.check_password("nope"))

    def test_an_old_pbkdf2_hash_is_remade_as_argon2_at_sign_in(self):
        u = make_user("old")
        User.objects.filter(pk=u.pk).update(password=make_password(STRONG, hasher="pbkdf2_sha256"))
        self.assertEqual(identify_hasher(User.objects.get(pk=u.pk).password).algorithm, "pbkdf2_sha256")
        self.assertTrue(self.client.login(username="old", password=STRONG))
        stored = User.objects.get(pk=u.pk).password
        self.assertTrue(stored.startswith("argon2$"), stored[:12])
        self.assertTrue(check_password(STRONG, stored))

    def test_a_wrong_password_never_changes_the_stored_hash(self):
        u = make_user("old2")
        legacy = make_password(STRONG, hasher="pbkdf2_sha256")
        User.objects.filter(pk=u.pk).update(password=legacy)
        self.assertFalse(self.client.login(username="old2", password="wrong"))
        self.assertEqual(User.objects.get(pk=u.pk).password, legacy)

    def test_web_sign_in_upgrades_too(self):
        u = make_user("web")
        User.objects.filter(pk=u.pk).update(password=make_password(STRONG, hasher="pbkdf2_sha256"))
        r = self.client.post(reverse("accounts:login"), {"username": "web", "password": STRONG})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(User.objects.get(pk=u.pk).password.startswith("argon2$"))

    def test_weaker_argon2_parameters_are_remade_with_the_current_ones(self):
        from apps.accounts.hashers import RavixArgon2PasswordHasher as H

        weak = H().encode(STRONG, H().salt()).replace(f"m={H.memory_cost}", "m=8192")
        self.assertTrue(H().must_update(weak))
        self.assertFalse(H().must_update(H().encode(STRONG, H().salt())))

    def test_environment_values_are_clamped(self):
        from apps.accounts import hashers

        with mock.patch.dict("os.environ", {"RAVIX_ARGON2_MEMORY_KIB": "1", "RAVIX_ARGON2_TIME_COST": "abc"}):
            self.assertEqual(hashers._int("RAVIX_ARGON2_MEMORY_KIB", 65536, 8192, 1048576), 8192)
            self.assertEqual(hashers._int("RAVIX_ARGON2_TIME_COST", 3, 1, 10), 3)


class SettingsListTests(TestCase):
    def test_the_hasher_list_has_pbkdf2_as_a_fallback(self):
        from django.conf import settings as s
        import importlib.util

        text = open(s.BASE_DIR / "config" / "settings" / "base.py", encoding="utf-8").read()
        self.assertIn('find_spec("argon2")', text)
        self.assertIn("PBKDF2PasswordHasher", text)
        self.assertTrue(importlib.util.find_spec("argon2"))


class LengthTests(TestCase):
    def test_default_minimum_is_ten(self):
        self.assertTrue(rejected("abcdefg-9"))
        self.assertFalse(rejected(GOOD))

    def test_the_account_setting_is_used_and_never_goes_below_eight(self):
        AccountPolicy.load()
        AccountPolicy.objects.filter(pk=1).update(password_min_length=14)
        self.assertTrue(rejected("tangerine-lamp"[:13]))
        self.assertFalse(rejected("tangerine-lamp-x"))
        AccountPolicy.objects.filter(pk=1).update(password_min_length=3)  # hand-edited row
        self.assertTrue(rejected("xk2-9qz"))
        self.assertFalse(rejected("xk2-9qz-Wm"))

    def test_a_giant_password_is_refused(self):
        self.assertTrue(rejected("a1-" + "x" * 200))

    def test_a_broken_policy_table_falls_back_to_the_defaults(self):
        with mock.patch("apps.accounts.models.AccountPolicy.objects") as m:
            m.filter.side_effect = RuntimeError("no such table")
            self.assertEqual(pv.policy_values()["password_min_length"], 10)
            self.assertTrue(rejected("short-1"))


class CommonPasswordTests(TestCase):
    def test_django_list_still_applies(self):
        self.assertTrue(rejected("password123456"))

    def test_local_list_catches_persian_and_finglish_words(self):
        for pw in ("salam123456", "Salam-1234567", "khodahafez", "iran1234567", "mohammad1234", "tehran12345"):
            self.assertTrue(rejected(pw), pw)

    def test_persian_letters_and_digits_are_folded(self):
        digits = "".join(chr(0x06F0 + int(c)) for c in "1234567890")
        self.assertEqual(pv.fold(digits), "1234567890")
        self.assertEqual(pv.fold("كتاب"), pv.fold("کتاب"))  # Arabic kaf = Persian kaf
        salam = "سلام"
        self.assertTrue(rejected(salam + digits))  # salam + Persian digits
        self.assertTrue(rejected(salam + "‌" + "1234567"))  # a ZWNJ does not hide it

    def test_the_product_name_and_admin_words_are_refused(self):
        for pw in ("Ravix@2026!!", "ravixcms12345", "Admin-2026!", "Administrator1!"):
            self.assertTrue(rejected(pw), pw)

    def test_a_strong_passphrase_with_a_common_word_inside_is_fine(self):
        self.assertFalse(rejected("my-salam-is-louder-than-a-trumpet-7"))
        self.assertFalse(rejected(GOOD))

    def test_it_can_be_switched_off(self):
        AccountPolicy.load()
        AccountPolicy.objects.filter(pk=1).update(password_check_common=False)
        self.assertFalse(rejected("salam1234567"))
        AccountPolicy.objects.filter(pk=1).update(password_check_common=True)
        self.assertTrue(rejected("salam1234567"))


class SiteContextTests(TestCase):
    def test_site_name_and_own_details_cannot_be_the_core(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={"site_name": "Nilgoon Bakery"})
        self.assertTrue(rejected("Nilgoon-2026-xx"))
        self.assertTrue(rejected("bakery123456"))
        u = User(username="soheilkarimi", email="soheil.k@example.com", first_name="Soheil", last_name="Karimi")
        self.assertTrue(rejected("Soheilkarimi!2026", u))
        self.assertTrue(rejected("karimi-1234567", u))
        self.assertFalse(rejected(GOOD, u))

    def test_it_can_be_switched_off(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={"site_name": "Nilgoon Bakery"})
        AccountPolicy.load()
        AccountPolicy.objects.filter(pk=1).update(password_check_personal=False)
        self.assertFalse(rejected("Nilgoon-2026-xx"))


class FormsUseTheRulesTests(TestCase):
    def setUp(self):
        cache.clear()
        SiteSettings.objects.update_or_create(pk=1, defaults={"registration_open": True})

    def test_sign_up_refuses_a_common_password_with_a_clear_message(self):
        data = {"first_name": "A", "last_name": "B", "email": "a@example.com", "password1": "Salam-1234567", "password2": "Salam-1234567"}
        r = self.client.post(reverse("accounts:register"), data)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(email="a@example.com").exists())

    def test_the_policy_page_saves_the_password_settings(self):
        admin = make_user("boss", Role.ADMIN)
        self.client.force_login(admin)
        base = {"registration_mode": "closed", "default_role": "user", "remember_days": 30, "invitation_days": 7}
        r = self.client.post(reverse("accounts:policy"), {**base, "password_min_length": 12, "password_check_common": "on"})
        self.assertEqual(r.status_code, 302)
        row = AccountPolicy.load()
        self.assertEqual((row.password_min_length, row.password_check_common, row.password_check_personal), (12, True, False))
        r = self.client.post(reverse("accounts:policy"), {**base, "password_min_length": 7})
        self.assertEqual(r.status_code, 200)  # below 8 is not accepted


class ForcedChangeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("temp", Role.AUTHOR, must_change_password=True)
        self.client.force_login(self.user)
        self.change = reverse("accounts:password_change")

    def test_every_page_goes_to_the_password_page(self):
        for url in (reverse("dashboard:home"), reverse("accounts:profile"), "/", "/fa/"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertEqual(r["Location"].split("?")[0], self.change, url)

    def test_the_wanted_page_is_remembered(self):
        r = self.client.get(reverse("accounts:profile"))
        self.assertIn("next=", r["Location"])

    def test_posts_and_json_requests_are_refused(self):
        self.assertEqual(self.client.post(reverse("accounts:profile"), {}).status_code, 302)
        r = self.client.get("/api/anything/")
        self.assertEqual(r.status_code, 403)
        self.assertIn("detail", r.json())

    def test_password_page_logout_and_static_stay_open(self):
        self.assertEqual(self.client.get(self.change).status_code, 200)
        self.assertContains(self.client.get(self.change), "alert-warning")
        self.assertEqual(self.client.post(reverse("accounts:logout")).status_code, 302)

    def test_a_flagged_person_who_is_not_signed_in_is_untouched(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("accounts:login")).status_code, 200)

    def test_changing_the_password_clears_the_flag_and_continues(self):
        r = self.client.post(self.change, {"old_password": STRONG, "new_password1": GOOD, "new_password2": GOOD, "next": reverse("accounts:profile")})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], reverse("accounts:profile"))
        self.user.refresh_from_db()
        self.assertFalse(self.user.must_change_password)
        self.assertIsNotNone(self.user.password_changed_at)
        self.assertTrue(self.user.check_password(GOOD))
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 200)

    def test_an_outside_next_address_is_ignored(self):
        r = self.client.post(self.change, {"old_password": STRONG, "new_password1": GOOD, "new_password2": GOOD, "next": "https://evil.example/"})
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("evil.example", r["Location"])

    def test_the_same_password_is_refused_and_the_flag_stays(self):
        r = self.client.post(self.change, {"old_password": STRONG, "new_password1": STRONG, "new_password2": STRONG})
        self.assertEqual(r.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.must_change_password)

    def test_a_weak_new_password_keeps_the_flag(self):
        r = self.client.post(self.change, {"old_password": STRONG, "new_password1": "salam1234567", "new_password2": "salam1234567"})
        self.assertEqual(r.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.must_change_password)

    def test_resetting_the_password_by_email_clears_it_too(self):
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        self.client.logout()
        uid, token = urlsafe_base64_encode(force_bytes(self.user.pk)), default_token_generator.make_token(self.user)
        url = reverse("accounts:password_reset_confirm", args=[uid, token])
        r = self.client.get(url, follow=True)
        post = reverse("accounts:password_reset_confirm", args=[uid, "set-password"])
        r = self.client.post(post, {"new_password1": GOOD, "new_password2": GOOD})
        self.assertEqual(r.status_code, 302)
        self.user.refresh_from_db()
        self.assertFalse(self.user.must_change_password)


class AdminSetsTheFlagTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = make_user("boss", Role.ADMIN)
        self.target = make_user("worker", Role.AUTHOR)
        self.client.force_login(self.admin)
        self.url = reverse("accounts:user_manage", args=[self.target.pk])

    def test_the_manage_form_sets_and_clears_it_and_logs_it(self):
        r = self.client.post(self.url, {"role": "author", "is_active": "on", "must_change_password": "on"})
        self.assertEqual(r.status_code, 302)
        self.target.refresh_from_db()
        self.assertTrue(self.target.must_change_password)
        self.assertTrue(AuditLog.objects.filter(actor=self.admin, message__contains=self.target.username).exists())
        self.client.post(self.url, {"role": "author", "is_active": "on"})
        self.target.refresh_from_db()
        self.assertFalse(self.target.must_change_password)

    def test_the_flag_applies_at_the_next_request_of_a_signed_in_person(self):
        self.client.post(self.url, {"role": "author", "is_active": "on", "must_change_password": "on"})
        other = self.client_class()
        other.force_login(self.target)
        self.assertEqual(other.get(reverse("accounts:profile"))["Location"].split("?")[0], reverse("accounts:password_change"))
