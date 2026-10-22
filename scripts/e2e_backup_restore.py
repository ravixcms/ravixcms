#!/usr/bin/env python
"""
آزمون واقعی «پشتیبان ← نابودی ← بازیابی» روی PostgreSQL واقعی (نه فقط ساخت فایل پشتیبان).

مراحل: دیتابیس تازه + migrate + داده‌ٔ نمونه (کاربر، نوشته، محصول دیجیتال، فایل media و فایل خصوصی فروشگاه) ← deploy/backup.sh ←
خروجی کامل dumpdata ← DROP DATABASE و حذف پوشه‌ها ← CREATE DATABASE خالی ← deploy/restore.sh ← مقایسهٔ dumpdata (باید یکسان باشد)،
هش فایل‌ها، ورود کاربر با همان رمز، manage.py check. سپس: پشتیبان دست‌کاری‌شده باید رد شود.

اجرا:  E2E_PG_URL='postgresql://USER:PASS@HOST:5432' python scripts/e2e_backup_restore.py   (USER باید CREATEDB داشته باشد)
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
BASE = os.environ.get("E2E_PG_URL", "").rstrip("/")
FAILED = []


def check(cond, msg):
    print(("  ✔ " if cond else "  ✘ ") + msg)
    if not cond:
        FAILED.append(msg)


def sh(cmd, env, cwd=ROOT, ok=True, inp=None):
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, input=inp, timeout=900)
    if ok and r.returncode != 0:
        print(r.stdout[-1500:], r.stderr[-1500:])
        raise SystemExit(f"دستور ناموفق: {cmd}")
    return r


def tree_hash(path: Path):
    h = hashlib.sha256()
    for f in sorted(p for p in path.rglob("*") if p.is_file()):
        h.update(f.relative_to(path).as_posix().encode()); h.update(f.read_bytes())
    return h.hexdigest()


def main():
    if not BASE:
        raise SystemExit("E2E_PG_URL را تنظیم کنید (مثلاً postgresql://user:pass@localhost:5432).")
    u = urlparse(BASE)
    admin_env = {**os.environ, "PGHOST": u.hostname, "PGPORT": str(u.port or 5432), "PGUSER": u.username, "PGPASSWORD": u.password or "", "PGDATABASE": "postgres"}
    name = "ravin_e2e_" + uuid.uuid4().hex[:8]
    url = f"{BASE}/{name}"
    tmp = Path(tempfile.mkdtemp(prefix="ravin-e2e-bk-"))
    media, priv, bk = tmp / "media", tmp / "shop_private", tmp / "backups"
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.development", "DATABASE_URL": url, "RAVIN_SHOP_ENABLED": "true",
           "MEDIA_DIR": str(media), "RAVIN_SHOP_PRIVATE_ROOT": str(priv), "BACKUP_DIR": str(bk), "RAVIN_ENV_FILE": "/nonexistent",
           "RAVIN_PLUGIN_INSTALL_DIR": str(tmp / "pi"), "RAVIN_PLUGIN_BACKUP_DIR": str(tmp / "pb"), "RAVIN_REGISTRY_STORAGE": str(tmp / "reg"), "RAVIN_RESTORE_YES": "1"}
    # در تنظیمات، MEDIA_ROOT ثابت است؛ برای آزمون، رسانه را با symlink به پوشهٔ موقت می‌بریم
    real_media = ROOT / "media"
    try:
        print("۱) ساخت دیتابیس تازه و migrate")
        sh(["psql", "-qc", f"CREATE DATABASE {name}"], admin_env)
        sh([PY, "manage.py", "migrate", "--noinput", "-v0"], env)
        print("۲) داده‌ٔ نمونه")
        seed = f'''
from apps.accounts.models import User
from apps.content.models import Post
from apps.shop.models import Product
from apps.sites.models import Site
from django.core.files.base import ContentFile
u = User.objects.create_superuser("bk_root", "bk@example.com", "Bk-R00t-Pass-77")
Post.objects.create(title="نوشتهٔ پشتیبان", slug="bk-post", body="متن مهم ۱۲۳", status="published")
site = Site.objects.get(is_default=True)
p = Product(site=site, kind="digital", name="کتاب دیجیتال", slug="bk-book", price=1000, stock=0, track_stock=False)
p.digital_file.save("book.pdf", ContentFile(b"%PDF-1.4 secret-digital-content"), save=False)
p.save()
print(p.digital_file.path)
'''
        r = sh([PY, "manage.py", "shell", "-c", seed], env)
        priv_file = Path(r.stdout.strip().splitlines()[-1])
        check(priv_file.is_file(), f"فایل دیجیتال خصوصی ساخته شد ({priv_file.parent.name}/…)")
        media.mkdir(parents=True)
        (media / "uploads").mkdir()
        (media / "uploads" / "a.png").write_bytes(b"\x89PNG-fake-bytes-for-backup-test")
        before_media, before_priv = tree_hash(media), tree_hash(priv)
        dump_args = [PY, "manage.py", "dumpdata", "--natural-foreign", "--natural-primary", "--indent", "0", "-e", "sessions"]
        before = sh(dump_args, env).stdout
        check(len(before) > 1000, f"خروجی کامل دیتابیس مبدأ ({len(before)} بایت)")

        print("۳) deploy/backup.sh")
        r = sh(["bash", "deploy/backup.sh"], env)
        files = sorted(p.name for p in bk.iterdir())
        print("   ", files)
        check(any(f.endswith(".dump") for f in files) and any(f.endswith(".sha256") for f in files), "dump و فایل هش ساخته شد")
        check(any(f.endswith(".media.tar.gz") for f in files) and any(f.endswith(".shop_private.tar.gz") for f in files), "آرشیو media و فایل‌های دیجیتال ساخته شد")
        check(oct(next(bk.glob("*.dump")).stat().st_mode & 0o777) == "0o600", "مجوز فایل پشتیبان 600 است")
        check("testpw" not in r.stdout + r.stderr and u.password not in (r.stdout + r.stderr), "رمز دیتابیس در خروجی اسکریپت نیامد")
        dump = next(bk.glob("*.dump"))
        stamp = dump.name[:-5]
        tars = sorted(str(p) for p in bk.glob(stamp + ".*.tar.gz"))

        print("۴) نابودی کامل: DROP DATABASE + حذف media و فایل‌های خصوصی")
        sh(["psql", "-qc", f"DROP DATABASE {name}"], admin_env)
        sh(["psql", "-qc", f"CREATE DATABASE {name}"], admin_env)
        shutil.rmtree(media); shutil.rmtree(priv)
        gone = sh([PY, "manage.py", "shell", "-c", "from apps.accounts.models import User; print(User.objects.count())"], env, ok=False)
        check(gone.returncode != 0, "دیتابیس خالی است (جدولی وجود ندارد)")

        print("۵) deploy/restore.sh")
        # پشتیبان دست‌کاری‌شده باید رد شود
        bad = tmp / "tampered"; shutil.copytree(bk, bad)
        with open(bad / dump.name, "ab") as f:
            f.write(b"tamper")
        rr = sh(["bash", "deploy/restore.sh", str(bad / dump.name)], env, ok=False)
        check(rr.returncode == 2 and "هش" in rr.stderr, "پشتیبان دست‌کاری‌شده با کنترل هش رد شد (قبل از لمس دیتابیس)")
        sh(["bash", "deploy/restore.sh", str(dump), *tars], env)
        sh([PY, "manage.py", "migrate", "--noinput", "-v0"], env)

        print("۶) راستی‌آزمایی بازیابی")
        after = sh(dump_args, env).stdout
        check(after == before, "dumpdata دیتابیس بازیابی‌شده با مبدأ دقیقاً یکسان است")
        check(tree_hash(media) == before_media, "فایل‌های media بایت‌به‌بایت بازیابی شد")
        check(tree_hash(priv) == before_priv, "فایل‌های دیجیتال خصوصی بایت‌به‌بایت بازیابی شد")
        login = sh([PY, "manage.py", "shell", "-c",
                    "from django.contrib.auth import authenticate; u=authenticate(username='bk_root', password='Bk-R00t-Pass-77'); print('LOGIN', bool(u and u.is_superuser))"], env)
        check("LOGIN True" in login.stdout, "ورود مدیرکل با همان رمز بعد از بازیابی کار می‌کند")
        sh([PY, "manage.py", "check"], env)
        check(True, "manage.py check پس از بازیابی سالم است")
        client = sh([PY, "manage.py", "shell", "-c",
                     "from django.test import Client; c=Client(); r=c.get('/blog/bk-post/'); print('PAGE', r.status_code, 'متن مهم' in r.content.decode())"], env)
        check("PAGE 200 True" in client.stdout, "نوشتهٔ بازیابی‌شده در سایت عمومی نمایش داده می‌شود")
        print("۷) مسیر PostgreSQL در به‌روزرسان (apps.updater PostgresBackend: pg_dump/pg_restore)")
        code = f"""
from pathlib import Path
from apps.content.models import Post
from apps.updater.backup import PostgresBackend
b = PostgresBackend()
f = Path({str(tmp / 'upd.dump')!r})
b.backup(f)
Post.objects.all().delete()
assert Post.objects.count() == 0
b.restore(f)
print('RESTORED', Post.objects.filter(slug='bk-post').count())
"""
        r = sh([PY, "manage.py", "shell", "-c", code], env)
        check("RESTORED 1" in r.stdout, "PostgresBackend: حذف داده ← restore ← داده برگشت")
    finally:
        sh(["psql", "-qc", f"DROP DATABASE IF EXISTS {name}"], admin_env, ok=False)
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILED:
        print(f"✘ {len(FAILED)} بررسی ناموفق:", *FAILED, sep="\n   - ")
        raise SystemExit(1)
    print("✔ پشتیبان‌گیری و بازیابی واقعی روی PostgreSQL موفق بود.")


if __name__ == "__main__":
    main()
