"""منبع به‌روزرسانی: فقط HTTPS، بدون پیروی از دامنهٔ دیگر، ورودی خراب نادیده، خطای شبکه قابل‌فهم، مسیر ناامن در منبع پوشه‌ای."""
import json
from unittest import mock

from django.test import SimpleTestCase, override_settings

from apps.plugins.sources import RegistryUnavailable
from apps.updater import sources
from apps.updater.errors import UpdateError

from .helpers import UpdaterCase

GOOD = {"version": "0.7.0", "sha256": "a" * 64, "size": 10, "download_url": "https://u.example.com/p.zip", "requires": {}}


def index(core=None, **extra):
    return json.dumps({"schema": 1, "core": core if core is not None else [GOOD], **extra}).encode()


class IndexParsing(SimpleTestCase):
    def test_valid_and_malformed_entries(self):
        idx = sources.parse_index(index([GOOD, {"version": "x", "sha256": "a" * 64}, {"version": "0.8.0", "sha256": "zz"}, "junk", {**GOOD, "version": "0.9.0", "size": 10**12}]))
        self.assertEqual([e["version"] for e in idx["core"]], ["0.7.0"])

    def test_bad_schema_and_json(self):
        for data in (b"{not json", json.dumps({"schema": 99, "core": []}).encode(), b"[]"):
            with self.assertRaises(UpdateError):
                sources.parse_index(data)

    def test_theme_slug_validation(self):
        idx = sources.parse_index(index(themes={"ok_theme": [GOOD], "../evil": [GOOD], "UP": [GOOD]}))
        self.assertEqual(list(idx["themes"]), ["ok_theme"])


class HttpSourceTests(UpdaterCase):
    def http(self):
        return sources.HttpSource("https://u.example.com")

    def test_index_fetched_over_https_helper_and_signature_optional(self):
        calls = []

        def fake(url, max_bytes, allow_insecure=False):
            calls.append(url)
            if url.endswith("index.json"):
                return index(), {}
            raise RegistryUnavailable("404")

        with mock.patch("apps.plugins.sources.http_get", side_effect=fake):
            idx = self.http().index()
        self.assertEqual(idx["core"][0]["version"], "0.7.0")
        self.assertEqual(calls[0], "https://u.example.com/updates/v1/index.json")

    def test_network_error_becomes_update_error(self):
        with mock.patch("apps.plugins.sources.http_get", side_effect=RegistryUnavailable("اتصال ممکن نیست")):
            with self.assertRaisesRegex(UpdateError, "دریافت از منبع"):
                self.http().index()

    def test_cross_domain_download_refused(self):
        with mock.patch("apps.plugins.sources.http_get") as g:
            with self.assertRaisesRegex(UpdateError, "دامنه"):
                self.http().download({**GOOD, "download_url": "https://evil.example.org/p.zip"})
            g.assert_not_called()

    def test_http_scheme_rejected_by_helper(self):
        from apps.plugins.sources import http_get

        with self.assertRaises(RegistryUnavailable):
            http_get("http://u.example.com/x", 10)

    def test_no_source_configured(self):
        with override_settings(RAVIX_UPDATE_DIR="", RAVIX_UPDATE_URL=""):
            self.assertFalse(sources.has_source())
            with self.assertRaisesRegex(UpdateError, "تنظیم نشده"):
                sources.get_source()

    def test_dir_source_blocks_traversal(self):
        self.dist.mkdir(parents=True, exist_ok=True)
        (self.base / "secret.txt").write_text("top secret")
        s = sources.DirSource(self.dist)
        with self.assertRaises(UpdateError):
            s.download({**GOOD, "download_url": "../secret.txt"})
        with self.assertRaises(UpdateError):
            s.download({**GOOD, "download_url": ""})
