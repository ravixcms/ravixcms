from django.test import TestCase
from django.urls import reverse

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.core.models import SiteSettings


class APITests(TestCase):
    def test_me_requires_auth(self):
        self.assertIn(self.client.get(reverse("api:me")).status_code, (401, 403))

    def test_me_get_and_patch(self):
        user = make_user("api")
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("api:me")).json()["username"], "api")
        response = self.client.patch(reverse("api:me"), {"first_name": "نام", "role": "super_admin"}, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.first_name, "نام")
        self.assertEqual(user.role, Role.USER)  # نقش از طریق API قابل تغییر نیست

    def test_users_list_restricted(self):
        self.client.force_login(make_user("plain"))
        self.assertEqual(self.client.get(reverse("api:users")).status_code, 403)
        self.client.force_login(make_user("boss", Role.ADMIN))
        self.assertEqual(self.client.get(reverse("api:users")).status_code, 200)

    def test_site_public_read_hides_private_fields(self):
        data = self.client.get(reverse("api:site")).json()
        self.assertIn("site_name", data)
        self.assertNotIn("admin_email", data)

    def test_site_patch_requires_permission(self):
        self.assertIn(self.client.patch(reverse("api:site"), {"site_name": "x"}, content_type="application/json").status_code, (401, 403))
        self.client.force_login(make_user("plain"))
        self.assertEqual(self.client.patch(reverse("api:site"), {"site_name": "x"}, content_type="application/json").status_code, 403)
        self.client.force_login(make_user("boss", Role.ADMIN))
        response = self.client.patch(reverse("api:site"), {"site_name": "جدید"}, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SiteSettings.load().site_name, "جدید")

    def test_dashboard_stats_api(self):
        self.client.force_login(make_user("plain"))
        self.assertEqual(self.client.get(reverse("api:dashboard-stats")).status_code, 403)
        self.client.force_login(make_user("boss", Role.ADMIN))
        data = self.client.get(reverse("api:dashboard-stats")).json()
        self.assertEqual(data["total_users"], 2)
