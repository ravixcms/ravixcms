"""بستهٔ خراب/مخرب باید پیش از هر تغییری رد شود: zip-slip، symlink، چند ریشه، zip-bomb، کلید خصوصی، فایل محرمانه، خطای نحوی، مسیر غیرمجاز."""
import io
import stat
import tempfile
import zipfile
from pathlib import Path

from django.test import SimpleTestCase

from apps.updater import package
from apps.updater.errors import UpdateError

from .helpers import make_tree


def zipped(files: dict, root="ravin-0.7.0", raw=()):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, content in files.items():
            zf.writestr(f"{root}/{rel}", content)
        for name, content in raw:
            zf.writestr(name, content)
    return buf.getvalue()


def good_files(version="0.7.0", **extra):
    base = Path(tempfile.mkdtemp())
    make_tree(base, version)
    files = {str(p.relative_to(base)): p.read_text() for p in base.rglob("*") if p.is_file()}
    files["release.json"] = '{"schema": 1, "component": "core", "version": "%s", "requires": {}}' % version
    files.update(extra)
    return files


class PackageSafetyTests(SimpleTestCase):
    def extract(self, data):
        d = Path(tempfile.mkdtemp())
        return package.extract(data, d / "x")

    def validate(self, files, version="0.7.0", **kw):
        root = self.extract(zipped(files, **kw))
        return package.validate_core(root, version)

    def test_good_package_passes(self):
        self.assertEqual(self.validate(good_files()).release["version"], "0.7.0")

    def test_not_a_zip(self):
        with self.assertRaises(UpdateError):
            self.extract(b"not a zip")

    def test_zip_slip_rejected(self):
        with self.assertRaisesRegex(UpdateError, "ناامن|نامعتبر"):
            self.extract(zipped({}, raw=[("ravin-0.7.0/../../evil.py", "x=1")]))

    def test_absolute_and_backslash_rejected(self):
        for name in ("/etc/passwd", "ravin-0.7.0\\..\\evil", "C:/x"):
            with self.assertRaises(UpdateError, msg=name):
                self.extract(zipped({}, raw=[(name, "x")]))

    def test_symlink_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            info = zipfile.ZipInfo("ravin-0.7.0/link")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "/etc/passwd")
        with self.assertRaisesRegex(UpdateError, "symlink"):
            self.extract(buf.getvalue())

    def test_multiple_roots_rejected(self):
        with self.assertRaisesRegex(UpdateError, "ریشه"):
            self.extract(zipped(good_files(), raw=[("other/file.py", "x")]))

    def test_zip_bomb_ratio_rejected(self):
        with self.assertRaisesRegex(UpdateError, "فشرده"):
            self.extract(zipped({"static/big.txt": "0" * (30 * 1024 * 1024)}))

    def test_secret_files_rejected(self):
        for name in ("config/.env", "db.sqlite3", "static/server.key", "static/a.pem"):
            with self.assertRaises(UpdateError, msg=name):
                self.validate(good_files(**{name: "x"}))

    def test_private_key_inside_rejected(self):
        with self.assertRaisesRegex(UpdateError, "کلید خصوصی"):
            self.validate(good_files(**{"docs/note.md": "-----BEGIN " + "PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"}))

    def test_syntax_error_rejected_without_executing(self):
        with self.assertRaisesRegex(UpdateError, "نحوی"):
            self.validate(good_files(**{"apps/core/bad.py": "def (:\n"}))
        # کد معتبر «اجرا» نمی‌شود: فقط compile
        marker = Path(tempfile.mkdtemp()) / "ran"
        self.validate(good_files(**{"apps/core/sidefx.py": f"open({str(marker)!r}, 'w').write('x')\n"}))
        self.assertFalse(marker.exists())

    def test_unlisted_top_level_rejected(self):
        for name in ("plugins/evil/x.py", ".env", "media/x.png", "update_backups/x"):
            with self.assertRaises(UpdateError, msg=name):
                self.validate(good_files(**{name: "x"}))

    def test_only_default_theme_allowed(self):
        with self.assertRaisesRegex(UpdateError, "default"):
            self.validate(good_files(**{"themes/custom/theme.json": "{}"}))

    def test_version_mismatch(self):
        with self.assertRaisesRegex(UpdateError, "یکی نیست"):
            self.validate(good_files(), version="0.9.0")
        files = good_files()
        files["apps/core/version.py"] = 'RAVIN_VERSION = "0.1.0"\n'
        with self.assertRaisesRegex(UpdateError, "RAVIN_VERSION"):
            self.validate(files)

    def test_missing_release_or_required(self):
        files = good_files()
        del files["release.json"]
        with self.assertRaisesRegex(UpdateError, "release.json"):
            self.validate(files)
        files = good_files()
        del files["manage.py"]
        with self.assertRaisesRegex(UpdateError, "الزامی"):
            self.validate(files)

    def test_wrong_component(self):
        files = good_files()
        files["release.json"] = '{"schema": 1, "component": "theme", "version": "0.7.0"}'
        with self.assertRaisesRegex(UpdateError, "هسته نیست"):
            self.validate(files)
