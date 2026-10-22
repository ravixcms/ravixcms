import io
import zipfile

from django.test import override_settings

from apps.pluginhub.packages import PackageError, inspect

from .helpers import HubTestCase, make_zip, manifest


class PackageValidationTests(HubTestCase):
    def bad(self, data, text=None):
        with self.assertRaises(PackageError) as cm:
            inspect(data)
        if text:
            self.assertIn(text, str(cm.exception))

    def test_valid(self):
        info = inspect(make_zip())
        self.assertEqual(info.manifest.slug, "demo_plugin")
        self.assertEqual(len(info.sha256), 64)
        self.assertEqual(len(info.fingerprint), 64)

    def test_fingerprint_matches_plugin_system(self):
        """اثرانگشت مخزن همان اثرانگشتی است که سیستم افزونه‌ها هنگام نصب حساب می‌کند."""
        import tempfile
        from pathlib import Path

        from apps.plugins.integrity import fingerprint
        from apps.pluginhub.packages import extract_to
        data = make_zip(extra={"demo_plugin/views.py": "x = 1\n"})
        with tempfile.TemporaryDirectory() as tmp:
            extract_to(data, Path(tmp))
            self.assertEqual(fingerprint(Path(tmp) / "demo_plugin"), inspect(data).fingerprint)

    def test_not_a_zip_and_empty(self):
        self.bad(b"not a zip", "zip")
        self.bad(b"")

    def test_path_traversal(self):
        self.bad(make_zip(raw_entries=[("demo_plugin/../../evil.py", "x")]))
        self.bad(make_zip(raw_entries=[("/etc/passwd", "x")]))
        self.bad(make_zip(raw_entries=[("demo_plugin\\evil.py", "x")]))

    def test_symlink_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("demo_plugin/plugin.json", "{}")
            info = zipfile.ZipInfo("demo_plugin/link")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            zf.writestr(info, "/etc/passwd")
        self.bad(buf.getvalue(), "symlink")

    def test_missing_manifest_and_bad_manifest(self):
        self.bad(make_zip(skip_manifest=True), "plugin.json")
        self.bad(make_zip(man={"slug": "demo_plugin"}), "نامعتبر")
        self.bad(make_zip(man=manifest(version="1.0")), "version")
        self.bad(make_zip(man=manifest(evil_key=1)), "ناشناخته")

    def test_slug_must_match_root_folder(self):
        self.bad(make_zip(root="other_name"), "slug")

    def test_multiple_roots_and_loose_files(self):
        self.bad(make_zip(extra={"other/readme.txt": "x"}), "ریشه")
        self.bad(make_zip(raw_entries=[("loose.txt", "x")]))

    def test_duplicate_names(self):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.bad(make_zip(raw_entries=[("demo_plugin/__init__.py", "dup")]), "تکراری")

    def test_zip_bomb_and_limits(self):
        big = make_zip(extra={"demo_plugin/big.bin": b"\0" * (9 * 1024 * 1024)})
        with override_settings(RAVIN_REGISTRY_MAX_PACKAGE_BYTES=5 * 1024 * 1024):
            self.assertLess(len(big), 5 * 1024 * 1024)  # فشرده کوچک، بازشده بزرگ
            self.bad(big, "حجم")

    def test_encrypted_flag_rejected(self):
        data = bytearray(make_zip())
        # فلگ رمزگذاری در هدر محلی و مرکزی
        i = data.find(b"PK\x03\x04")
        data[i + 6] |= 1
        j = data.find(b"PK\x01\x02")
        data[j + 8] |= 1
        self.bad(bytes(data))
