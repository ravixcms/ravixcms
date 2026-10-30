from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.accounts.permissions import ROLE_CAPABILITIES, Role

from .test_auth import STRONG, make_user

User = get_user_model()


class RoleModelTests(TestCase):
    def test_role_flags_synced(self):
        self.assertTrue(make_user("sa", Role.SUPER_ADMIN).is_superuser)
        admin = make_user("ad", Role.ADMIN)
        self.assertTrue(admin.is_staff)
        self.assertFalse(admin.is_superuser)
        author = make_user("au", Role.AUTHOR)
        self.assertFalse(author.is_staff)

    def test_createsuperuser_gets_super_admin_role(self):
        user = User.objects.create_superuser("root", "root@example.com", STRONG)
        self.assertEqual(user.role, Role.SUPER_ADMIN)

    def test_capabilities_per_role(self):
        user, author, admin, sa = (make_user(n, r) for n, r in
                                   [("u", Role.USER), ("a", Role.AUTHOR), ("m", Role.ADMIN), ("s", Role.SUPER_ADMIN)])
        self.assertFalse(user.has_cap("content.write"))
        self.assertTrue(author.has_cap("content.write"))
        self.assertFalse(author.has_cap("users.manage"))
        self.assertTrue(admin.has_cap("settings.manage"))
        self.assertTrue(all(sa.has_cap(c) for c in ROLE_CAPABILITIES[Role.SUPER_ADMIN]))

    def test_inactive_user_has_no_capabilities(self):
        user = make_user("x", Role.ADMIN, is_active=False)
        self.assertFalse(user.has_cap("dashboard.view"))

    def test_management_hierarchy(self):
        sa, admin, author, user = (make_user(n, r) for n, r in
                                   [("s", Role.SUPER_ADMIN), ("m", Role.ADMIN), ("a", Role.AUTHOR), ("u", Role.USER)])
        self.assertTrue(sa.can_manage(admin))
        self.assertTrue(admin.can_manage(author))
        self.assertFalse(admin.can_manage(sa))
        self.assertFalse(admin.can_manage(make_user("m2", Role.ADMIN)))
        self.assertFalse(author.can_manage(user))
        self.assertFalse(sa.can_manage(sa))
        self.assertEqual(set(admin.assignable_roles()), {Role.USER, Role.AUTHOR})


class AccessControlTests(TestCase):
    def setUp(self):
        self.users = {r: make_user(r.value, r) for r in Role}

    def get(self, role, name, **kwargs):
        self.client.force_login(self.users[role])
        return self.client.get(reverse(name, **kwargs))

    def test_settings_page_access(self):
        self.assertEqual(self.get(Role.USER, "core:site_settings").status_code, 403)
        self.assertEqual(self.get(Role.AUTHOR, "core:site_settings").status_code, 403)
        self.assertEqual(self.get(Role.ADMIN, "core:site_settings").status_code, 200)
        self.assertEqual(self.get(Role.SUPER_ADMIN, "core:site_settings").status_code, 200)

    def test_user_list_access(self):
        self.assertEqual(self.get(Role.AUTHOR, "accounts:user_list").status_code, 403)
        self.assertEqual(self.get(Role.ADMIN, "accounts:user_list").status_code, 200)

    def test_anonymous_redirected(self):
        response = self.client.get(reverse("core:site_settings"))
        self.assertEqual(response.status_code, 302)

    def test_403_page_is_persian(self):
        response = self.get(Role.USER, "core:site_settings")
        self.assertContains(response, "دسترسی غیرمجاز", status_code=403)

    def test_sidebar_filtered_by_role(self):
        user_html = self.get(Role.AUTHOR, "dashboard:home").content.decode()
        admin_html = self.get(Role.ADMIN, "dashboard:home").content.decode()
        self.assertNotIn(reverse("core:site_settings"), user_html)
        self.assertIn(reverse("core:site_settings"), admin_html)

    def test_dashboard_stats_only_for_admins(self):
        self.assertNotIn("stats", self.get(Role.AUTHOR, "dashboard:home").context)
        self.assertEqual(self.get(Role.ADMIN, "dashboard:home").context["stats"]["total_users"], 4)


class UserManagementTests(TestCase):
    def setUp(self):
        self.sa = make_user("sa", Role.SUPER_ADMIN)
        self.admin = make_user("adm", Role.ADMIN)
        self.author = make_user("aut", Role.AUTHOR)
        self.user = make_user("usr", Role.USER)

    def url(self, target):
        return reverse("accounts:user_manage", args=[target.pk])

    def test_admin_can_promote_user_to_author(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.url(self.user), {"role": "author", "is_active": "on"})
        self.assertRedirects(response, reverse("accounts:user_list"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, Role.AUTHOR)

    def test_admin_cannot_grant_admin_or_super_admin(self):
        self.client.force_login(self.admin)
        for role in ("admin", "super_admin"):
            response = self.client.post(self.url(self.user), {"role": role, "is_active": "on"})
            self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, Role.USER)

    def test_admin_cannot_edit_super_admin_or_self(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url(self.sa)).status_code, 403)
        self.assertEqual(self.client.get(self.url(self.admin)).status_code, 403)

    def test_author_cannot_manage(self):
        self.client.force_login(self.author)
        self.assertEqual(self.client.post(self.url(self.user), {"role": "admin"}).status_code, 403)

    def test_deactivate_user(self):
        self.client.force_login(self.admin)
        self.client.post(self.url(self.author), {"role": "author"})  # is_active فرستاده نشده = غیرفعال
        self.author.refresh_from_db()
        self.assertFalse(self.author.is_active)

    def test_super_admin_can_demote_other_super_admin(self):
        other = make_user("sa2", Role.SUPER_ADMIN)
        self.client.force_login(self.sa)
        self.client.post(self.url(other), {"role": "admin", "is_active": "on"})
        other.refresh_from_db()
        self.assertEqual(other.role, Role.ADMIN)
        self.assertFalse(other.is_superuser)

    def test_user_list_search_and_filter(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("accounts:user_list"), {"q": "usr"})
        self.assertEqual([u.username for u in response.context["users"]], ["usr"])
        response = self.client.get(reverse("accounts:user_list"), {"role": "author"})
        self.assertEqual([u.username for u in response.context["users"]], ["aut"])
