"""نصب قالب از ZIP: موفقیت، رد مدارک مخرب، حفظ وضعیت پس از شکست، قفل، ممیزی و سازگاری با بقیهٔ سیستم.

ZIPهای مخرب فقط در حافظه/پوشهٔ موقتِ آزمون ساخته می‌شوند (داده‌های ساختگی)؛ هیچ‌چیز خارج از پوشهٔ موقت نوشته نمی‌شود.
"""
import hashlib
import io
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.audit.models import AuditLog
from apps.themes import installer, package, registry, services
from apps.themes.manifest import inspect_theme_dir
from apps.themes.models import ThemeConfig

BASE_THEMES = Path(settings.BASE_DIR) / "themes"
DEFAULT_SRC = BASE_THEMES / "default"


def default_files(slug="my-theme", edit=None):
    """فایل‌های قالب پیش‌فرض را به‌صورت {مسیر: بایت} با slug جدید برمی‌گرداند (screenshot حذف می‌شود)."""
    files = {}
    for path in sorted(DEFAULT_SRC.rglob("*")):
        if path.is_file():
            rel = path.relative_to(DEFAULT_SRC).as_posix()
            files[rel] = path.read_bytes()
    manifest = json.loads(files["theme.json"])
    manifest.update(slug=slug, name=f"قالب {slug}", version="1.2.3")
    manifest.pop("screenshot", None)
    files["theme.json"] = json.dumps(manifest, ensure_ascii=False).encode()
    files.pop("static/screenshot.png", None)
    if edit:
        edit(files)
    return files


def build_zip(slug="my-theme", edit=None, root=None, files=None):
    files = default_files(slug, edit) if files is None else files
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, content in files.items():
            zf.writestr(f"{root or slug}/{rel}", content)
    return buf.getvalue()


def raw_zip(entries):
    """entries: فهرست (نام، محتوا، external_attr?)؛ برای ساخت ZIPهای مخربِ دقیق."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in entries:
            name, content = item[0], item[1]
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            if len(item) > 2:
                info.external_attr = item[2]
            zf.writestr(info, content)
    return buf.getvalue()


def upload(client, data, name="theme.zip"):
    return client.post(reverse("themes:upload"), {"theme_zip": SimpleUploadedFile(name, data, content_type="application/zip")})


def snapshot(path: Path):
    """اثرانگشت کل درخت فایل‌ها (نام + هش)؛ برای اثبات «هیچ تغییری اعمال نشد»."""
    out = {}
    if not path.exists():
        return out
    for p in sorted(path.rglob("*")):
        rel = p.relative_to(path).as_posix()
        if rel in (".install.lock", ".revision"):
            continue  # فایل‌های زیرساختی قفل/مهر نسخه (عمداً پایدار می‌مانند، محتوای قالب نیستند)
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() and not p.is_symlink() else "dir"
    return out


class UploadCase(TestCase):
    """هر آزمون: پوشهٔ نصب موقت، RAVIX_THEME_WEB_INSTALL روشن و یک مدیرکل واردشده."""

    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="ravix-theme-up-"))
        self.install_dir = self.tmp / "installed"
        self.install_dir.mkdir(mode=0o750)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        ov = override_settings(
            RAVIX_THEME_INSTALL_DIR=self.install_dir,
            RAVIX_THEME_DIRS=[BASE_THEMES, self.install_dir],
            RAVIX_THEME_WEB_INSTALL=True,
        )
        ov.enable()
        self.addCleanup(ov.disable)
        registry.clear_cache()
        self.addCleanup(registry.clear_cache)
        self.sa = make_user("sa", role=Role.SUPER_ADMIN)
        self.client.force_login(self.sa)

    # ---- کمکی‌ها
    def installed_names(self):
        return sorted(p.name for p in self.install_dir.iterdir() if not p.name.startswith("."))

    def leftovers(self):
        return [p.name for p in self.install_dir.iterdir() if p.name.startswith(".staging-")]

    def audit_texts(self):
        return [e.message for e in AuditLog.objects.filter(object_type="ظاهر سایت").order_by("id")]

    def assert_rejected(self, data, *, expect=None, status=400, security=None):
        before = snapshot(self.install_dir)
        cfg_before = ThemeConfig.load().active_theme
        resp = upload(self.client, data)
        self.assertEqual(resp.status_code, status, resp.content.decode()[:300] if status != 400 else "")
        self.assertEqual(snapshot(self.install_dir), before, "پوشهٔ نصب نباید تغییر کند")
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(ThemeConfig.load().active_theme, cfg_before)
        body = resp.content.decode()
        if expect:
            self.assertIn(expect, body)
        self.assertNotIn(str(self.tmp), body)  # مسیر سرور نشت نمی‌کند
        texts = self.audit_texts()
        self.assertTrue(any("نصب قالب ZIP — " + ("رد امنیتی" if security else "رد") in t for t in texts), texts)
        self.assertFalse(any("— موفق:" in t for t in texts), texts)
        return resp


# ====================================================================== نصب موفق
class SuccessTests(UploadCase):
    def test_valid_theme_installs_and_is_listed_but_not_activated(self):
        resp = upload(self.client, build_zip())
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].endswith("?installed=my-theme"))
        info = inspect_theme_dir(self.install_dir / "my-theme")
        self.assertTrue(info.valid, info.errors)
        self.assertEqual(self.installed_names(), ["my-theme"])
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(ThemeConfig.load().active_theme, "default")  # فعال نشد
        self.assertNotIn("my-theme", [c for c in [(ThemeConfig.load().draft or {}).get("theme")]])
        page = self.client.get(resp["Location"])
        self.assertContains(page, "قالب my-theme")
        self.assertContains(page, "نصب شد ولی هنوز فعال نشده")
        self.assertIsNotNone(registry.get_theme("my-theme"))

    def test_audit_start_then_success(self):
        upload(self.client, build_zip())
        texts = self.audit_texts()
        self.assertEqual(len(texts), 2, texts)
        self.assertIn("آغاز", texts[0])
        self.assertIn("موفق", texts[1])
        self.assertIn("my-theme", texts[1])
        last = AuditLog.objects.filter(object_type="ظاهر سایت").latest("id")
        self.assertEqual(last.action, AuditLog.Action.CREATE)
        self.assertEqual(last.actor, self.sa)

    def test_installed_files_have_restricted_permissions(self):
        upload(self.client, build_zip())
        theme = self.install_dir / "my-theme"
        for p in [theme, *theme.rglob("*")]:
            mode = stat.S_IMODE(p.stat().st_mode)
            self.assertEqual(mode, 0o750 if p.is_dir() else 0o640, (p, oct(mode)))

    def test_install_dir_created_when_missing(self):
        shutil.rmtree(self.install_dir)
        resp = upload(self.client, build_zip())
        self.assertEqual(resp.status_code, 302)
        self.assertTrue((self.install_dir / "my-theme").is_dir())

    def test_installed_theme_serves_through_public_pipeline_after_activation(self):
        upload(self.client, build_zip())
        services.activate(mock.Mock(user=self.sa, META={}), "my-theme")
        resp = Client().get("/")
        self.assertEqual(resp.status_code, 200)

    def test_two_themes_can_be_installed_one_after_the_other(self):
        upload(self.client, build_zip("alpha"))
        upload(self.client, build_zip("beta"))
        self.assertEqual(self.installed_names(), ["alpha", "beta"])
        self.assertTrue(registry.get_theme("alpha").valid and registry.get_theme("beta").valid)


# ====================================================================== مدارک نامعتبر
class InvalidInputTests(UploadCase):
    def test_non_zip_file_with_zip_extension(self):
        self.assert_rejected(b"this is plain text, not a zip file at all" * 10, expect="ZIP معتبر نیست")

    def test_empty_file(self):
        resp = self.client.post(reverse("themes:upload"), {"theme_zip": SimpleUploadedFile("a.zip", b"")})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.installed_names(), [])

    def test_missing_file_field(self):
        resp = self.client.post(reverse("themes:upload"), {})
        self.assertEqual(resp.status_code, 400)
        self.assertContains(resp, "یک فایل ZIP انتخاب کنید", status_code=400)

    def test_corrupt_zip_truncated(self):
        data = build_zip()
        self.assert_rejected(data[: len(data) // 2], expect="خراب")

    def test_corrupt_zip_flipped_bytes(self):
        data = bytearray(build_zip())
        for i in range(60, len(data) - 100, 97):
            data[i] ^= 0xFF
        self.assert_rejected(bytes(data))

    def test_empty_zip_archive(self):
        buf = io.BytesIO()
        zipfile.ZipFile(buf, "w").close()
        self.assert_rejected(buf.getvalue())

    def test_missing_theme_json(self):
        self.assert_rejected(build_zip(edit=lambda f: f.pop("theme.json")), expect="theme.json")

    def test_theme_json_slug_mismatch(self):
        def edit(f):
            m = json.loads(f["theme.json"])
            m["slug"] = "other"
            f["theme.json"] = json.dumps(m).encode()

        self.assert_rejected(build_zip(edit=edit), expect="slug")

    def test_missing_required_template(self):
        self.assert_rejected(build_zip(edit=lambda f: f.pop("templates/post_detail.html")), expect="post_detail.html")

    def test_base_template_without_required_tags(self):
        self.assert_rejected(build_zip(edit=lambda f: f.update({"templates/base.html": b"<html>{% block content %}{% endblock %}</html>"})), expect="theme_head")

    def test_forbidden_template_library(self):
        self.assert_rejected(build_zip(edit=lambda f: f.update({"templates/home.html": b"{% load static %}x"})), expect="مجاز نیست")

    def test_theme_json_not_an_object(self):
        self.assert_rejected(build_zip(edit=lambda f: f.update({"theme.json": b"[1,2,3]"})), expect="theme.json")

    def test_files_outside_single_root(self):
        data = raw_zip([("theme.json", b"{}"), ("my-theme/templates/base.html", b"x")])
        self.assert_rejected(data, expect="بیرون از پوشهٔ قالب")

    def test_two_root_folders_including_macosx(self):
        files = default_files("my-theme")
        entries = [(f"my-theme/{k}", v) for k, v in files.items()] + [("other/readme.txt", b"junk")]
        self.assert_rejected(raw_zip(entries), expect="فقط یک پوشهٔ ریشه")
        entries = [(f"my-theme/{k}", v) for k, v in files.items()] + [("__MACOSX/._x", b"junk")]
        self.assert_rejected(raw_zip(entries))

    def test_invalid_root_slug(self):
        self.assert_rejected(build_zip(root="My Theme"), expect="مجاز نیست")
        self.assert_rejected(build_zip(root="UPPER"), expect="شناسهٔ معتبر")

    def test_unexpected_top_level_entry(self):
        self.assert_rejected(build_zip(edit=lambda f: f.update({"extra/readme.txt": b"hi"})), expect="غیرمجاز در ریشه")


# ====================================================================== حملات مسیر و ورودی ویژه
class PathAttackTests(UploadCase):
    def setUp(self):
        super().setUp()
        self.outside = self.tmp / "evil.txt"

    def assert_nothing_escaped(self):
        self.assertFalse(self.outside.exists())
        self.assertFalse((self.tmp / "evil.txt").exists())
        self.assertFalse((self.install_dir.parent / "evil.txt").exists())

    def test_dotdot_path(self):
        entries = [(f"my-theme/{k}", v) for k, v in default_files().items()] + [("my-theme/../evil.txt", b"x")]
        self.assert_rejected(raw_zip(entries), security=True)
        self.assert_nothing_escaped()

    def test_dotdot_at_start(self):
        self.assert_rejected(raw_zip([("../evil.txt", b"x")]), security=True)
        self.assert_nothing_escaped()

    def test_absolute_path(self):
        entries = [(f"my-theme/{k}", v) for k, v in default_files().items()] + [("/var/tmp/ravix-evil-test.txt", b"x")]
        self.assert_rejected(raw_zip(entries), security=True, expect="مسیر مطلق")
        self.assertFalse(Path("/var/tmp/ravix-evil-test.txt").exists())
        self.assert_nothing_escaped()

    def test_windows_drive_and_backslash(self):
        self.assert_rejected(raw_zip([("C:/evil.txt", b"x")]), security=True)
        self.assert_rejected(raw_zip([("my-theme\\..\\evil.txt", b"x")]), security=True)

    def test_symlink_entry(self):
        link = (stat.S_IFLNK | 0o777) << 16
        entries = [(f"my-theme/{k}", v) for k, v in default_files().items()] + [("my-theme/static/link.css", b"/etc/passwd", link)]
        self.assert_rejected(raw_zip(entries), security=True, expect="symlink")

    def test_special_entries_fifo_and_device(self):
        for kind in (stat.S_IFIFO, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFSOCK):
            entries = [(f"my-theme/{k}", v) for k, v in default_files().items()] + [("my-theme/static/dev.css", b"", (kind | 0o644) << 16)]
            self.assert_rejected(raw_zip(entries), security=True)

    def test_duplicate_names(self):
        entries = [(f"my-theme/{k}", v) for k, v in default_files().items()]
        entries.append(("my-theme/theme.json", b"{}"))
        with self.assertWarns(UserWarning):  # zipfile خودش هشدار «Duplicate name» می‌دهد
            data = raw_zip(entries)
        self.assert_rejected(data, security=True, expect="تکراری")

    def test_duplicate_names_differing_in_case(self):
        entries = [(f"my-theme/{k}", v) for k, v in default_files().items()] + [("my-theme/THEME.json", b"{}")]
        self.assert_rejected(raw_zip(entries), security=True, expect="تکراری")

    def test_file_and_directory_conflict(self):
        entries = [(f"my-theme/{k}", v) for k, v in default_files().items()] + [("my-theme/static/x.css", b"a{}"), ("my-theme/static/x.css/y.css", b"b{}")]
        data = raw_zip(entries)
        self.assert_rejected(data, security=True, expect="متعارض")

    def test_hidden_unicode_space_and_deep_names_rejected(self):
        for bad in ("my-theme/static/.htaccess", "my-theme/static/فونت.css", "my-theme/static/a b.css", "my-theme/static/" + "d/" * 9 + "x.css"):
            entries = [(f"my-theme/{k}", v) for k, v in default_files().items()] + [(bad, b"x")]
            self.assert_rejected(raw_zip(entries))

    def test_control_characters_in_name(self):
        entries = [(f"my-theme/{k}", v) for k, v in default_files().items()] + [("my-theme/static/a\x01b.css", b"x")]
        self.assert_rejected(raw_zip(entries), security=True)

    def test_encrypted_flag_rejected(self):
        data = bytearray(build_zip())
        # بیت رمزگذاری را در هدر فایل محلی و مرکزی روشن می‌کنیم
        for sig in (b"PK\x03\x04", b"PK\x01\x02"):
            idx = 0
            while (idx := bytes(data).find(sig, idx)) != -1:
                off = idx + (6 if sig == b"PK\x03\x04" else 8)
                data[off] |= 0x01
                idx += 4
        self.assert_rejected(bytes(data))


# ====================================================================== سقف‌ها و zip-bomb
class LimitTests(UploadCase):
    def test_zip_bomb_by_compression_ratio(self):
        bomb = lambda f: f.update({"static/css/bomb.css": b"A" * (4 * 1024 * 1024)})  # ~4KB فشرده ⇒ نسبت >100
        data = build_zip(edit=bomb)
        self.assertLess(len(data), 1024 * 1024)
        self.assert_rejected(data, security=True, expect="zip-bomb")

    def test_zip_bomb_total_extracted_limit(self):
        data = build_zip(edit=lambda f: f.update({f"static/css/b{i}.css": b"B" * (4 * 1024 * 1024) for i in range(9)}))
        self.assert_rejected(data, security=True)  # ۳۶MB بازشده > ۳۰MB (یا نسبت فشرده‌سازی)

    def test_compressed_size_limit_real_10mb(self):
        junk = os.urandom(10 * 1024 * 1024 + 1024)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("my-theme/static/img.png", junk)
        self.assertGreater(len(buf.getvalue()), installer.MAX_ZIP_BYTES)
        self.assert_rejected(buf.getvalue(), expect="مگابایت")

    def test_single_file_over_5mb(self):
        junk = os.urandom(5 * 1024 * 1024 + 1)
        data = build_zip(edit=lambda f: f.update({"static/img/huge.png": junk}))
        self.assert_rejected(data, expect="مگابایت")

    def test_file_count_limit(self):
        with mock.patch.object(package, "MAX_FILES", 5):
            self.assert_rejected(build_zip(), expect="تعداد فایل")

    def test_file_count_limit_is_400_by_default(self):
        self.assertEqual(package.MAX_FILES, 400)
        data = build_zip(edit=lambda f: f.update({f"static/css/f{i}.css": b"a" for i in range(401)}))
        self.assert_rejected(data, expect="تعداد")

    def test_entries_limit(self):
        with mock.patch.object(package, "MAX_ZIP_ENTRIES", 3):
            self.assert_rejected(build_zip(), expect="تعداد ورودی")

    def test_lying_size_in_zip_header_is_caught_at_extraction(self):
        """اعلام حجم کمتر از واقعیت در ZIP: شمارش واقعی هنگام خواندن باید جلوی نوشتن بیش از سقف را بگیرد."""
        data = build_zip()
        zf = package.open_zip(data)
        pkg = package.validate(zf, len(data))
        entry = next(e for e in pkg.entries if not e.is_dir and e.parts[-1] == "theme.json")
        entry.info.file_size = 3  # دروغ: اعلام ۳ بایت
        dest = Path(tempfile.mkdtemp(dir=self.tmp))
        with self.assertRaises(package.ThemePackageError):
            package.extract(zf, pkg, dest)

    def test_disallowed_extensions(self):
        for bad in ("static/x.py", "static/x.php", "static/x.exe", "static/x.sh", "static/x.php.png", "templates/x.pyc", "static/x.htaccess", "static/x.dll"):
            self.assert_rejected(build_zip(edit=lambda f, b=bad: f.update({b: b"x"})), security=True)

    def test_unknown_extension_and_no_extension(self):
        self.assert_rejected(build_zip(edit=lambda f: f.update({"static/data.bin": b"x"})), expect="مجاز نیست")
        self.assert_rejected(build_zip(edit=lambda f: f.update({"static/NOEXT": b"x"})), expect="بدون پسوند")

    def test_svg_rejected_with_clear_message(self):
        self.assert_rejected(build_zip(edit=lambda f: f.update({"static/logo.svg": b"<svg xmlns='http://www.w3.org/2000/svg'/>"})), expect="SVG")

    def test_html_in_static_rejected_by_existing_validator(self):
        self.assert_rejected(build_zip(edit=lambda f: f.update({"static/page.html": b"<b>x</b>"})), expect="static")

    def test_non_html_in_templates_rejected_by_existing_validator(self):
        self.assert_rejected(build_zip(edit=lambda f: f.update({"templates/x.css": b"a{}"})), expect="templates")

    def test_png_with_php_tail_is_just_data_but_py_never_runs(self):
        """فایل‌ها هرگز import/اجرا نمی‌شوند؛ محتوای شبیه کد در فایل مجاز فقط داده است."""
        data = build_zip(edit=lambda f: f.update({"static/img/a.png": b"\x89PNG\r\n<?php echo 1; ?>"}))
        self.assertEqual(upload(self.client, data).status_code, 302)


# ====================================================================== slug تکراری و جایگزینی
class ConflictTests(UploadCase):
    def test_duplicate_slug_in_install_dir_not_replaced(self):
        upload(self.client, build_zip("same", edit=lambda f: f.update({"static/marker.txt": b"ORIGINAL"})))
        before = snapshot(self.install_dir)
        resp = upload(self.client, build_zip("same", edit=lambda f: f.update({"static/marker.txt": b"NEW"})))
        self.assertEqual(resp.status_code, 400)
        self.assertContains(resp, "از قبل وجود دارد", status_code=400)
        self.assertEqual(snapshot(self.install_dir), before)
        self.assertEqual((self.install_dir / "same/static/marker.txt").read_bytes(), b"ORIGINAL")

    def test_existing_manual_theme_in_other_dir_not_replaced(self):
        extra = self.tmp / "extra"
        (extra / "manual").mkdir(parents=True)
        (extra / "manual" / "keep.txt").write_text("mine")
        with override_settings(RAVIX_THEME_DIRS=[BASE_THEMES, extra, self.install_dir]):
            registry.clear_cache()
            resp = upload(self.client, build_zip("manual"))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual((extra / "manual" / "keep.txt").read_text(), "mine")
        self.assertEqual(self.installed_names(), [])

    def test_default_theme_never_replaced(self):
        before = snapshot(DEFAULT_SRC)
        resp = upload(self.client, build_zip("default"))
        self.assertEqual(resp.status_code, 400)
        self.assertContains(resp, "default", status_code=400)
        self.assertEqual(snapshot(DEFAULT_SRC), before)
        self.assertEqual(self.installed_names(), [])

    def test_existing_invalid_theme_dir_is_also_protected(self):
        (self.install_dir / "broken").mkdir()
        (self.install_dir / "broken" / "x.txt").write_text("data")
        resp = upload(self.client, build_zip("broken"))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual((self.install_dir / "broken" / "x.txt").read_text(), "data")

    def test_symlink_with_same_name_is_not_followed_or_replaced(self):
        target = self.tmp / "elsewhere"
        target.mkdir()
        (self.install_dir / "linked").symlink_to(target)
        resp = upload(self.client, build_zip("linked"))
        self.assertEqual(resp.status_code, 400)
        self.assertTrue((self.install_dir / "linked").is_symlink())
        self.assertEqual(list(target.iterdir()), [])


# ====================================================================== مجوز، CSRF، پرچم
class AccessTests(UploadCase):
    def test_non_super_admin_roles_forbidden_and_nothing_written(self):
        for role in (Role.USER, Role.AUTHOR, Role.ADMIN):
            c = Client()
            c.force_login(make_user(f"u_{role}", role=role))
            before = snapshot(self.install_dir)
            resp = upload(c, build_zip())
            self.assertEqual(resp.status_code, 403, role)
            self.assertEqual(snapshot(self.install_dir), before)
        self.assertTrue(any("بدون مجوز" in t for t in self.audit_texts()))
        self.assertFalse(any("— موفق:" in t for t in self.audit_texts()))

    def test_anonymous_redirected_to_login(self):
        c = Client()
        resp = upload(c, build_zip())
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp["Location"])
        self.assertEqual(self.installed_names(), [])

    def test_themes_manage_does_not_imply_install(self):
        admin = make_user("adm", role=Role.ADMIN)
        self.assertTrue(admin.has_cap("themes.manage"))
        self.assertFalse(admin.has_cap("themes.install"))
        self.assertTrue(self.sa.has_cap("themes.install"))

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(reverse("themes:upload")).status_code, 405)

    def test_csrf_enforced(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.sa)
        resp = upload(c, build_zip())
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.installed_names(), [])

    def test_web_install_flag_off_rejects_without_touching_files(self):
        with override_settings(RAVIX_THEME_WEB_INSTALL=False):
            before = snapshot(self.install_dir)
            resp = upload(self.client, build_zip())
            self.assertEqual(resp.status_code, 400)
            self.assertContains(resp, "خاموش", status_code=400)
            self.assertEqual(snapshot(self.install_dir), before)
            self.assertFalse((self.install_dir / ".install.lock").exists())
            self.assertEqual(self.installed_names(), [])

    def test_flag_default_is_off(self):
        text = (Path(settings.BASE_DIR) / "config/settings/base.py").read_text(encoding="utf-8")
        self.assertIn('env_bool("RAVIX_THEME_WEB_INSTALL", False)', text)


# ====================================================================== قفل هم‌زمانی
class LockTests(UploadCase):
    def test_second_request_rejected_while_lock_held_and_lock_released_after(self):
        fd = os.open(self.install_dir / ".install.lock", os.O_RDWR | os.O_CREAT, 0o600)
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            self.assert_rejected(build_zip(), expect="نصب دیگری")
        finally:
            os.close(fd)
        self.assertEqual(upload(self.client, build_zip()).status_code, 302)  # قفل آزاد است

    def test_lock_released_after_validation_failure(self):
        upload(self.client, build_zip(edit=lambda f: f.pop("theme.json")))
        self.assertEqual(upload(self.client, build_zip()).status_code, 302)

    def test_lock_and_staging_released_after_unexpected_crash(self):
        with mock.patch.object(package, "extract", side_effect=RuntimeError("boom /srv/secret/path")):
            resp = upload(self.client, build_zip())
        self.assertEqual(resp.status_code, 400)
        self.assertNotIn("/srv/secret/path", resp.content.decode())
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(self.installed_names(), [])
        self.assertTrue(any("خطای داخلی" in t for t in self.audit_texts()))
        self.assertFalse(any("— موفق:" in t for t in self.audit_texts()))
        self.assertEqual(upload(self.client, build_zip()).status_code, 302)

    def test_killed_process_releases_lock(self):
        code = (
            "import fcntl,os,sys,time;fd=os.open(sys.argv[1],os.O_RDWR|os.O_CREAT,0o600);"
            "fcntl.flock(fd,fcntl.LOCK_EX);print('locked',flush=True);time.sleep(60)"
        )
        proc = subprocess.Popen([sys.executable, "-I", "-c", code, str(self.install_dir / ".install.lock")], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "locked")
            self.assert_rejected(build_zip(), expect="نصب دیگری")
            os.kill(proc.pid, signal.SIGKILL)  # توقف ناگهانی فرایند
            proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
        self.assertEqual(upload(self.client, build_zip()).status_code, 302)

    def test_two_real_concurrent_installs_only_one_wins(self):
        data = build_zip("race")
        results = {}
        started = threading.Event()
        real_extract = package.extract

        def slow_extract(*a, **kw):
            started.set()
            time.sleep(0.6)
            return real_extract(*a, **kw)

        def run(key):
            try:
                results[key] = installer._install(data)
            except Exception as exc:  # noqa: BLE001
                results[key] = exc

        with mock.patch("apps.updater.pipeline.core_update_active", return_value=False), mock.patch.object(package, "extract", slow_extract):
            t1 = threading.Thread(target=run, args=("a",))
            t1.start()
            self.assertTrue(started.wait(5))
            t2 = threading.Thread(target=run, args=("b",))
            t2.start()
            t1.join(10)
            t2.join(10)
        wins = [k for k, v in results.items() if isinstance(v, installer.InstallResult)]
        fails = [v for v in results.values() if isinstance(v, installer.ThemeInstallError)]
        self.assertEqual(len(wins), 1, results)
        self.assertEqual(len(fails), 1, results)
        self.assertIn("نصب دیگری", fails[0].message)
        self.assertEqual(self.installed_names(), ["race"])
        self.assertEqual(self.leftovers(), [])
        self.assertTrue(inspect_theme_dir(self.install_dir / "race").valid)

    def test_unsupported_lock_fs_fails_closed_without_fallback(self):
        import errno

        with mock.patch("fcntl.flock", side_effect=OSError(errno.ENOLCK, "no locks")):
            self.assert_rejected(build_zip(), expect="قفل فایل‌سیستمی")
        self.assertEqual(self.installed_names(), [])

    def test_network_filesystem_refused(self):
        with mock.patch.object(installer, "_mount_fstype", return_value="nfs4"):
            self.assert_rejected(build_zip(), expect="قفل قابل‌اعتماد")

    def test_blocked_while_core_update_active(self):
        with mock.patch("apps.updater.pipeline.core_update_active", return_value=True):
            self.assert_rejected(build_zip(), expect="ارتقای هسته")

    def test_unwritable_install_dir_gives_clear_error(self):
        with mock.patch.object(installer.os, "access", return_value=False):
            self.assert_rejected(build_zip(), expect="قابل‌نوشتن نیست")

    def test_rename_failure_leaves_no_partial_install(self):
        with mock.patch.object(installer.os, "rename", side_effect=OSError(18, "Invalid cross-device link")):
            self.assert_rejected(build_zip(), expect="هیچ تغییری")
        self.assertEqual(self.installed_names(), [])

    def test_final_validation_failure_removes_new_dir(self):
        real = installer.inspect_theme_dir
        calls = {"n": 0}

        def flaky(path):
            calls["n"] += 1
            info = real(path)
            if calls["n"] == 2:  # اعتبارسنجی نهایی پس از rename
                return type(info)(slug=info.slug, path=info.path, errors=("خطای ساختگی",))
            return info

        with mock.patch.object(installer, "inspect_theme_dir", flaky):
            self.assert_rejected(build_zip(), expect="اعتبارسنجی نهایی")
        self.assertEqual(self.installed_names(), [])

    def test_stale_staging_dirs_are_cleaned_but_other_files_untouched(self):
        old = self.install_dir / ".staging-old"
        old.mkdir()
        (old / "x").write_text("junk")
        os.utime(old, (time.time() - 7200, time.time() - 7200))
        fresh = self.install_dir / ".staging-fresh"
        fresh.mkdir()
        other = self.install_dir / "not-ours"
        other.mkdir()
        (other / "keep.txt").write_text("keep")
        self.assertEqual(upload(self.client, build_zip()).status_code, 302)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())
        self.assertEqual((other / "keep.txt").read_text(), "keep")


# ====================================================================== ممیزی
class AuditTests(UploadCase):
    def test_rejection_is_not_logged_as_success(self):
        upload(self.client, b"not a zip")
        events = list(AuditLog.objects.filter(object_type="ظاهر سایت").order_by("id"))
        self.assertEqual(len(events), 2)
        self.assertIn("آغاز", events[0].message)
        self.assertIn("رد شد", events[1].message)
        self.assertEqual(events[1].action, AuditLog.Action.UPDATE)

    def test_security_rejection_marked(self):
        upload(self.client, raw_zip([("../evil.txt", b"x")]))
        self.assertTrue(any("رد امنیتی" in t for t in self.audit_texts()))

    def test_no_filename_or_secret_in_audit(self):
        upload(self.client, build_zip(), name="evil\nname<script>.zip")
        joined = " ".join(self.audit_texts())
        self.assertNotIn("evil", joined)
        self.assertNotIn("<script>", joined)

    def test_audit_has_actor_and_type(self):
        upload(self.client, build_zip())
        e = AuditLog.objects.filter(object_type="ظاهر سایت").first()
        self.assertEqual(e.actor, self.sa)
        self.assertEqual(AuditLog.objects.filter(action="create").count(), 1)


# ====================================================================== پنل و سازگاری
class PanelTests(UploadCase):
    def test_super_admin_sees_upload_card_with_limits(self):
        page = self.client.get(reverse("themes:list"))
        self.assertContains(page, "بارگذاری قالب (ZIP)")
        self.assertContains(page, "10 مگابایت")
        self.assertContains(page, 'enctype="multipart/form-data"')
        self.assertContains(page, "csrfmiddlewaretoken")
        self.assertContains(page, "SVG")

    def test_admin_does_not_see_upload_card(self):
        c = Client()
        c.force_login(make_user("adm", role=Role.ADMIN))
        page = c.get(reverse("themes:list"))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "بارگذاری قالب (ZIP)")

    def test_flag_off_shows_explanation_instead_of_form(self):
        with override_settings(RAVIX_THEME_WEB_INSTALL=False):
            page = self.client.get(reverse("themes:list"))
        self.assertContains(page, "خاموش")
        self.assertNotContains(page, 'name="theme_zip"')

    def test_error_page_shows_clear_message_and_details(self):
        resp = upload(self.client, build_zip(edit=lambda f: f.pop("templates/home.html")))
        self.assertContains(resp, "نصب قالب انجام نشد", status_code=400)
        self.assertContains(resp, "home.html", status_code=400)

    def test_static_status_explained_after_install(self):
        resp = upload(self.client, build_zip())
        page = self.client.get(resp["Location"])
        self.assertContains(page, "collectstatic")
        self.assertContains(page, "وضعیت فایل‌های استاتیک")

    def test_static_pending_warning_flips_after_collectstatic(self):
        static_root = self.tmp / "staticfiles"
        static_root.mkdir()
        with override_settings(STATIC_ROOT=static_root, DEBUG=False):
            upload(self.client, build_zip())
            page = self.client.get(reverse("themes:list"))
            self.assertContains(page, "هنوز منتشر نشده‌اند")
            # شبیه‌سازی خروجی collectstatic برای هر دو قالب (پیش‌فرض و نصب‌شده)
            (static_root / "themes" / "my-theme").mkdir(parents=True)
            (static_root / "themes" / "default").mkdir(parents=True)
            page = self.client.get(reverse("themes:list"))
            self.assertNotContains(page, "هنوز منتشر نشده‌اند")

    def test_check_themes_command_sees_installed_theme(self):
        from django.core.management import call_command

        upload(self.client, build_zip())
        out = io.StringIO()
        call_command("check_themes", "my-theme", stdout=out)
        self.assertIn("my-theme", out.getvalue())

    def test_preview_activate_deactivate_customize_work_with_installed_theme(self):
        upload(self.client, build_zip())
        self.assertEqual(self.client.post(reverse("themes:preview_theme", args=["my-theme"])).status_code, 302)
        self.assertEqual(ThemeConfig.load().draft["theme"], "my-theme")
        self.client.post(reverse("themes:preview_stop"))
        self.client.post(reverse("themes:discard"))
        self.assertEqual(self.client.post(reverse("themes:activate", args=["my-theme"])).status_code, 302)
        self.assertEqual(ThemeConfig.load().active_theme, "my-theme")
        for name in ("themes:customize", "themes:layout", "themes:menus", "themes:home", "themes:list"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(Client().get("/").status_code, 200)
        self.client.post(reverse("themes:deactivate", args=["my-theme"]))
        self.assertEqual(ThemeConfig.load().active_theme, "default")

    def test_earlier_themes_not_broken(self):
        manual = self.tmp / "manual-dir"
        shutil.copytree(DEFAULT_SRC, manual / "older")
        m = json.loads((manual / "older/theme.json").read_text())
        m.update(slug="older", name="Older")
        (manual / "older/theme.json").write_text(json.dumps(m))
        with override_settings(RAVIX_THEME_DIRS=[BASE_THEMES, manual, self.install_dir]):
            registry.clear_cache()
            upload(self.client, build_zip("newer"))
            themes = registry.get_themes(refresh=True)
            self.assertTrue(all(themes[s].valid for s in ("default", "older", "newer")))
            self.client.post(reverse("themes:activate", args=["older"]))
            self.assertEqual(Client().get("/").status_code, 200)
            self.assertTrue(inspect_theme_dir(DEFAULT_SRC).valid)

    def test_registry_revision_stamp_refreshes_other_workers(self):
        registry.get_themes(refresh=True)
        self.assertIsNone(registry.get_theme("late"))
        # «worker دیگر»: پوشه را بدون clear_cache می‌سازیم و فقط مهر نسخه را به‌روز می‌کنیم
        shutil.copytree(DEFAULT_SRC, self.install_dir / "late")
        m = json.loads((self.install_dir / "late/theme.json").read_text())
        m.update(slug="late", name="Late")
        (self.install_dir / "late/theme.json").write_text(json.dumps(m))
        self.assertIsNone(registry.get_theme("late"))  # کش قدیمی
        time.sleep(0.01)
        (self.install_dir / ".revision").write_text("x")
        self.assertIsNotNone(registry.get_theme("late"))

    def test_url_and_capability_registered(self):
        self.assertEqual(reverse("themes:upload"), "/panel/themes/upload/")
        from apps.accounts.permissions import CAPABILITIES, ROLE_CAPABILITIES

        self.assertIn("themes.install", CAPABILITIES)
        holders = [r for r, caps in ROLE_CAPABILITIES.items() if "themes.install" in caps]
        self.assertEqual(holders, [Role.SUPER_ADMIN])


# ====================================================================== سازگاری با updater امضاشده
class UpdaterCompatibilityTests(UploadCase):
    def test_updater_validator_accepts_installed_theme(self):
        from apps.updater import package as upd_package

        upload(self.client, build_zip())
        ex = upd_package.validate_theme(self.install_dir / "my-theme", "my-theme", "1.2.3")
        self.assertEqual(ex.release["component"], "theme")

    def test_updater_lists_installed_theme_in_versions(self):
        from apps.updater import versions

        upload(self.client, build_zip())
        self.assertIn("my-theme", [t["slug"] for t in versions.installed_themes()])

    def test_updater_still_refuses_to_install_new_themes_and_default(self):
        from apps.updater import pipeline
        from apps.updater.errors import UpdateBlocked

        with self.assertRaises(UpdateBlocked):
            pipeline.apply_theme("default", None, self.sa)

    def test_signed_upgrade_of_zip_installed_theme_still_works(self):
        from apps.updater import pipeline, release
        from apps.updater.tests.helpers import SEED, UpdaterCase

        # از زیرساخت آزمون updater استفاده می‌کنیم: همان fixture، فقط قالب از مسیر ZIP نصب شده است.
        case = UpdaterCase("setUp")
        case.setUp()
        self.addCleanup(case.doCleanups)
        zip_data = build_zip("mytheme", edit=lambda f: f.update({"static/marker.txt": b"0.1.0"}))
        # نسخهٔ نصب‌شده باید 0.1.0 باشد
        files = default_files("mytheme", lambda f: f.update({"static/marker.txt": b"0.1.0"}))
        m = json.loads(files["theme.json"])
        m["version"] = "0.1.0"
        files["theme.json"] = json.dumps(m).encode()
        self.assertEqual(upload(self.client, build_zip("mytheme", files=files)).status_code, 302)
        src = case.base / "src-0.2.0" / "mytheme"
        shutil.copytree(self.install_dir / "mytheme", src)
        m["version"] = "0.2.0"
        (src / "theme.json").write_text(json.dumps(m))
        (src / "static/marker.txt").write_text("0.2.0")
        slug, entry = release.build_theme(src, case.dist, seed=SEED)
        release.write_index(case.dist, themes={slug: [entry]})
        run = pipeline.apply_theme("mytheme", None, case.superadmin)
        self.assertEqual(run.status, "succeeded")
        self.assertEqual((self.install_dir / "mytheme/static/marker.txt").read_text(), "0.2.0")
        self.assertEqual(self.leftovers(), [])
        del zip_data
