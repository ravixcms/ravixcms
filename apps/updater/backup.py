"""
پشتیبان‌گیری پیش از هر ارتقا: آرشیو فایل‌های مدیریت‌شده (tar.gz) + پشتیبان دیتابیس. پوشهٔ پشتیبان با مجوز ۰۷۰۰.
SQLite با API رسمی backup (سازگار با WAL)؛ PostgreSQL با pg_dump/pg_restore (رمز فقط از طریق متغیر محیطی PGPASSWORD، نه آرگومان).
فایل‌های ``.env``، media و دیتابیس هرگز داخل آرشیو فایل‌ها نیستند.
"""
from django.utils.translation import gettext
import errno
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

from .errors import UpdateBlocked, UpdateError
from .logs import sanitize
from .package import CORE_ENTRIES


def backup_dir() -> Path:
    d = Path(settings.RAVIX_UPDATE_BACKUP_DIR)
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
            raise UpdateError(gettext("Backing up an in-memory database is not possible."))
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
            raise UpdateError((gettext("The %(cmd)s tool is not installed on the server; %(what)s was not possible.") % {"cmd": cmd[0], "what": what}))
        r = subprocess.run(cmd, env=self.env, capture_output=True, text=True, timeout=3600)
        if r.returncode != 0:
            raise UpdateError((gettext("%(what)s failed: %(sanitize)s") % {"what": what, "sanitize": sanitize(r.stderr, 500)}))

    def backup(self, dest: Path):
        self._run(["pg_dump", "-Fc", "-f", str(dest), *self.args], gettext("Database backup"))

    def restore(self, src: Path):
        connection.close()
        self._run(["pg_restore", "--clean", "--if-exists", "--no-owner", *self.args, str(src)], gettext("Database restore"))


class MysqlBackend:
    """MySQL/MariaDB: ``mysqldump``/``mariadb-dump`` + ``mysql`` when the host has them (the password goes through MYSQL_PWD, never an argument);
    otherwise a portable JSON dump written by Django itself (gzip) that ``loaddata`` can read back. Both formats are recognised on restore."""

    name = "mysql"

    def __init__(self):
        s = connection.settings_dict
        self.name_db = str(s["NAME"])
        self.args = ["-h", str(s.get("HOST") or "localhost"), "-P", str(s.get("PORT") or 3306), "-u", str(s.get("USER") or "")]
        self.env = {**os.environ, "MYSQL_PWD": str(s.get("PASSWORD") or "")}

    @staticmethod
    def _tool(*names):
        for n in names:
            found = shutil.which(n)
            if found:
                return found
        return None

    def backup(self, dest: Path):
        tool = self._tool("mysqldump", "mariadb-dump")
        if tool:
            cmd = [tool, *self.args, "--single-transaction", "--quick", "--no-tablespaces", "--default-character-set=utf8mb4", f"--result-file={dest}", self.name_db]
            r = subprocess.run(cmd, env=self.env, capture_output=True, text=True, timeout=3600)
            if r.returncode == 0:
                return
        self._json_dump(dest)

    def _json_dump(self, dest: Path):
        import gzip
        import io

        from django.core.management import call_command

        buf = io.StringIO()
        call_command("dumpdata", "--natural-foreign", "--natural-primary", "-e", "contenttypes", "-e", "auth.permission", "-e", "sessions", stdout=buf)
        with gzip.open(dest, "wt", encoding="utf-8") as fh:
            fh.write(buf.getvalue())

    def restore(self, src: Path):
        connection.close()
        with open(src, "rb") as fh:
            magic = fh.read(2)
        if magic == b"\x1f\x8b":
            import gzip
            import tempfile

            from django.core.management import call_command

            call_command("flush", interactive=False, verbosity=0)
            with gzip.open(src, "rb") as zin, tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
                shutil.copyfileobj(zin, tmp)
            try:
                call_command("loaddata", tmp.name, verbosity=0)
            finally:
                os.unlink(tmp.name)
            return
        tool = self._tool("mysql", "mariadb")
        if not tool:
            raise UpdateError(gettext("The mysql client tool is not installed on the server; the restore was not possible."))
        with open(src, "rb") as fh:
            r = subprocess.run([tool, *self.args, "--default-character-set=utf8mb4", self.name_db], env=self.env, stdin=fh, capture_output=True, timeout=3600)
        if r.returncode != 0:
            raise UpdateError((gettext("Database restore failed: %(sanitize)s") % {"sanitize": sanitize(r.stderr.decode("utf-8", "replace"), 500)}))


def default_db_backend():
    vendor = connection.vendor
    if vendor == "sqlite":
        return SqliteBackend()
    if vendor == "postgresql":
        return PostgresBackend()
    if vendor == "mysql":
        return MysqlBackend()
    raise UpdateError((gettext("Automatic backup is not supported for the “%(vendor)s” database.") % {"vendor": vendor}))


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
                raise UpdateError(gettext("The backup archive is unsafe; the restore was not performed."))
        tar.extractall(dest)  # noqa: S202  # nosec B202
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


def _db_size(backend) -> int:
    """Rough size of the database (0 when it cannot be told): for the free-space check before a backup."""
    try:
        if backend.name == "sqlite":
            return Path(connection.settings_dict["NAME"]).stat().st_size
        with connection.cursor() as cur:
            if connection.vendor == "postgresql":
                cur.execute("select pg_database_size(current_database())")
            elif connection.vendor == "mysql":
                cur.execute("select coalesce(sum(data_length + index_length), 0) from information_schema.tables where table_schema = database()")
            else:
                return 0
            return int(cur.fetchone()[0])
    except Exception:  # noqa: BLE001 - an estimate must never stop an update
        return 0


def ensure_space(root: Path, backend, extra: tuple = ()) -> None:
    """Stops before anything is written when the backup folder cannot hold the backup (files + database, uncompressed as an upper bound)."""
    from django.template.defaultfilters import filesizeformat

    base = backup_dir()
    need = _db_size(backend) + sum(f.stat().st_size for _entry, p in _managed_paths(root, extra) for f in ([p] if p.is_file() else p.rglob("*")) if f.is_file()) + 16 * 1024 * 1024
    free = shutil.disk_usage(base).free
    if free < need:
        raise UpdateBlocked(gettext("Not enough free disk space for the backup in %(dir)s: about %(need)s are needed and %(free)s are free. Free some space (for example delete old backups in that folder) and try again. Nothing was changed.")
                            % {"dir": str(base), "need": filesizeformat(need), "free": filesizeformat(free)})


def create_backup(label: str, root: Path, db_backend=None, extra: tuple = ()) -> dict:
    """پشتیبان کامل می‌سازد و ``{dir, db_file, db_sha256, files_archive, files_sha256, backend}`` برمی‌گرداند."""
    backend = db_backend or default_db_backend()
    ensure_space(root, backend, extra)
    base = backup_dir()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    d = base / f"{stamp}-{label}"
    n = 1
    while d.exists():
        n += 1
        d = base / f"{stamp}-{label}-{n}"
    d.mkdir(mode=0o700)
    db_file = d / ("db.sqlite3" if backend.name == "sqlite" else "db.dump")
    arch = d / "files.tar.gz"
    try:
        backend.backup(db_file)
        if not db_file.is_file() or db_file.stat().st_size == 0:
            raise UpdateError(gettext("The database backup file was not created."))
        make_files_archive(root, arch, extra)
    except OSError as exc:
        shutil.rmtree(d, ignore_errors=True)  # never leave a half-written backup that fills the disk
        if exc.errno in (errno.ENOSPC, errno.EDQUOT):
            from django.template.defaultfilters import filesizeformat

            raise UpdateError(gettext("The disk became full while the backup was being written (%(free)s free in %(dir)s). Nothing was changed; free some space and try again.")
                              % {"free": filesizeformat(shutil.disk_usage(base).free), "dir": str(base)}) from exc
        raise
    except UpdateError:
        shutil.rmtree(d, ignore_errors=True)
        raise
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
            raise UpdateError((gettext("The backup is corrupted or incomplete (%(info)s); automatic restore will not be performed.") % {"info": info[key]}))
    return d


def prune(keep: int):
    base = backup_dir()
    dirs = sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name)
    for old in dirs[: max(len(dirs) - max(keep, 1), 0)]:
        shutil.rmtree(old, ignore_errors=True)
