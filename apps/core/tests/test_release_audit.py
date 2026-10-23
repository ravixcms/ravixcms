"""ممیزی پیش از انتشار: هیچ مسیر پنل/API بدون ورود پاسخ نمی‌دهد، نقش‌های پایین به بخش‌های مدیریتی راه ندارند، هدرهای امنیتی، XSS/SQLi/آپلود."""
import re

from django.test import Client, TestCase
from django.urls import URLPattern, URLResolver, get_resolver

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user

CONVERTERS = re.compile(r"<(?:(?:int|slug|str|path|uuid):)?[^>]+>")
PREFIXES = ("panel/", "api/v1/")
# مسیرهایی که عمداً برای ناشناس‌ها باز یا توکنی هستند
PUBLIC_OK = ("api/v1/registry/", "api/v1/shop/payments/", "panel/login", "accounts/",
             "api/v1/site/", "api/v1/shop/categories/", "api/v1/shop/products/", "api/v1/shop/gateways/")  # فهرست عمومیِ عمداً باز (فقط داده‌ٔ نمایشی)


def all_routes(patterns=None, prefix=""):
    for p in patterns if patterns is not None else get_resolver().url_patterns:
        route = prefix + str(p.pattern)
        if isinstance(p, URLResolver):
            yield from all_routes(p.url_patterns, route)
        elif isinstance(p, URLPattern):
            if "^" in route or "(?P" in route:
                continue
            yield "/" + CONVERTERS.sub(lambda m: "1" if "int:" in m.group(0) else "x", route)


def guarded_routes():
    return sorted({r for r in all_routes() if r.lstrip("/").startswith(PREFIXES) and not r.lstrip("/").startswith(PUBLIC_OK)})


class AnonymousAccess(TestCase):
    def test_every_panel_and_api_route_refuses_anonymous(self):
        routes = guarded_routes()
        self.assertGreater(len(routes), 80)
        c = Client()
        bad = []
        for r in routes:
            for method in (c.get, c.post):
                resp = method(r)
                ok = resp.status_code in (301, 302, 401, 403, 404, 405) and (resp.status_code not in (301, 302) or "login" in resp["Location"] or resp["Location"].endswith("/"))
                if not ok:
                    bad.append((method.__name__, r, resp.status_code))
        self.assertEqual(bad, [])


class LowRoleAccess(TestCase):
    ALLOWED_FOR_USER = ("/panel/", "/panel/profile", "/panel/logout")

    def test_plain_user_cannot_open_management_pages(self):
        u = make_user("plainu", Role.USER)
        c = Client()
        c.force_login(u)
        leaks = []
        for r in guarded_routes():
            if r.startswith("/api/"):
                continue
            resp = c.get(r)
            if resp.status_code == 200 and not r.startswith(self.ALLOWED_FOR_USER):
                leaks.append(r)
        self.assertEqual(leaks, [], "کاربر عادی به این صفحات مدیریتی دسترسی دارد")

    def test_author_cannot_reach_system_areas(self):
        c = Client()
        c.force_login(make_user("authu", Role.AUTHOR))
        for r in ("/panel/updates/", "/panel/plugins/", "/panel/plugins/install-package/", "/panel/plugins/trusted-keys/", "/panel/themes/", "/panel/audit/"):
            self.assertIn(c.get(r).status_code, (403, 404), r)

    def test_admin_cannot_apply_updates_or_manage_keys(self):
        c = Client()
        c.force_login(make_user("admu", Role.ADMIN))
        self.assertEqual(c.post("/panel/updates/apply/core/").status_code, 403)
        self.assertEqual(c.get("/panel/plugins/trusted-keys/").status_code, 403)

    def test_django_admin_requires_staff_superuser(self):
        c = Client()
        c.force_login(make_user("admu2", Role.ADMIN))
        self.assertEqual(c.get("/django-admin/").status_code, 302)  # مدیر عادی: به ورود admin هدایت می‌شود
        c.force_login(make_user("rootu", Role.SUPER_ADMIN))
        self.assertEqual(c.get("/django-admin/").status_code, 200)


class Headers(TestCase):
    def test_csp_and_permissions_policy_on_all_areas(self):
        c = Client()
        c.force_login(make_user("hdr", Role.SUPER_ADMIN))
        for url in ("/panel/", "/panel/updates/", "/accounts/login/", "/api/v1/me/", "/"):
            r = c.get(url)
            csp = r.get("Content-Security-Policy", "")
            self.assertIn("script-src 'self'", csp, url)
            self.assertNotIn("script-src 'self' 'unsafe", csp, url)
            self.assertIn("object-src 'none'", csp, url)
            self.assertIn("Permissions-Policy", r, url)
            self.assertEqual(r["X-Content-Type-Options"], "nosniff", url)
            self.assertIn(r["X-Frame-Options"], ("DENY", "SAMEORIGIN"), url)

    def test_no_inline_event_handlers_or_scripts_in_templates(self):
        import pathlib
        from django.conf import settings

        bad = []
        for base in (settings.BASE_DIR / "templates", settings.BASE_DIR / "themes", settings.BASE_DIR / "plugins"):
            for p in base.rglob("*.html"):
                text = p.read_text(encoding="utf-8")
                if re.search(r"\son[a-z]+=\"", text) or re.search(r"<script(?![^>]*\bsrc=)(?![^>]*ld\+json)[^>]*>", text):
                    bad.append(str(p.relative_to(settings.BASE_DIR)))
        self.assertEqual(bad, [], "CSP: اسکریپت درون‌خطی ممنوع است")

    def test_login_cookies_flags(self):
        from apps.accounts.tests.test_auth import make_user as mk

        mk("cookieu", Role.USER, password="Str0ng-pass-123")
        c = Client()
        r = c.post("/accounts/login/", {"username": "cookieu", "password": "Str0ng-pass-123"})
        sess = r.cookies.get("sessionid")
        if sess:
            self.assertTrue(sess["httponly"])
            self.assertEqual(sess["samesite"], "Lax")


class ErrorPagesDoNotLeak(TestCase):
    def test_404_has_no_debug_info(self):
        r = Client().get("/no/such/page/zzz")
        self.assertEqual(r.status_code, 404)
        self.assertNotIn("Traceback", r.content.decode())
        self.assertNotIn("SECRET_KEY", r.content.decode())

    def test_sql_injection_payloads_are_inert(self):
        c = Client()
        for q in ("' OR 1=1--", "\"; DROP TABLE content_post;--", "%27%29%20UNION%20SELECT%20password%20FROM%20accounts_user--"):
            for url in ("/search/", "/blog/", "/"):
                r = c.get(url, {"q": q, "s": q, "page": q, "category": q})
                self.assertLess(r.status_code, 500, (url, q))
                self.assertNotIn("password", r.content.decode().lower().replace("type=\"password\"", ""))

    def test_xss_payload_is_escaped_in_search_echo(self):
        r = Client().get("/fa/search/", {"q": "<script>alert(1)</script>"})
        self.assertNotIn("<script>alert(1)</script>", r.content.decode())


class LoginThrottle(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        make_user("thr", Role.USER, password="Str0ng-pass-123")

    def attempt(self, user, pw="bad", **meta):
        return Client().post("/accounts/login/", {"username": user, "password": pw}, **meta)

    def test_account_locked_after_five_failures_even_with_right_password(self):
        for _ in range(5):
            self.attempt("thr")
        r = self.attempt("thr", "Str0ng-pass-123")
        self.assertNotIn("_auth_user_id", r.client.session)

    def test_rotating_usernames_hits_per_ip_cap(self):
        from apps.accounts import throttle

        for i in range(throttle.MAX_ATTEMPTS_PER_IP):
            self.attempt(f"nobody{i}")
        r = self.attempt("thr", "Str0ng-pass-123")
        self.assertNotIn("_auth_user_id", r.client.session)

    def test_spoofed_real_ip_header_ignored_unless_trusted(self):
        from django.test import override_settings

        for i in range(5):
            self.attempt("thr", REMOTE_ADDR="10.0.0.1", HTTP_X_REAL_IP=f"203.0.113.{i}")  # تلاش برای دور زدن با سربرگ جعلی
        r = self.attempt("thr", "Str0ng-pass-123", REMOTE_ADDR="10.0.0.1", HTTP_X_REAL_IP="198.51.100.9")
        self.assertNotIn("_auth_user_id", r.client.session)
        with override_settings(RAVIX_TRUSTED_PROXIES=["127.0.0.1"]):  # پشت پراکسی مورد اعتماد، هر کلاینتِ واقعی جداگانه شمرده می‌شود
            r = self.attempt("thr", "Str0ng-pass-123", REMOTE_ADDR="127.0.0.1", HTTP_X_REAL_IP="198.51.100.77")
            self.assertEqual(r.status_code, 302)
