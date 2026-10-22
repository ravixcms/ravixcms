"""
پشتیبان‌گیری پیش از هر ارتقا: آرشیو فایل‌های مدیریت‌شده (tar.gz) + پشتیبان دیتابیس. پوشهٔ پشتیبان با مجوز ۰۷۰۰.
SQLite با API رسمی backup (سازگار با WAL)؛ PostgreSQL با pg_dump/pg_restore (رمز فقط از طریق متغیر محیطی PGPASSWORD، نه آرگومان).
فایل‌های ``.env``، media و دیتابیس هرگز داخل آرشیو فایل‌ها نیستند.
"""
import hashlib
import os
import shutil
import sqlite3
import subprocess
import tarfile
import time
from pathlib import Path

from django.conf import settings
from django.db import connection

from .errors import UpdateError
from .logs import sanitize
from .package import CORE_ENTRIES


def backup_dir() -> Path:
    d = Path(settings.RAVIN_UPDATE_BACKUP_DIR)
    d.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(d, 0o700)
    return d


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ دیتابیس
class SqliteBackend:
    name = "sqlite"

    def __init__(self, db_path: str | None = None):
        self.path = db_path or connection.settings_dict["NAME"]

    def backup(self, dest: Path):
        if str(self.path) == ":memory:":
            raise UpdateError("پشتیبان‌گیری از دیتابیس درون‌حافظه ممکن نیست.")
        src = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(str(dest))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

    def restore(self, src: Path):
        connection.close()
        tmp = Path(str(self.path) + ".restore-tmp")
        shutil.copy2(src, tmp)
        for ext in ("-wal", "-shm"):
            Path(str(self.path) + ext).unlink(missing_ok=True)
        os.replace(tmp, self.path)


class PostgresBackend:
    """pg_dump -Fc / pg_restore --clean. در این محیط آزمایش واقعی نشده (docs/upgrade.md)؛ ابزارها باید روی سرور نصب باشند."""

    name = "postgres"

    def __init__(self):
        s = connection.settings_dict
        self.args = ["-h", str(s.get("HOST") or "localhost"), "-p", str(s.get("PORT") or 5432), "-U", str(s.get("USER") or ""), "-d", str(s["NAME"])]
        self.env = {**os.environ, "PGPASSWORD": str(s.get("PASSWORD") or "")}

    def _run(self, cmd, what):
        if shutil.which(cmd[0]) is None:
            raise UpdateError(f"ابزار {cmd[0]} روی سرور نصب نیست؛ {what} ممکن نشد.")
        r = subprocess.run(cmd, env=self.env, capture_output=True, text=True, timeout=3600)
        if r.returncode != 0:
            raise UpdateError(f"{what} ناموفق بود: {sanitize(r.stderr, 500)}")

    def backup(self, dest: Path):
        self._run(["pg_dump", "-Fc", "-f", str(dest), *self.args], "پشتیبان‌گیری دیتابیس")

    def restore(self, src: Path):
        connection.close()
        self._run(["pg_restore", "--clean", "--if-exists", "--no-owner", *self.args, str(src)], "بازیابی دیتابیس")


def default_db_backend():
    vendor = connection.vendor
    if vendor == "sqlite":
        return SqliteBackend()
    if vendor == "postgresql":
        return PostgresBackend()
    raise UpdateError(f"پشتیبان‌گیری خودکار برای دیتابیس «{vendor}» پشتیبانی نمی‌شود.")


# ------------------------------------------------------------------ فایل‌ها
def _managed_paths(root: Path, extra: tuple = ()):
    for entry in (*CORE_ENTRIES, *extra):
        p = root / entry
        if p.exists():
            yield entry, p


def _tar_filter(info: tarfile.TarInfo):
    name = info.name
    if "__pycache__" in name or name.endswith((".pyc", ".pyo")):
        return None
    return info


def make_files_archive(root: Path, dest: Path, extra: tuple = ()):
    with tarfile.open(dest, "w:gz") as tar:
        for entry, p in _managed_paths(root, extra):
            tar.add(p, arcname=entry, filter=_tar_filter)
    if os.name == "posix":
        os.chmod(dest, 0o600)


def extract_archive(archive: Path, dest: Path) -> Path:
    """آرشیو پشتیبان را (پس از بررسی ناامنی) در پوشهٔ موقت ``dest`` باز می‌کند؛ جایگزینی فایل‌های سایت جداگانه و با swap انجام می‌شود."""
    base = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        for m in tar.getmembers():
            tgt = (base / m.name).resolve()
            if not (tgt == base or base in tgt.parents) or m.issym() or m.islnk() or m.isdev():
                raise UpdateError("آرشیو پشتیبان ناامن است؛ بازیابی انجام نشد.")
        tar.extractall(dest)  # noqa: S202 - نام‌ها بالا بررسی شد
    return base


def make_dir_archive(src: Path, dest: Path):
    """آرشیو یک پوشه (مثلاً یک قالب) با نام پوشه به‌عنوان ریشه."""
    with tarfile.open(dest, "w:gz") as tar:
        tar.add(src, arcname=src.name, filter=_tar_filter)
    if os.name == "posix":
        os.chmod(dest, 0o600)


def create_dir_backup(label: str, src: Path) -> dict:
    """پشتیبان فقط-فایل از یک پوشه (برای قالب‌ها که مهاجرت دیتابیس ندارند)."""
    base = backup_dir()
    d = base / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}"
    n = 1
    while d.exists():
        n += 1
        d = base / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}-{n}"
    d.mkdir(mode=0o700)
    arch = d / "files.tar.gz"
    make_dir_archive(src, arch)
    return {"dir": d.name, "files_archive": arch.name, "files_sha256": sha256_file(arch), "backend": "none"}


def create_backup(label: str, root: Path, db_backend=None, extra: tuple = ()) -> dict:
    """پشتیبان کامل می‌سازد و ``{dir, db_file, db_sha256, files_archive, files_sha256, backend}`` برمی‌گرداند."""
    base = backup_dir()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    d = base / f"{stamp}-{label}"
    n = 1
    while d.exists():
        n += 1
        d = base / f"{stamp}-{label}-{n}"
    d.mkdir(mode=0o700)
    backend = db_backend or default_db_backend()
    db_file = d / ("db.sqlite3" if backend.name == "sqlite" else "db.dump")
    backend.backup(db_file)
    if not db_file.is_file() or db_file.stat().st_size == 0:
        raise UpdateError("فایل پشتیبان دیتابیس ساخته نشد.")
    arch = d / "files.tar.gz"
    make_files_archive(root, arch, extra)
    for f in (db_file,):
        if os.name == "posix":
            os.chmod(f, 0o600)
    return {"dir": d.name, "db_file": db_file.name, "db_sha256": sha256_file(db_file), "files_archive": arch.name, "files_sha256": sha256_file(arch),
            "backend": backend.name}


def verify_backup(info: dict) -> Path:
    d = backup_dir() / info["dir"]
    for key, sha in (("db_file", "db_sha256"), ("files_archive", "files_sha256")):
        if key not in info:
            continue
        f = d / info[key]
        if not f.is_file() or sha256_file(f) != info[sha]:
            raise UpdateError(f"پشتیبان خراب یا ناقص است ({info[key]}); بازیابی خودکار انجام نمی‌شود.")
    return d


def prune(keep: int):
    base = backup_dir()
    dirs = sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name)
    for old in dirs[: max(len(dirs) - max(keep, 1), 0)]:
        shutil.rmtree(old, ignore_errors=True)
