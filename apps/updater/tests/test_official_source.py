"""سرور رسمی به‌روزرسانی/مخزن در دسترس نیست ⇒ سایت و پنل خطا نمی‌دهند؛ کلید رسمی داخل کد و ابطال کلید."""
import socket
import urllib.error
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.core import official
from apps.plugins import signing as psigning
from apps.plugins.models import TrustedKey
from apps.updater import signing as usigning
from apps.updater import sources

DEAD = "https://updates.invalid.example"
DEAD_REG = "https://registry.invalid.example"
FAILURES = [
    urllib.error.URLError(ConnectionRefusedError(111, "refused")),
    urllib.error.URLError(socket.gaierror(-2, "Name or service not known")),
    TimeoutError("timed out"),
    urllib.error.HTTPError(DEAD, 503, "down", {}, None),
    urllib.error.HTTPError(DEAD, 404, "nf", {}, None),
]


def _urlopen_fail(exc):
    def opener(*a, **k):
        raise exc
    return opener


@override_settings(RAVIX_UPDATE_URL=DEAD, RAVIX_UPDATE_DIR="", RAVIX_OFFICIAL_REGISTRY_URL=DEAD_REG)
class UnreachableServerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.admin = make_user("boss", Role.SUPER_ADMIN)
        self.c = Client()
        self.c.force_login(self.admin)

    def _pages(self):
        return [reverse("updater:home"), reverse("plugins:list"), reverse("dashboard:home"), reverse("core:site_health"),
                reverse("plugins:trusted_keys")]

    def test_public_site_never_touches_network(self):
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("network used")) as m:
            for url in ("/fa/", "/fa/blog/", "/healthz", "/fa/nothing/"):
                self.assertIn(Client().get(url).status_code, (200, 404))
        m.assert_not_called()

    def test_panel_pages_ok_for_every_failure_kind(self):
        for exc in FAILURES:
            cache.clear()
            with mock.patch("urllib.request.OpenerDirector.open", side_effect=exc):
                for url in self._pages():
                    r = self.c.get(url)
                    self.assertEqual(r.status_code, 200, (type(exc).__name__, url))

    def test_updates_page_explains_and_failure_is_cached(self):
        calls = []

        def boom(*a, **k):
            calls.append(1)
            raise urllib.error.URLError(ConnectionRefusedError(111, "refused"))

        with mock.patch("urllib.request.OpenerDirector.open", side_effect=boom):
            r = self.c.get(reverse("updater:home"))
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, "ممکن نشد")
            first = len(calls)
            self.assertGreaterEqual(first, 1)
            for _ in range(3):
                self.assertEqual(self.c.get(reverse("updater:home")).status_code, 200)
            self.assertEqual(len(calls), first)  # کش منفی: بازدیدهای بعدی شبکه را صدا نمی‌زنند

    def test_check_button_bypasses_negative_cache_and_recovers(self):
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=TimeoutError("t")):
            self.c.get(reverse("updater:home"))
            r = self.c.post(reverse("updater:check"), follow=True)
            self.assertEqual(r.status_code, 200)
        good = mock.MagicMock()
        idx = b'{"schema": 1, "channel": "stable", "core": [], "themes": {}}'
        with mock.patch("apps.updater.sources._http_get", return_value=(idx, {})):
            self.assertEqual(sources.cached_index(force=True)["core"], [])
            self.assertEqual(sources.cached_index()["core"], [])  # بازیابی: خطای قبلی پاک شد
        del good

    def test_garbage_responses_are_handled(self):
        for body in (b"<html>nope</html>", b"{}", b'{"schema": 99}', b"\xff\xfe"):
            cache.clear()
            with mock.patch("apps.updater.sources._http_get", return_value=(body, {})):
                self.assertEqual(self.c.get(reverse("updater:home")).status_code, 200)

    def test_short_timeouts_used_for_index(self):
        seen = []

        def spy(url, max_bytes, allow_insecure=False, timeout=30):
            seen.append(timeout)
            raise urllib.error.URLError("x")

        with mock.patch("apps.plugins.sources.http_get", side_effect=spy):
            self.c.get(reverse("updater:home"))
        self.assertTrue(seen and max(seen) <= 10, seen)

    def test_management_command_fails_cleanly_not_traceback(self):
        from io import StringIO

        from django.core.management import call_command
        from django.core.management.base import CommandError

        with mock.patch("urllib.request.OpenerDirector.open", side_effect=TimeoutError("t")):
            with self.assertRaises(CommandError):
                call_command("ravix_update", "check", stdout=StringIO(), stderr=StringIO())


class OfficialDefaultsTests(TestCase):
    def test_defaults_come_from_official_module_and_env_overrides(self):
        import importlib
        import os

        from config.settings import base

        with mock.patch.object(official, "OFFICIAL_UPDATE_URL", "https://u.example.com/"), \
                mock.patch.object(official, "OFFICIAL_REGISTRY_URL", "https://r.example.com"), \
                mock.patch.dict(os.environ, {}, clear=False):
            for name in ("RAVIX_UPDATE_URL", "RAVIN_UPDATE_URL", "RAVIX_OFFICIAL_REGISTRY_URL", "RAVIN_OFFICIAL_REGISTRY_URL"):
                os.environ.pop(name, None)
            m = importlib.reload(base)
            self.assertEqual(m.RAVIX_UPDATE_URL, "https://u.example.com")
            self.assertEqual(m.RAVIX_OFFICIAL_REGISTRY_URL, "https://r.example.com")
            os.environ["RAVIX_UPDATE_URL"] = "https://mirror.example.org"
            os.environ["RAVIX_OFFICIAL_REGISTRY_URL"] = ""  # خالیِ صریح = خاموش
            m = importlib.reload(base)
            self.assertEqual(m.RAVIX_UPDATE_URL, "https://mirror.example.org")
            self.assertEqual(m.RAVIX_OFFICIAL_REGISTRY_URL, "")
        for name in ("RAVIX_UPDATE_URL", "RAVIX_OFFICIAL_REGISTRY_URL"):
            os.environ.pop(name, None)
        importlib.reload(base)

    def test_shipped_defaults_are_empty_placeholders(self):
        # این آزمون عمداً می‌شکند تا ناشر پس از پر کردن official.py آن را آگاهانه به‌روز کند.
        self.assertEqual((official.OFFICIAL_UPDATE_URL, official.OFFICIAL_REGISTRY_URL, official.OFFICIAL_PUBLIC_KEYS, official.REVOKED_KEY_IDS),
                         ("", "", (), ()))


class KeyTrustTests(TestCase):
    def setUp(self):
        self.seed, self.pub = psigning.generate_keypair()
        self.kid = psigning.key_id(self.pub)
        self.sig, _ = usigning.sign(self.seed, "core", "", "0.7.0", "a" * 64)

    def verify(self):
        return usigning.verify("core", "", "0.7.0", "a" * 64, self.sig, self.kid)

    def test_builtin_key_is_trusted_and_listed(self):
        with mock.patch.object(official, "OFFICIAL_PUBLIC_KEYS", (f"release-2026:{self.pub}",)):
            self.assertTrue(self.verify().ok)
            k = psigning.trusted_keys()[self.kid]
            self.assertEqual((k.source, k.label), ("builtin", "release-2026"))

    def test_untrusted_by_default(self):
        self.assertFalse(self.verify().ok)

    def test_revocation_beats_every_source(self):
        cases = [
            dict(builtin=True), dict(env=True), dict(db=True),
        ]
        for case in cases:
            with self.subTest(case=case):
                TrustedKey.objects.all().delete()
                with override_settings(RAVIX_TRUSTED_PUBLIC_KEYS=[self.pub] if case.get("env") else []), \
                        mock.patch.object(official, "OFFICIAL_PUBLIC_KEYS", (self.pub,) if case.get("builtin") else ()):
                    if case.get("db"):
                        TrustedKey.objects.create(label="x", public_key=self.pub, key_id=self.kid, is_active=True)
                    self.assertTrue(self.verify().ok)
                    with override_settings(RAVIX_REVOKED_KEY_IDS=[self.kid]):
                        self.assertFalse(self.verify().ok)
                        self.assertNotIn(self.kid, psigning.trusted_keys())
                    with mock.patch.object(official, "REVOKED_KEY_IDS", (self.kid.upper(),)):
                        self.assertFalse(self.verify().ok)

    def test_rotation_old_signature_rejected_new_accepted(self):
        seed2, pub2 = psigning.generate_keypair()
        kid2 = psigning.key_id(pub2)
        sig2, _ = usigning.sign(seed2, "core", "", "0.7.0", "a" * 64)
        with mock.patch.object(official, "OFFICIAL_PUBLIC_KEYS", (self.pub, pub2)):
            self.assertTrue(self.verify().ok)
            self.assertTrue(usigning.verify("core", "", "0.7.0", "a" * 64, sig2, kid2).ok)
            with override_settings(RAVIX_REVOKED_KEY_IDS=[self.kid]):
                self.assertFalse(self.verify().ok)
                self.assertTrue(usigning.verify("core", "", "0.7.0", "a" * 64, sig2, kid2).ok)

    def test_panel_shows_builtin_and_revoked(self):
        admin = make_user("boss", Role.SUPER_ADMIN)
        c = Client()
        c.force_login(admin)
        with mock.patch.object(official, "OFFICIAL_PUBLIC_KEYS", (f"rel:{self.pub}",)), override_settings(RAVIX_REVOKED_KEY_IDS=["deadbeefdeadbeef"]):
            r = c.get(reverse("plugins:trusted_keys"))
        self.assertContains(r, "کد برنامه")
        self.assertContains(r, "deadbeefdeadbeef")


class EndToEndOverHttpTests(__import__("apps.updater.tests.helpers", fromlist=["UpdaterCase"]).UpdaterCase):
    """همان مسیر سرور رسمی: ravix_release ⇒ index.json با نام فایل نسبی ⇒ HttpSource ⇒ امضا با کلید رسمی داخل کد ⇒ ارتقا."""

    def _serve(self):
        base = "https://updates.example.com/updates/v1/"

        def fake(url, max_bytes, allow_insecure=False, timeout=30):
            assert url.startswith(base), url
            return (self.dist / url[len(base):]).read_bytes(), {}

        return mock.patch("apps.plugins.sources.http_get", side_effect=fake)

    def test_upgrade_through_official_http_source_with_builtin_key(self):
        from apps.updater import pipeline
        from apps.updater.tests.helpers import SEED

        self.publish("0.9.0", sign_index=True)
        pub = psigning.load_private_key(SEED).public_key().public_bytes_raw()
        import base64

        pub_b64 = base64.b64encode(pub).decode()
        with override_settings(RAVIX_UPDATE_URL="https://updates.example.com", RAVIX_UPDATE_DIR="", RAVIX_TRUSTED_PUBLIC_KEYS=[], RAVIX_UPDATE_REQUIRE_SIGNED_INDEX=True), \
                mock.patch.object(official, "OFFICIAL_PUBLIC_KEYS", (f"official:{pub_b64}",)), self._serve():
            src = sources.get_source()
            self.assertEqual(src.kind, "official")  # HttpSource، نه پوشه‌ای
            run = self.apply("0.9.0", source=src)
            self.assertEqual(run.status, "succeeded")
            self.assertEqual(self.disk(), "0.9.0")
        del pipeline

    def test_revoked_official_key_blocks_upgrade_before_any_change(self):
        import base64

        from apps.updater.errors import UpdateError
        from apps.updater.tests.helpers import SEED

        self.publish("0.9.0", sign_index=True)
        pub_b64 = base64.b64encode(psigning.load_private_key(SEED).public_key().public_bytes_raw()).decode()
        before = self.disk()
        with override_settings(RAVIX_UPDATE_URL="https://updates.example.com", RAVIX_UPDATE_DIR="", RAVIX_TRUSTED_PUBLIC_KEYS=[],
                               RAVIX_REVOKED_KEY_IDS=[psigning.key_id(pub_b64)]), \
                mock.patch.object(official, "OFFICIAL_PUBLIC_KEYS", (f"official:{pub_b64}",)), self._serve():
            with self.assertRaises(UpdateError):
                self.apply("0.9.0", source=sources.get_source())
        self.assertEqual(self.disk(), before)
