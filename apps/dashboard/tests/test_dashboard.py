from django.test import TestCase
from django.urls import reverse

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.dashboard import services


class DashboardTests(TestCase):
    def test_stats_are_real_counts(self):
        make_user("a", Role.ADMIN)
        make_user("b", Role.AUTHOR)
        make_user("c", Role.USER, is_active=False)
        stats = services.site_stats()
        self.assertEqual(stats["total_users"], 3)
        self.assertEqual(stats["active_users"], 2)
        self.assertEqual(stats["inactive_users"], 1)
        self.assertEqual({r["value"]: r["count"] for r in stats["roles"]}["author"], 1)

    def test_profile_completeness(self):
        user = make_user("p")
        self.assertEqual(services.profile_completeness(user), 17)  # فقط ایمیل پر است (۱ از ۶)
        user.first_name, user.last_name, user.phone, user.bio = "الف", "ب", "0912", "x"
        self.assertEqual(services.profile_completeness(user), 83)

    def test_dashboard_renders_for_each_role(self):
        for role in Role:
            if role == Role.USER:
                continue  # a member has no panel (covered in test_members_without_panel)
            self.client.force_login(make_user(role.value, role))
            response = self.client.get(reverse("dashboard:home"))
            self.assertEqual(response.status_code, 200, role)
            self.assertContains(response, "خوش آمدید")
