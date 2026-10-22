"""آزمون نصب قالب ZIP روی میزبان واقعی (بدون مرورگر؛ درخواست‌ها درون‌پردازه‌ای با Django test Client).

اجرا (روی سرور، با کاربر سرویس و محیط واقعی؛ دستور کامل در docs/themes.md):
    sudo -u ravin bash -c 'set -a; . /etc/ravin/ravin.env; set +a; export RAVIN_THEME_INSTALL_DIR=/srv/ravin/themes_installed; \
        cd /srv/ravin/app && /srv/ravin/venv/bin/python scripts/vps_theme_install_check.py'

⚠ فقط روی سرور آزمایشی. این اسکریپت:
  - کاربران موقت `vpscheck_*` می‌سازد و در پایان حذف می‌کند؛
  - قالب‌های آزمایشی `vpscheck-*` را در RAVIN_THEME_INSTALL_DIR نصب و در پایان پاک می‌کند (فقط همین‌ها)؛
  - ZIPهای مخرب فقط در حافظه ساخته می‌شوند (داده‌های ساختگی)؛ چیزی خارج از پوشهٔ نصب نوشته نمی‌شود؛
  - collectstatic را اجرا نمی‌کند؛ قالب‌ها را فعال نمی‌کند.
خروجی: PASS/FAIL برای هر بررسی؛ کد خروج ۰ فقط اگر همه PASS باشند. اطلاعات محرمانه چاپ نمی‌شود.
"""
import io
import json
import os
import shutil
import stat
import sys
import threading
import zipfile
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.db import connection  # noqa: E402
from django.test import Client, override_settings  # noqa: E402
from django.urls import reverse  # noqa: E402

from apps.accounts.models import User  # noqa: E402
from apps.accounts.permissions import Role  # noqa: E402
from apps.audit.models import AuditLog  # noqa: E402
from apps.themes import installer, registry  # noqa: E402

ROOT = Path(settings.RAVIN_THEME_INSTALL_DIR)
DEFAULT_SRC = Path(settings.BASE_DIR) / "themes" / "default"
EVIL = Path("/tmp/ravin-evil-vps-check.txt")
RESULTS = []
CREATED = []


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""))


def files_for(slug):
    files = {}
    for p in sorted(DEFAULT_SRC.rglob("*")):
        if p.is_file():
            files[p.relative_to(DEFAULT_SRC).as_posix()] = p.read_bytes()
    m = json.loads(files["theme.json"])
    m.update(slug=slug, name=f"آزمون {slug}", version="1.0.0")
    m.pop("screenshot", None)
    files["theme.json"] = json.dumps(m, ensure_ascii=False).encode()
    files.pop("static/screenshot.png", None)
    return files


def zip_bytes(slug, files=None, root=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, data in (files or files_for(slug)).items():
            zf.writestr(f"{root or slug}/{rel}", data)
    return buf.getvalue()


def raw_zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in entries:
            info = zipfile.ZipInfo(item[0])
            info.compress_type = zipfile.ZIP_DEFLATED
            if len(item) > 2:
                info.external_attr = item[2]
            zf.writestr(info, item[1])
    return buf.getvalue()


def client_for(user):
    c = Client(HTTP_HOST="localhost")
    c.force_login(user)
    return c


def post(c, data, name="t.zip"):
    return c.post(reverse("themes:upload"), {"theme_zip": SimpleUploadedFile(name, data, content_type="application/zip")}, secure=True)


def tree(path):
    return sorted(p.relative_to(path).as_posix() for p in path.rglob("*") if p.name not in (".install.lock", ".revision")) if path.exists() else []


def main():
    print(f"پوشهٔ نصب: {ROOT} | کاربر OS: {os.getuid()} | DB: {connection.vendor}")
    if not ROOT.exists():
        print(f"خطا: {ROOT} وجود ندارد؛ ابتدا بسازید (install -d -m 0750 -o ravin -g ravin ...)")
        return 2
    sa = User.objects.create_user(username="vpscheck_super", email="vpscheck_super@example.invalid", password=os.urandom(12).hex() + "Aa1!", role=Role.SUPER_ADMIN)
    ad = User.objects.create_user(username="vpscheck_admin", email="vpscheck_admin@example.invalid", password=os.urandom(12).hex() + "Aa1!", role=Role.ADMIN)
    users = [sa, ad]
    try:
        run(sa, ad)
    finally:
        for slug in CREATED:
            shutil.rmtree(ROOT / slug, ignore_errors=True)
        for u in users:
            u.delete()
        registry.clear_cache()
        EVIL.unlink(missing_ok=True)
    ok = all(RESULTS)
    print(f"\nنتیجه: {sum(RESULTS)}/{len(RESULTS)} PASS" + ("" if ok else " — ناموفق دارد"))
    return 0 if ok else 1


def run(sa, ad):
    csa, cad = client_for(sa), client_for(ad)
    baseline = tree(ROOT)

    # ۱) خاموش بودن: رد شود و چیزی تغییر نکند
    with override_settings(RAVIN_THEME_WEB_INSTALL=False):
        r = post(csa, zip_bytes("vpscheck-off"))
    check("پرچم خاموش: درخواست رد می‌شود", r.status_code in (400, 403), r.status_code)
    check("پرچم خاموش: هیچ فایلی تغییر نکرد", tree(ROOT) == baseline)

    with override_settings(RAVIN_THEME_WEB_INSTALL=True):
        # ۲) مجوز: ادمین معمولی نباید بتواند
        r = post(cad, zip_bytes("vpscheck-adm"))
        check("ادمین بدون themes.install: ۴۰۳", r.status_code == 403, r.status_code)
        check("ادمین بدون مجوز: هیچ فایلی نوشته نشد", tree(ROOT) == baseline)

        # ۳) نصب موفق
        slug = "vpscheck-ok"
        r = post(csa, zip_bytes(slug))
        CREATED.append(slug)
        check("نصب معتبر: تغییر مسیر (۳۰۲)", r.status_code == 302, r.status_code)
        d = ROOT / slug
        check("نصب معتبر: پوشهٔ قالب ساخته شد", (d / "theme.json").is_file())
        check("نصب معتبر: مالک پوشه همان کاربر سرویس است", d.exists() and d.stat().st_uid == os.getuid())
        modes = {stat.S_IMODE(p.stat().st_mode) for p in d.rglob("*") if p.is_file()} if d.exists() else set()
        dmodes = {stat.S_IMODE(p.stat().st_mode) for p in d.rglob("*") if p.is_dir()} | ({stat.S_IMODE(d.stat().st_mode)} if d.exists() else set())
        check("مجوز فایل‌ها ۰۶۴۰ و پوشه‌ها ۰۷۵۰", modes == {0o640} and dmodes == {0o750}, f"{[oct(m) for m in modes]} {[oct(m) for m in dmodes]}")
        check("رجیستری قالب را می‌بیند", slug in registry.get_themes(refresh=True))
        from apps.themes.models import ThemeConfig
        check("قالب خودکار فعال نشد", ThemeConfig.objects.first() is None or ThemeConfig.objects.first().active_theme != slug)
        msgs = list(AuditLog.objects.filter(object_type=installer.AUDIT_TYPE, message__startswith="نصب قالب ZIP").order_by("-id").values_list("action", "message")[:40])
        check("ممیزی: رویداد موفق با Action=create و شناسهٔ قالب", any(a == "create" and "موفق" in m and slug in m for a, m in msgs), msgs[:3])
        check("ممیزی: هیچ رد/آغاز با Action=create ثبت نشده", not any(a == "create" and ("رد" in m or "آغاز" in m) for a, m in msgs))

        # ۴) تکراری
        before = tree(ROOT)
        r = post(csa, zip_bytes(slug))
        check("نصب تکراری رد می‌شود (۴۰۰)", r.status_code == 400, r.status_code)
        check("نصب تکراری چیزی را تغییر نداد", tree(ROOT) == before)
        r = post(csa, zip_bytes("default"))
        check("slug «default» رد می‌شود", r.status_code == 400, r.status_code)

        # ۵) مخرب (در حافظه)
        base = files_for("vpscheck-bad")

        def with_extra(extra):
            f = dict(base)
            f.update(extra)
            return f

        EVIL.unlink(missing_ok=True)
        attacks = {
            "zip-slip (..)": raw_zip([("vpscheck-bad/theme.json", base["theme.json"]), ("vpscheck-bad/../../tmp/ravin-evil-vps-check.txt", b"x")]),
            "مسیر مطلق": raw_zip([("/tmp/ravin-evil-vps-check.txt", b"x")]),
            "فایل SVG": zip_bytes("vpscheck-bad", with_extra({"static/a.svg": b"<svg xmlns='http://www.w3.org/2000/svg'/>"})),
            "فایل php": zip_bytes("vpscheck-bad", with_extra({"static/a.php": b"<?php echo 1;"})),
            "فایل py": zip_bytes("vpscheck-bad", with_extra({"static/a.py": b"print(1)"})),
            "x.php.png": zip_bytes("vpscheck-bad", with_extra({"static/x.php.png": b"x"})),
            "symlink": raw_zip([("vpscheck-bad/theme.json", base["theme.json"]), ("vpscheck-bad/static/l", "/etc/passwd", (stat.S_IFLNK | 0o777) << 16)]),
            "ZIP bomb (نسبت فشرده‌سازی)": zip_bytes("vpscheck-bad", with_extra({"static/z.css": b"\0" * (4 * 1024 * 1024)})),
            "بیش از ۴۰۰ فایل": zip_bytes("vpscheck-bad", with_extra({f"static/f{i}.css": b"a{}" for i in range(405)})),
            "دو پوشهٔ ریشه": raw_zip([("vpscheck-bad/theme.json", base["theme.json"]), ("other/x.css", b"a{}")]),
            "فایل ZIP نیست": b"this is not a zip file",
            "ZIP خراب": zip_bytes("vpscheck-bad")[:200],
            "بزرگ‌تر از ۱۰MB": b"PK\x03\x04" + os.urandom(11 * 1024 * 1024),
        }
        for label, blob in attacks.items():
            before = tree(ROOT)
            r = post(csa, blob)
            check(f"مخرب رد شد: {label}", r.status_code == 400, r.status_code)
            check(f"مخرب بدون اثر: {label}", tree(ROOT) == before)
        check("هیچ فایلی خارج از پوشهٔ نصب نوشته نشد", not EVIL.exists())
        check("هیچ پوشهٔ staging باقی نمانده", not [p for p in ROOT.iterdir() if p.name.startswith(".staging-")])
        msgs = list(AuditLog.objects.filter(object_type=installer.AUDIT_TYPE, message__startswith="نصب قالب ZIP").order_by("-id").values_list("action", "message")[:120])
        check("ممیزی: رد امنیتی/رد شد ثبت شده", any("رد امنیتی" in m or "رد شد" in m for a, m in msgs))
        check("ممیزی: هیچ رد یا خطایی با Action=create (موفقیت) ثبت نشده", not any(a == "create" and "— موفق:" not in m for a, m in msgs))

        # ۶) هم‌زمانی روی فایل‌سیستم واقعی: دو درخواست با یک slug → دقیقاً یکی موفق
        slug2 = "vpscheck-race"
        CREATED.append(slug2)
        blob = zip_bytes(slug2)
        codes = []

        def worker():
            try:
                c = client_for(User.objects.get(username="vpscheck_super"))
                codes.append(post(c, blob).status_code)
            finally:
                connection.close()

        ts = [threading.Thread(target=worker) for _ in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        check("دو نصب هم‌زمان با یک slug: یکی ۳۰۲ و یکی ۴۰۰", sorted(codes) == [302, 400], codes)
        check("پس از رقابت قفل آزاد است (نصب بعدی ممکن است)", post(csa, zip_bytes("vpscheck-after")).status_code == 302)
        CREATED.append("vpscheck-after")

    # ۷) نوع فایل‌سیستم
    fs = installer._mount_fstype(ROOT) or "نامشخص"
    print(f"INFO  نوع فایل‌سیستم پوشهٔ نصب: {fs}")
    check("فایل‌سیستم قفل‌پذیر محلی است (نه NFS/CIFS)", fs not in ("nfs", "nfs4", "cifs", "smb3", "smbfs", "afs", "9p", "fuse.sshfs"), fs)


if __name__ == "__main__":
    sys.exit(main())
