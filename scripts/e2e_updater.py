#!/usr/bin/env python
"""
آزمون سرتاسری واقعی به‌روزرسان (بدون mock): یک نمونهٔ کامل از پروژه را در پوشهٔ موقت می‌سازد، بسته‌های امضاشدهٔ 0.7.0 و 0.8.0 (با مهاجرت واقعی دیتابیس
و یک مهاجرت عمداً خراب) را می‌سازد و با پروسه‌های واقعی ``manage.py`` ارتقا، شکست میانه + بازگشت خودکار و بازگشت دستی را اجرا می‌کند.

اجرا (از ریشهٔ پروژه):  python scripts/e2e_updater.py
هیچ کلید واقعی استفاده نمی‌شود؛ کلید امضا برای همین اجرا ساخته و با پوشهٔ موقت حذف می‌شود.
"""
import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
IGNORE = shutil.ignore_patterns(".venv", "venv", "__pycache__", "*.pyc", "db.sqlite3*", "media", "staticfiles", "update_work", "update_backups", "shop_private",
                                "plugins_installed", "plugin_backups", ".env", ".git", "*.sqlite3")
FAILED = []


def check(cond, msg):
    print(("  ✔ " if cond else "  ✘ ") + msg)
    if not cond:
        FAILED.append(msg)


def run(args, cwd, env, expect_ok=True):
    r = subprocess.run([PY, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=900)
    if expect_ok and r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-2000:])
        raise SystemExit(f"دستور ناموفق: {args}")
    return r


def tree_hash(root: Path, entries):
    h = hashlib.sha256()
    for e in entries:
        p = root / e
        files = [p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file() and "__pycache__" not in x.parts and x.suffix != ".pyc")
        for f in files:
            h.update(f.relative_to(root).as_posix().encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def set_version(tree: Path, version: str):
    f = tree / "apps/core/version.py"
    f.write_text(re.sub(r'RAVIX_VERSION = "[^"]+"', f'RAVIX_VERSION = "{version}"', f.read_text()), encoding="utf-8")


PG = {}  # پر می‌شود اگر E2E_PG_URL تنظیم شده باشد: اجرای همین سناریو روی PostgreSQL واقعی


def query(db, sql_sqlite, sql_pg=None):
    if PG:
        r = subprocess.run(["psql", "-At", "-F", "|", "-c", sql_pg or sql_sqlite], env=PG["env"], capture_output=True, text=True, check=True)
        return [tuple(line.split("|")) for line in r.stdout.splitlines()]
    con = sqlite3.connect(db)
    try:
        return list(con.execute(sql_sqlite))
    finally:
        con.close()


def columns(db: Path, table="updater_updaterun"):
    return {r[1] if not PG else r[0] for r in query(db, f"pragma table_info({table})", f"select column_name from information_schema.columns where table_name='{table}'")}


def applied(db: Path):
    return {r[0] for r in query(db, "select name from django_migrations where app='updater'")}


def main():
    tmp = Path(tempfile.mkdtemp(prefix="ravix-e2e-upd-"))
    try:
        site = tmp / "site"
        shutil.copytree(ROOT, site, ignore=IGNORE)
        db = site / "db.sqlite3"
        env = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.development", "PYTHONDONTWRITEBYTECODE": "1",
               "RAVIX_UPDATE_DIR": str(tmp / "dist"), "RAVIX_UPDATE_WORK_DIR": str(tmp / "work"), "RAVIX_UPDATE_BACKUP_DIR": str(tmp / "backups"),
               "RAVIX_UPDATE_APP_ROOT": str(site)}
        env.pop("DATABASE_URL", None)
        pgname = None
        if os.environ.get("E2E_PG_URL"):
            import uuid
            from urllib.parse import urlparse

            base = os.environ["E2E_PG_URL"].rstrip("/")
            u = urlparse(base)
            pgname = "ravix_e2eu_" + uuid.uuid4().hex[:8]
            PG["env"] = {**os.environ, "PGHOST": u.hostname, "PGPORT": str(u.port or 5432), "PGUSER": u.username, "PGPASSWORD": u.password or "", "PGDATABASE": "postgres"}
            subprocess.run(["psql", "-qc", f"CREATE DATABASE {pgname}"], env=PG["env"], check=True)
            PG["env"]["PGDATABASE"] = pgname
            PG["drop"] = pgname
            env["DATABASE_URL"] = f"{base}/{pgname}"
            print("   (روی PostgreSQL واقعی)")
        print("۱) ساخت سایت 0.6.0 و دیتابیس")
        run(["manage.py", "migrate", "--noinput"], site, env)
        env_keys = run(["manage.py", "ravix_release", "keygen", "--key-file", str(tmp / "release.key")], site, env).stdout
        pub = env_keys.strip().splitlines()[-1]
        env["RAVIX_TRUSTED_PUBLIC_KEYS"] = f"e2e:{pub}"
        check("restart_required" in columns(db), "جدول تاریخچه ساخته شد")
        check("e2e_note" not in columns(db), "ستون جدید هنوز نیست")
        v0 = tree_hash(site, ["apps", "config", "templates", "static", "manage.py"])

        print("۲) ساخت و امضای بستهٔ 0.7.0 (با مهاجرت واقعی)")
        s7 = tmp / "src070"
        shutil.copytree(site, s7, ignore=shutil.ignore_patterns("__pycache__", "*.sqlite3*"))
        set_version(s7, "0.7.0")
        m = s7 / "apps/updater/models.py"
        m.write_text(m.read_text().replace("    restart_required = models.BooleanField(default=False)\n",
                                           "    restart_required = models.BooleanField(default=False)\n    e2e_note = models.CharField(max_length=20, blank=True)\n"), encoding="utf-8")
        (s7 / "apps/updater/migrations/0002_e2e_note.py").write_text(
            "from django.db import migrations, models\n\n\nclass Migration(migrations.Migration):\n    dependencies = [('updater', '0001_initial')]\n"
            "    operations = [migrations.AddField('updaterun', 'e2e_note', models.CharField(max_length=20, blank=True))]\n", encoding="utf-8")
        run(["manage.py", "ravix_release", "core", "--source", str(s7), "--out", str(tmp / "dist"), "--key-file", str(tmp / "release.key")], site, env)

        print("۳) بررسی بدون تغییر (dry-run)")
        r = run(["manage.py", "ravix_update", "check"], site, env)
        check("0.7.0" in r.stdout, "نسخهٔ جدید دیده شد")
        run(["manage.py", "ravix_update", "apply", "core", "--dry-run"], site, env)
        check(tree_hash(site, ["apps", "config", "templates", "static", "manage.py"]) == v0, "dry-run هیچ فایلی را تغییر نداد")

        print("۴) ارتقای واقعی 0.6.0 → 0.7.0")
        r = run(["manage.py", "ravix_update", "apply", "core"], site, env)
        check("0.7.0" in r.stdout, "ارتقا گزارش موفق داد")
        check(re.search(r'RAVIX_VERSION = "0.7.0"', (site / "apps/core/version.py").read_text()) is not None, "نسخهٔ روی دیسک 0.7.0 است")
        check("e2e_note" in columns(db), "مهاجرت واقعی دیتابیس اعمال شد")
        check("0002_e2e_note" in applied(db), "مهاجرت در django_migrations ثبت شد")
        check(not list((site).glob(".ravix-old*")), "پوشهٔ موقت جایگزینی پاک شد")
        v7 = tree_hash(site, ["apps", "config", "templates", "static", "manage.py"])
        hist = run(["manage.py", "ravix_update", "history"], site, env).stdout
        check("succeeded" in hist, "تاریخچه موفق را ثبت کرد")
        run(["manage.py", "check"], site, env)

        print("۵) بستهٔ 0.8.0 با مهاجرت عمداً خراب ← شکست میانه ← بازگشت خودکار")
        s8 = tmp / "src080"
        shutil.copytree(s7, s8, ignore=shutil.ignore_patterns("__pycache__"))
        set_version(s8, "0.8.0")
        (s8 / "apps/updater/migrations/0003_boom.py").write_text(
            "from django.db import migrations, models\n\n\ndef boom(apps, schema_editor):\n    raise RuntimeError('migration exploded postgres://user:S3CR3TPASS@db/x')\n\n\n"
            "class Migration(migrations.Migration):\n    dependencies = [('updater', '0002_e2e_note')]\n"
            "    operations = [migrations.AddField('updaterun', 'e2e_other', models.CharField(max_length=5, blank=True, default='')), migrations.RunPython(boom)]\n", encoding="utf-8")
        run(["manage.py", "ravix_release", "core", "--source", str(s8), "--out", str(tmp / "dist"), "--key-file", str(tmp / "release.key")], site, env)
        # برای این‌که 0.8.0 آخرین باشد (نه 0.7.0 دوباره): خودکار انتخاب می‌شود
        r = run(["manage.py", "ravix_update", "apply", "core"], site, env, expect_ok=False)
        check(r.returncode != 0, "ارتقای خراب با خطا پایان یافت")
        check("S3CR3TPASS" not in r.stdout + r.stderr, "رمز داخل خطا در خروجی دستور نیامد")
        check(re.search(r'RAVIX_VERSION = "0.7.0"', (site / "apps/core/version.py").read_text()) is not None, "نسخهٔ روی دیسک به 0.7.0 برگشت")
        check(tree_hash(site, ["apps", "config", "templates", "static", "manage.py"]) == v7, "همهٔ فایل‌ها بایت‌به‌بایت مثل قبل از تلاش ارتقا است")
        check("0003_boom" not in applied(db) and "e2e_other" not in columns(db), "دیتابیس به وضعیت قبل از ارتقا برگشت (بدون ستون/مهاجرت نیمه‌کاره)")
        check("e2e_note" in columns(db), "داده/ساختار 0.7.0 سالم ماند")
        row = query(db, "select status, instr(error||recovery, 'S3CR3TPASS') from updater_updaterun order by id desc limit 1",
                    "select status, position('S3CR3TPASS' in error||recovery) from updater_updaterun order by id desc limit 1")[0]
        steps = int(query(db, "select count(*) from updater_updaterun")[0][0])
        check(row[0] == "rolled_back", f"وضعیت اجرا rolled_back است ({row[0]})")
        check(int(row[1]) == 0, "رمز در تاریخچهٔ دیتابیس ذخیره نشد")
        check(steps == 3 or steps == 2, "تاریخچهٔ قبلی پس از بازیابی دیتابیس حفظ شد")
        run(["manage.py", "check"], site, env)
        run(["manage.py", "migrate", "--check"], site, env)

        print("۶) بازگشت دستی به 0.6.0")
        up_id = str(query(db, "select id from updater_updaterun where to_version='0.7.0' and kind='upgrade' and status='succeeded' and backup like '%dir%'",
                          "select id from updater_updaterun where to_version='0.7.0' and kind='upgrade' and status='succeeded' and backup::text like '%dir%'")[0][0])
        r = run(["manage.py", "ravix_update", "rollback", "--run", up_id], site, env, expect_ok=False)
        check(r.returncode != 0 and tree_hash(site, ["apps", "config", "templates", "static", "manage.py"]) == v7, "بدون تأیید صریح چیزی تغییر نکرد")
        run(["manage.py", "ravix_update", "rollback", "--run", up_id, "--yes-data-loss"], site, env)
        check(tree_hash(site, ["apps", "config", "templates", "static", "manage.py"]) == v0, "فایل‌ها دقیقاً به 0.6.0 برگشتند")
        check("e2e_note" not in columns(db) and "0002_e2e_note" not in applied(db), "دیتابیس به 0.6.0 برگشت")
        hist = run(["manage.py", "ravix_update", "history"], site, env).stdout
        check("reverted" in hist and "rollback" not in hist.replace("rolled_back", ""), "تاریخچه: ارتقای اول reverted شد")
        run(["manage.py", "check"], site, env)

        print("۷) بسته‌ٔ دست‌کاری‌شده رد می‌شود")
        pkg = next((tmp / "dist").glob("ravix-core-0.7.0.zip"))
        pkg.write_bytes(pkg.read_bytes() + b"tamper")
        r = run(["manage.py", "ravix_update", "apply", "core", "--to", "0.7.0"], site, env, expect_ok=False)
        check(r.returncode != 0 and "SHA-256" in r.stderr + r.stdout, "SHA-256 نامطابق رد شد")
        if "SHA-256" not in r.stderr + r.stdout:
            print("      خروجی واقعی:", (r.stderr + r.stdout)[-2500:])
        check(tree_hash(site, ["apps", "config", "templates", "static", "manage.py"]) == v0, "هیچ فایلی تغییر نکرد")
    finally:
        if PG.get("drop"):
            PG["env"]["PGDATABASE"] = "postgres"
            subprocess.run(["psql", "-qc", f"DROP DATABASE IF EXISTS {PG['drop']}"], env=PG["env"])
        if not os.environ.get("E2E_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print("پوشهٔ موقت نگه داشته شد:", tmp)
    print()
    if FAILED:
        print(f"✘ {len(FAILED)} بررسی ناموفق:")
        for f in FAILED:
            print("   -", f)
        raise SystemExit(1)
    print("✔ همهٔ بررسی‌های سرتاسری موفق بود.")


if __name__ == "__main__":
    main()
