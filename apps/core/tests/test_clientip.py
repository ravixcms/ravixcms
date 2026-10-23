"""تعیین IP کلاینت: اعتماد فقط به پروکسی مورداعتماد، راست‌ترین IP غیرمورداعتماد، جعل هدر، IP نامعلوم، و مصرف‌کننده‌ها."""
from django.core.cache import cache
from django.core.management import call_command
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from io import StringIO

from apps.core import clientip, sitehealth

RF = RequestFactory()


def req(**meta):
    meta.setdefault("REMOTE_ADDR", "")
    return RF.get("/fa/", **meta)


class ResolveTests(SimpleTestCase):
    def test_direct_connection_ignores_spoofed_headers(self):
        r = req(REMOTE_ADDR="203.0.113.5", HTTP_X_FORWARDED_FOR="6.6.6.6", HTTP_X_REAL_IP="7.7.7.7")
        self.assertEqual(clientip.client_ip(r), "203.0.113.5")

    def test_unix_socket_is_trusted_and_reads_real_ip(self):
        self.assertEqual(clientip.client_ip(req(HTTP_X_REAL_IP="198.51.100.9")), "198.51.100.9")
        self.assertEqual(clientip.client_ip(req(HTTP_X_FORWARDED_FOR="198.51.100.9")), "198.51.100.9")

    def test_attacker_cannot_forge_leftmost_entry_behind_trusted_proxy(self):
        # مهاجم "1.2.3.4" را در XFF می‌نویسد؛ nginx نشانی واقعی (198.51.100.9) را به راست اضافه می‌کند
        r = req(HTTP_X_FORWARDED_FOR="1.2.3.4, 198.51.100.9")
        self.assertEqual(clientip.client_ip(r), "198.51.100.9")

    def test_rightmost_untrusted_skips_trusted_hops(self):
        with override_settings(RAVIX_TRUSTED_PROXIES=["10.0.0.0/8", "127.0.0.1"]):
            r = req(REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="9.9.9.9, 198.51.100.9, 10.1.1.1, 10.2.2.2")
            self.assertEqual(clientip.client_ip(r), "198.51.100.9")

    def test_tcp_proxy_not_listed_is_not_trusted(self):
        r = req(REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="1.2.3.4")
        self.assertEqual(clientip.client_ip(r), "127.0.0.1")
        with override_settings(RAVIX_TRUSTED_PROXIES=["127.0.0.1"]):
            self.assertEqual(clientip.client_ip(req(REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="1.2.3.4")), "1.2.3.4")

    def test_untrusted_remote_cannot_become_trusted_by_header(self):
        with override_settings(RAVIX_TRUSTED_PROXIES=["10.0.0.0/8"]):
            r = req(REMOTE_ADDR="203.0.113.7", HTTP_X_FORWARDED_FOR="10.0.0.1")
            self.assertEqual(clientip.client_ip(r), "203.0.113.7")

    def test_unknown_when_trusted_proxy_sends_no_or_garbage_header(self):
        self.assertIsNone(clientip.client_ip(req()))
        self.assertIsNone(clientip.client_ip(req(HTTP_X_FORWARDED_FOR="not-an-ip")))
        self.assertIsNone(clientip.client_ip(req(HTTP_X_FORWARDED_FOR="1.2.3.4, garbage")))

    def test_ipv6_and_invalid_settings(self):
        self.assertEqual(clientip.client_ip(req(HTTP_X_REAL_IP="2001:db8::1")), "2001:db8::1")
        with override_settings(RAVIX_TRUSTED_PROXIES=["oops", "127.0.0.1"]):
            self.assertEqual(clientip.invalid_trusted_entries(), ["oops"])
            self.assertEqual(clientip.client_ip(req(REMOTE_ADDR="127.0.0.1", HTTP_X_REAL_IP="5.5.5.5")), "5.5.5.5")


class ConsumersTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_forged_xff_does_not_evade_login_ip_limit(self):
        from apps.accounts import throttle

        for i in range(throttle.MAX_ATTEMPTS_PER_IP):
            r = req(REMOTE_ADDR="203.0.113.5", HTTP_X_FORWARDED_FOR=f"1.1.1.{i}")
            throttle.register_failure(r, f"user{i}")
        self.assertTrue(throttle.is_locked(req(REMOTE_ADDR="203.0.113.5", HTTP_X_FORWARDED_FOR="8.8.8.8"), "other"))

    def test_unknown_ip_has_no_global_lock_but_per_username_lock(self):
        from apps.accounts import throttle

        anon = req()
        for i in range(throttle.MAX_ATTEMPTS_PER_IP + 5):
            throttle.register_failure(anon, f"user{i}")
        self.assertFalse(throttle.is_locked(anon, "innocent"))  # قفل سراسری نیست
        for _ in range(throttle.MAX_ATTEMPTS):
            throttle.register_failure(anon, "victim")
        self.assertTrue(throttle.is_locked(anon, "victim"))  # قفل به‌ازای نام‌کاربری برقرار است

    def test_unknown_ip_mail_limit_is_per_email_only(self):
        from apps.accounts import throttle

        anon = req()
        results = [throttle.allow_send(anon, f"u{i}@example.com", "reset") for i in range(30)]
        self.assertTrue(all(results))
        n = 0
        while throttle.allow_send(anon, "same@example.com", "reset") and n < 50:
            n += 1
        self.assertLess(n, 50)

    def test_comment_rate_limit_skipped_for_unknown_ip(self):
        from apps.comments import services

        self.assertFalse(any(services.rate_limited(req()) for _ in range(50)))

    def test_audit_uses_central_function(self):
        from apps.audit import services

        self.assertEqual(services._client_ip(req(HTTP_X_FORWARDED_FOR="1.2.3.4, 198.51.100.9")), "198.51.100.9")
        self.assertEqual(services._client_ip(req(REMOTE_ADDR="203.0.113.5", HTTP_X_FORWARDED_FOR="6.6.6.6")), "203.0.113.5")


class HealthAndPreflightTests(TestCase):
    def setUp(self):
        cache.clear()

    def by_key(self, request):
        return {c.key: c for c in sitehealth.check_client_ip(request)}

    def test_health_good_behind_unix_socket(self):
        self.assertEqual(self.by_key(req(HTTP_X_REAL_IP="198.51.100.9"))["client_ip"].status, sitehealth.GOOD)

    def test_health_critical_when_proxy_not_trusted(self):
        c = self.by_key(req(REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="198.51.100.9"))["client_ip"]
        self.assertEqual(c.status, sitehealth.CRITICAL)
        self.assertIn("RAVIX_TRUSTED_PROXIES", c.fix)

    def test_health_recommended_when_headers_missing(self):
        self.assertEqual(self.by_key(req())["client_ip"].status, sitehealth.RECOMMENDED)

    def test_health_flags_invalid_setting(self):
        with override_settings(RAVIX_TRUSTED_PROXIES=["junk"]):
            self.assertEqual(self.by_key(req(HTTP_X_REAL_IP="1.2.3.4"))["client_ip_config"].status, sitehealth.CRITICAL)

    def preflight(self):
        out = StringIO()
        try:
            call_command("ravix_preflight", stdout=out)
        except SystemExit:
            pass
        return out.getvalue()

    def test_preflight_reports_observed_untrusted_proxy(self):
        clientip.client_ip(req(REMOTE_ADDR="10.0.0.9", HTTP_X_FORWARDED_FOR="198.51.100.9"))  # ثبت مشاهده
        self.assertIn("مورداعتماد نیست", self.preflight())

    def test_preflight_ok_without_observations(self):
        self.assertIn("تشخیص IP کلاینت", self.preflight())
        self.assertNotIn("مورداعتماد نیست", self.preflight())

    def test_preflight_rejects_invalid_proxy_setting(self):
        with override_settings(RAVIX_TRUSTED_PROXIES=["junk"]):
            self.assertIn("RAVIX_TRUSTED_PROXIES شامل ورودی نامعتبر", self.preflight())
