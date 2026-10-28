"""The daily backup of a shared-hosting site, written into the data folder (never public_html).

One folder per backup: the database (``apps.updater.backup``: SQLite API, mysqldump/JSON or pg_dump), the media files (when the folder is not
larger than ``RAVIX_BACKUP_MAX_MEDIA_MB``) and a copy of the private settings file (it holds the keys that make the database readable).
"""
import os
import shutil
import tarfile
import time
from datetime import datetime, timezone as dt_tz
from pathlib import Path

from django.conf import settings

from apps.updater import backup as db_backup


def backup_root() -> Path:
    root = Path(getattr(settings, "RAVIX_BACKUP_DIR", None) or Path(settings.BASE_DIR).parent / "ravix-backups")
    root.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(root, 0o700)
    return root


def _dir_size(path: Path) -> int:
    total = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(base, name))
            except OSError:
                pass
    return total


def list_backups() -> list[Path]:
    root = backup_root()
    return sorted((p for p in root.iterdir() if p.is_dir() and (p / "done").exists()), key=lambda p: p.name)


def latest_backup_time() -> datetime | None:
    found = list_backups()
    if not found:
        return None
    return datetime.fromtimestamp((found[-1] / "done").stat().st_mtime, tz=dt_tz.utc)


def due(hours: int = 24) -> bool:
    last = latest_backup_time()
    return last is None or (time.time() - last.timestamp()) >= hours * 3600


def create() -> dict:
    root = backup_root()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = root / stamp
    n = 1
    while target.exists():
        n += 1
        target = root / f"{stamp}-{n}"
    target.mkdir(mode=0o700)
    backend = db_backup.default_db_backend()
    db_file = target / ("db.sqlite3" if backend.name == "sqlite" else "db.dump")
    backend.backup(db_file)
    if not db_file.is_file() or db_file.stat().st_size == 0:
        shutil.rmtree(target, ignore_errors=True)
        raise RuntimeError("the database backup file was not created")
    info = {"dir": target.name, "database": db_file.name, "media": False}
    media = Path(settings.MEDIA_ROOT)
    cap = int(getattr(settings, "RAVIX_BACKUP_MAX_MEDIA_MB", 300)) * 1024 * 1024
    if media.is_dir() and _dir_size(media) <= cap:
        with tarfile.open(target / "media.tar.gz", "w:gz") as tar:
            tar.add(media, arcname="media")
        info["media"] = True
    env_file = Path(getattr(settings, "RAVIX_ENV_FILE", "") or "")
    if env_file.is_file():
        shutil.copy2(env_file, target / "ravix.env")
    if os.name == "posix":
        for f in target.iterdir():
            os.chmod(f, 0o600)
    (target / "done").write_text("ok\n", encoding="utf-8")
    prune(int(getattr(settings, "RAVIX_BACKUP_KEEP", 7)))
    return info


def prune(keep: int) -> None:
    found = list_backups()
    for old in found[: max(len(found) - max(keep, 1), 0)]:
        shutil.rmtree(old, ignore_errors=True)
    # half-written folders (no "done" marker) older than a day
    for p in backup_root().iterdir():
        if p.is_dir() and not (p / "done").exists() and time.time() - p.stat().st_mtime > 86400:
            shutil.rmtree(p, ignore_errors=True)
