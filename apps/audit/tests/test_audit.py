from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import STRONG, make_user
from apps.audit.models import AuditLog
from apps.audit.services import log_event

User = get_user_model()


class AuthEventsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("sara")

    def test_login_logout_logged(self):
        self.client.post(reverse("accounts:login"), {"username": "sara", "password": STRONG})
        login = AuditLog.objects.get(action="login")
        self.assertEqual(login.actor, self.user)
        self.assertEqual(login.actor_name, self.user.display_name)
        self.client.post(reverse("accounts:logout"))
        self.assertTrue(AuditLog.objects.filter(action="logout", actor=self.user).exists())

    def test_failed_login_logged_without_password(self):
        self.client.post(reverse("accounts:login"), {"username": "sara", "password": "SuperSecretWrong123"})
        event = AuditLog.objects.get(action="login_failed")
        self.assertIn("sara", event.message)
        self.assertNotIn("SuperSecretWrong123", event.message)
        self.assertIsNone(event.actor)

    def test_registration_logged(self):
        self.client.post(reverse("accounts:register"), {
            "username": "newbie", "first_name": "ن", "last_name": "و", "email": "n@example.com",
            "password1": STRONG, "password2": STRONG})
        self.assertTrue(AuditLog.objects.filter(action="create", object_type="کاربر").exists())


class ServiceTests(TestCase):
    def test_actor_name_survives_user_deletion(self):
        user = make_user("temp")
        request = RequestFactory().get("/")
        request.user = user
        event = log_event(request, "update", user, "تست")
        name = user.display_name
        user.delete()
        event.refresh_from_db()
        self.assertIsNone(event.actor)
        self.assertEqual(event.actor_name, name)

    def test_message_truncated_and_ip_recorded(self):
        request = RequestFactory().get("/", REMOTE_ADDR="10.1.2.3")
        event = log_event(request, "update", message="x" * 1000)
        self.assertEqual(len(event.message), 400)
        self.assertEqual(event.ip_address, "10.1.2.3")

    def test_forwarded_header_is_not_trusted(self):
        request = RequestFactory().get("/", REMOTE_ADDR="10.1.2.3", HTTP_X_FORWARDED_FOR="6.6.6.6")
        self.assertEqual(log_event(request, "update", message="m").ip_address, "10.1.2.3")


class AuditViewTests(TestCase):
    def setUp(self):
        self.admin = make_user("adm", Role.ADMIN)
        log_event(None, "create", self.admin, "ایجاد آزمایشی")
        log_event(None, "delete", message="حذف آزمایشی", object_type="نوشته")
        self.url = reverse("audit:log_list")

    def test_permissions(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        for role in (Role.USER, Role.AUTHOR):
            self.client.force_login(make_user(f"u_{role.value}", role))
            self.assertEqual(self.client.get(self.url).status_code, 403, role)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_filters(self):
        self.client.force_login(self.admin)
        r = self.client.get(self.url, {"action": "delete"})
        self.assertEqual([e.action for e in r.context["events"]], ["delete"])
        r = self.client.get(self.url, {"q": "ایجاد آزمایشی"})
        self.assertEqual(len(r.context["events"]), 1)
        r = self.client.get(self.url, {"type": "نوشته"})
        self.assertEqual(len(r.context["events"]), 1)

    def test_pagination(self):
        for i in range(30):
            log_event(None, "update", message=f"e{i}")
        self.client.force_login(self.admin)  # خود ورود هم یک رویداد ثبت می‌کند
        total = AuditLog.objects.count()
        self.assertGreater(total, 30)
        self.assertEqual(len(self.client.get(self.url).context["events"]), 25)
        self.assertEqual(len(self.client.get(self.url, {"page": 2}).context["events"]), total - 25)

    def test_messages_are_escaped(self):
        log_event(None, "update", message="<script>alert(1)</script>")
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(self.url), "<script>alert(1)</script>")

    def test_admin_site_is_read_only(self):
        from django.contrib import admin

        model_admin = admin.site._registry[AuditLog]
        request = RequestFactory().get("/")
        request.user = make_user("root", Role.SUPER_ADMIN)
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))


class OtherModulesLogTests(TestCase):
    def test_settings_update_and_user_role_change_logged(self):
        admin = make_user("adm", Role.ADMIN)
        target = make_user("tgt", Role.USER)
        self.client.force_login(admin)
        self.client.post(reverse("core:site_settings"), {"site_name": "نام جدید", "registration_open": "on"})
        self.assertTrue(AuditLog.objects.filter(object_type="تنظیمات سایت", actor=admin).exists())
        self.client.post(reverse("accounts:user_manage", args=[target.pk]), {"role": "author", "is_active": "on"})
        self.assertTrue(AuditLog.objects.filter(object_type="کاربر", actor=admin, action="update").exists())
