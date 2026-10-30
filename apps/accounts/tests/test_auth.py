from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.permissions import Role
from apps.core.models import SiteSettings

User = get_user_model()

STRONG = "Sup3r-Secret-Pass!"


def make_user(username="ali", role=Role.USER, password=STRONG, **extra):
    return User.objects.create_user(username=username, email=f"{username}@example.com", password=password, role=role, **extra)


class RegistrationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.url = reverse("accounts:register")
        SiteSettings.objects.update_or_create(pk=1, defaults={"registration_open": True})  # a fresh install is closed; these tests open it
        self.data = {
            "first_name": "نوید",
            "last_name": "رضایی",
            "email": "New@Example.com",
            "password1": STRONG,
            "password2": STRONG,
        }

    def test_page_renders_with_csrf_and_rtl(self):
        response = self.client.get(self.url)
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(response, 'dir="rtl"')

    def test_register_creates_a_member_and_does_not_sign_in(self):
        response = self.client.post(self.url, self.data)
        self.assertRedirects(response, reverse("accounts:register_done"))
        user = User.objects.get(email="new@example.com")  # ایمیل نرمال می‌شود
        self.assertEqual(user.role, Role.USER)
        self.assertFalse(user.is_staff)
        self.assertEqual(user.username, "new")  # generated from the address, never asked
        self.assertNotIn("_auth_user_id", self.client.session)  # nobody is signed in by registering (same answer for known and new addresses)

    def test_cannot_escalate_role_via_post(self):
        self.client.post(self.url, {**self.data, "role": "super_admin", "is_superuser": "on"})
        self.assertEqual(User.objects.get(email="new@example.com").role, Role.USER)

    def test_password_mismatch_and_weak_password(self):
        response = self.client.post(self.url, {**self.data, "password2": "different"})
        self.assertTrue(response.context["form"].errors.get("password2"))
        response = self.client.post(self.url, {**self.data, "password1": "12345678", "password2": "12345678"})
        self.assertTrue(response.context["form"].errors.get("password2"))
        self.assertFalse(User.objects.filter(email="new@example.com").exists())

    def test_registration_can_be_closed(self):
        settings_obj = SiteSettings.load()
        settings_obj.registration_open = False
        settings_obj.save()
        response = self.client.post(self.url, self.data)
        self.assertRedirects(response, reverse("accounts:login"))
        self.assertFalse(User.objects.filter(email="new@example.com").exists())


class LoginLogoutTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("sara")
        self.url = reverse("accounts:login")

    def test_login_with_username_and_email(self):
        for ident in ("sara", "SARA", "sara@example.com"):
            self.client.logout()
            response = self.client.post(self.url, {"username": ident, "password": STRONG})
            self.assertRedirects(response, reverse("dashboard:home"), msg_prefix=ident, fetch_redirect_response=False)

    def test_wrong_password_shows_persian_error(self):
        response = self.client.post(self.url, {"username": "sara", "password": "bad"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "نادرست است")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_inactive_user_cannot_login(self):
        self.user.is_active = False
        self.user.save()
        response = self.client.post(self.url, {"username": "sara", "password": STRONG})
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(response.status_code, 200)

    def test_brute_force_lockout(self):
        for _ in range(5):
            self.client.post(self.url, {"username": "sara", "password": "bad"})
        response = self.client.post(self.url, {"username": "sara", "password": STRONG})
        self.assertContains(response, "تلاش‌های ناموفق")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_authenticated_user_redirected_from_login(self):
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(self.url), reverse("dashboard:home"), fetch_redirect_response=False)

    def test_logout_requires_post(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(response, reverse("accounts:login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_form_rejects_missing_csrf(self):
        from django.test import Client

        strict = Client(enforce_csrf_checks=True)
        response = strict.post(self.url, {"username": "sara", "password": STRONG})
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "نشست منقضی", status_code=403)

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("dashboard:home"))
        self.assertRedirects(response, f"{self.url}?next={reverse('dashboard:home')}")


class ProfileTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("reza")
        self.other = make_user("other")
        self.client.force_login(self.user)

    def test_update_profile(self):
        response = self.client.post(
            reverse("accounts:profile"),
            {"first_name": "رضا", "last_name": "احمدی", "email": "reza.new@example.com", "phone": "09123456789", "bio": "سلام"},
            follow=True,
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "رضا")
        self.assertEqual(self.user.email, "reza.new@example.com")
        self.assertContains(response, "با موفقیت به‌روزرسانی شد")

    def test_cannot_use_someone_elses_email(self):
        response = self.client.post(
            reverse("accounts:profile"),
            {"first_name": "a", "last_name": "b", "email": "OTHER@example.com"},
        )
        self.assertTrue(response.context["form"].errors.get("email"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "reza@example.com")

    def test_profile_cannot_change_role(self):
        self.client.post(
            reverse("accounts:profile"),
            {"first_name": "a", "last_name": "b", "email": "reza@example.com", "role": "super_admin"},
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, Role.USER)

    def test_password_change_success_keeps_session(self):
        new = "An0ther-Strong-Pass!"
        response = self.client.post(
            reverse("accounts:password_change"),
            {"old_password": STRONG, "new_password1": new, "new_password2": new},
            follow=True,
        )
        self.assertContains(response, "رمز عبور شما با موفقیت تغییر کرد")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(new))
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 200)  # هنوز وارد است

    def test_password_change_wrong_old_password(self):
        response = self.client.post(
            reverse("accounts:password_change"),
            {"old_password": "wrong", "new_password1": "An0ther-Strong-Pass!", "new_password2": "An0ther-Strong-Pass!"},
        )
        self.assertContains(response, "رمز عبور فعلی نادرست است")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(STRONG))

    def test_avatar_must_be_real_image(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        fake = SimpleUploadedFile("a.png", b"not an image", content_type="image/png")
        response = self.client.post(
            reverse("accounts:profile"),
            {"first_name": "a", "last_name": "b", "email": "reza@example.com", "avatar": fake},
        )
        self.assertTrue(response.context["form"].errors.get("avatar"))
