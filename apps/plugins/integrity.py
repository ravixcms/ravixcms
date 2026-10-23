"""اثرانگشت (SHA-256) محتوای پوشهٔ افزونه. نصب، همین اثرانگشت را «تأیید» می‌کند؛ هر تغییر بعدی کد، بارگذاری را متوقف می‌کند."""
from django.utils.translation import gettext
import hashlib
import os
from pathlib import Path

MAX_FILES = 600
MAX_TOTAL_BYTES = 8 * 1024 * 1024
_SKIP_DIRS = {"__pycache__"}
_SKIP_FILES = {".DS_Store", "Thumbs.db"}


class IntegrityError(Exception):
    pass


def iter_files(root: Path):
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for d in dirnames:
            if (Path(dirpath) / d).is_symlink():
                raise IntegrityError((gettext("Symbolic links (symlinks) are not allowed: %(d)s") % {"d": d}))
        for name in sorted(filenames):
            if name in _SKIP_FILES or name.endswith(".pyc"):
                continue
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file():
                raise IntegrityError((gettext("Unusual files/symbolic links are not allowed: %(relative_to)s") % {"relative_to": path.relative_to(root)}))
            yield path


def fingerprint(root) -> str:
    root = Path(root)
    digest = hashlib.sha256()
    total = count = 0
    for path in iter_files(root):
        count += 1
        size = path.stat().st_size
        total += size
        if count > MAX_FILES or total > MAX_TOTAL_BYTES:
            raise IntegrityError(gettext("The plugin exceeds the file count/size limit."))
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0" + str(size).encode() + b"\0")
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()
