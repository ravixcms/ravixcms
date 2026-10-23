"""
بررسی و استخراج امن بستهٔ ZIP قالب (بدون اجرای هیچ کد).

این ماژول فقط چیزهایی را می‌سنجد که روی خود فایل ZIP معنا دارند (zip-slip، symlink، zip-bomb، نام‌های خطرناک، ساختار ریشه).
اعتبارسنجی محتوای قالب (theme.json، قالب‌های الزامی، تگ‌های مجاز، پسوندهای مجاز ...) **بعد از استخراج** و فقط با
``manifest.inspect_theme_dir`` انجام می‌شود تا منطق موازی ساخته نشود.

ساختار مورد انتظار: ``<slug>/theme.json`` ، ``<slug>/templates/…`` ، ``<slug>/static/…`` (یک پوشهٔ ریشه به نام شناسهٔ قالب).
"""
from django.utils.translation import gettext
import os
import re
import stat
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .constants import (
    ALLOWED_EXTENSIONS,
    MAX_COMPRESSION_RATIO,
    MAX_EXTRACTED_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_PATH_DEPTH,
    MAX_PATH_LENGTH,
    MAX_ZIP_BYTES,
    MAX_ZIP_ENTRIES,
)

ZIP_MAGIC = b"PK\x03\x04"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,48}$")
# هر مؤلفهٔ مسیر: فقط حروف لاتین، عدد، «.»، «-» و «_»؛ بدون فاصله، بدون نقطهٔ ابتدایی (پنهان)، بدون یونیکد (ضد نام‌های مبهم).
COMPONENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")
DRIVE_RE = re.compile(r"^[A-Za-z]:")

# فایل‌های اجرایی/اسکریپتی که هرگز مجاز نیستند (پسوند آخر یا هر پسوند میانی، مثل x.php.png).
FORBIDDEN_EXTENSIONS = frozenset(
    {
        "py", "pyc", "pyo", "pyw", "pyz", "php", "php3", "php4", "php5", "php7", "phtml", "phar", "pl", "cgi", "sh", "bash",
        "bat", "cmd", "ps1", "exe", "dll", "so", "dylib", "com", "msi", "jar", "rb", "asp", "aspx", "jsp", "htaccess",
    }
)
# در نسخهٔ اول SVG پذیرفته نمی‌شود (محتوای فعال در SVG پاک‌سازی نمی‌شود).
BLOCKED_IN_UPLOAD = frozenset({"svg"})
UPLOAD_EXTENSIONS = ALLOWED_EXTENSIONS - BLOCKED_IN_UPLOAD
ROOT_FILES = frozenset({"LICENSE"})  # تنها فایل بدون پسوند مجاز (در ریشهٔ قالب)


class ThemePackageError(Exception):
    """خطای قابل‌نمایش به مدیر. ``security=True`` یعنی رد به‌دلیل الگوی حملهٔ شناخته‌شده (در ممیزی به‌عنوان رد امنیتی ثبت می‌شود)."""

    def __init__(self, message: str, *, security: bool = False):
        super().__init__(message)
        self.message = message
        self.security = security


def show(name: str, limit: int = 60) -> str:
    """نام کنترل‌نشدهٔ داخل ZIP را برای نمایش/ثبت امن می‌کند (escape نویسه‌های غیرمعمول و کوتاه‌سازی)."""
    text = ascii(str(name))[1:-1]
    return text if len(text) <= limit else text[:limit] + "…"


@dataclass(frozen=True)
class Entry:
    info: zipfile.ZipInfo
    parts: tuple[str, ...]
    is_dir: bool


@dataclass(frozen=True)
class PackageInfo:
    root: str
    entries: tuple[Entry, ...]
    zip_bytes: int
    declared_bytes: int
    file_count: int


def check_upload_bytes(data: bytes) -> None:
    if not data:
        raise ThemePackageError(gettext("No file was sent or the file is empty."))
    if len(data) > MAX_ZIP_BYTES:
        raise ThemePackageError((gettext("The file is larger than %(value)s MB.") % {"value": (MAX_ZIP_BYTES // (1024 * 1024))}))
    if not data.startswith(ZIP_MAGIC):
        # پسوند .zip به‌تنهایی کافی نیست؛ امضای واقعی ZIP بررسی می‌شود (ZIP خالی هم رد می‌شود).
        raise ThemePackageError(gettext("This file is not a valid ZIP (the file signature does not match ZIP)."))


def open_zip(data: bytes) -> zipfile.ZipFile:
    import io

    check_upload_bytes(data)
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError, EOFError, ValueError, NotImplementedError):
        raise ThemePackageError(gettext("The ZIP file is corrupted or incomplete and could not be opened.")) from None


def _split(name: str) -> tuple[tuple[str, ...], bool]:
    if "\\" in name:
        raise ThemePackageError((gettext("Unsafe name in ZIP (backslash): “%(show)s”.") % {"show": show(name)}), security=True)
    if "\x00" in name or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ThemePackageError(gettext("Unsafe name in ZIP (control character)."), security=True)
    if name.startswith("/") or DRIVE_RE.match(name):
        raise ThemePackageError((gettext("Absolute paths are not allowed in ZIP: “%(show)s”.") % {"show": show(name)}), security=True)
    is_dir = name.endswith("/")
    parts = tuple(name.rstrip("/").split("/")) if name.rstrip("/") else ()
    if not parts or any(p in ("", ".", "..") for p in parts):
        raise ThemePackageError((gettext("Unsafe path in ZIP (. or .. or an empty segment): “%(show)s”.") % {"show": show(name)}), security=True)
    if len(name) > MAX_PATH_LENGTH or len(parts) > MAX_PATH_DEPTH:
        raise ThemePackageError((gettext("The path “%(show)s” is too long or too deep.") % {"show": show(name)}))
    return parts, is_dir


def _check_component_names(parts: tuple[str, ...], name: str) -> None:
    for comp in parts:
        if not COMPONENT_RE.match(comp):
            raise ThemePackageError(
                (gettext("The name “%(show)s” is not allowed; file and folder names may only contain English letters, digits, “.”, “-” and “_” (no spaces, no Persian characters, no leading dot).") % {"show": show(comp)})
            )


def _check_extension(parts: tuple[str, ...], name: str) -> None:
    base = parts[-1]
    if len(parts) == 2 and base in ROOT_FILES:
        return
    pieces = base.lower().split(".")
    if len(pieces) < 2:
        raise ThemePackageError((gettext("A file without an extension is not allowed: “%(show)s”.") % {"show": show(name)}))
    if any(p in FORBIDDEN_EXTENSIONS for p in pieces[1:]):
        raise ThemePackageError((gettext("An executable or script file is not allowed in a theme (such as py or php): “%(show)s”.") % {"show": show(name)}), security=True)
    ext = pieces[-1]
    if ext in BLOCKED_IN_UPLOAD:
        raise ThemePackageError((gettext("SVG files are not allowed in this version: “%(show)s”. Use png, webp or jpg instead.") % {"show": show(name)}))
    if ext not in UPLOAD_EXTENSIONS:
        raise ThemePackageError((gettext("The file extension of “%(show)s” is not allowed in a theme.") % {"show": show(name)}))


def validate(zf: zipfile.ZipFile, zip_bytes: int) -> PackageInfo:
    """فهرست ورودی‌های ZIP را بدون هیچ استخراجی بررسی می‌کند و در صورت مشکل ThemePackageError می‌دهد."""
    infos = zf.infolist()
    if not infos:
        raise ThemePackageError(gettext("The ZIP is empty."))
    if len(infos) > MAX_ZIP_ENTRIES:
        raise ThemePackageError((gettext("The number of ZIP entries exceeds the limit (at most %(MAX_ZIP_ENTRIES)s).") % {"MAX_ZIP_ENTRIES": MAX_ZIP_ENTRIES}))
    seen: set[str] = set()
    file_paths: set[str] = set()
    dir_paths: set[str] = set()
    entries: list[Entry] = []
    roots: set[str] = set()
    declared = 0
    files = 0
    for info in infos:
        name = info.filename
        parts, is_dir = _split(name)
        _check_component_names(parts, name)
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ThemePackageError((gettext("Symbolic links are not allowed in ZIP: “%(show)s”.") % {"show": show(name)}), security=True)
        if stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ThemePackageError((gettext("Special or unusual entries are not allowed in ZIP: “%(show)s”.") % {"show": show(name)}), security=True)
        if info.flag_bits & 0x1:
            raise ThemePackageError(gettext("Encrypted ZIP files are not allowed."))
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ThemePackageError(gettext("The ZIP compression method is not supported (only Deflate or no compression)."))
        if is_dir != info.is_dir():
            raise ThemePackageError((gettext("Inconsistent entry in ZIP: “%(show)s”.") % {"show": show(name)}), security=True)
        key = "/".join(parts).lower()
        if key in seen:
            raise ThemePackageError((gettext("Duplicate name (or differing only in letter case) in ZIP: “%(show)s”.") % {"show": show(name)}), security=True)
        seen.add(key)
        roots.add(parts[0])
        if is_dir:
            dir_paths.add(key)
            entries.append(Entry(info, parts, True))
            continue
        if len(parts) < 2:
            raise ThemePackageError(
                (gettext("The file “%(show)s” is outside the theme folder. All files must be inside one folder named after the theme ID (e.g. my-theme/theme.json).") % {"show": show(name)})
            )
        _check_extension(parts, name)
        if info.file_size > MAX_FILE_BYTES:
            raise ThemePackageError((gettext("The size of file “%(show)s” exceeds %(value)s MB.") % {"show": show(name), "value": (MAX_FILE_BYTES // (1024 * 1024))}))
        if info.file_size > 0 and info.compress_size == 0:
            raise ThemePackageError(gettext("The ZIP is suspicious (a zero-size file in the compressed data)."), security=True)
        if info.compress_size > 0 and info.file_size > 64 * 1024 and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
            raise ThemePackageError((gettext("The compression ratio of “%(show)s” is unusual (possible zip bomb).") % {"show": show(name)}), security=True)
        declared += info.file_size
        if declared > MAX_EXTRACTED_BYTES:
            raise ThemePackageError(
                (gettext("The total size of the extracted files exceeds %(value)s MB.") % {"value": (MAX_EXTRACTED_BYTES // (1024 * 1024))}), security=True
            )
        files += 1
        if files > MAX_FILES:
            raise ThemePackageError((gettext("The number of theme files exceeds %(MAX_FILES)s.") % {"MAX_FILES": MAX_FILES}))
        file_paths.add(key)
        entries.append(Entry(info, parts, False))
    if len(roots) != 1:
        raise ThemePackageError(
            gettext("The ZIP must have only one root folder (named after the theme ID). If you made the ZIP on macOS, do not include the __MACOSX folder; put the files inside one folder and zip again.")
        )
    root = next(iter(roots))
    if not SLUG_RE.match(root):
        raise ThemePackageError(
            (gettext("The root folder name (“%(show)s”) is not a valid theme ID; only lowercase English letters, digits, “-” and “_” (at most 49 characters).") % {"show": show(root)})
        )
    all_dirs = set(dir_paths)  # پوشه‌های صریح + پوشه‌های ضمنی (والد هر فایل)
    for fp in file_paths:
        segs = fp.split("/")
        all_dirs.update("/".join(segs[:i]) for i in range(1, len(segs)))
    if file_paths & all_dirs:  # یک نام هم فایل است و هم پوشه
        raise ThemePackageError(gettext("Conflicting paths in ZIP (one name is both a file and a folder)."), security=True)
    if files == 0:
        raise ThemePackageError(gettext("The ZIP contains no files."))
    return PackageInfo(root=root, entries=tuple(entries), zip_bytes=zip_bytes, declared_bytes=declared, file_count=files)


def extract(zf: zipfile.ZipFile, package: PackageInfo, dest: Path) -> int:
    """
    ورودی‌های اعتبارسنجی‌شده را در ``dest`` (پوشهٔ خالیِ ساخته‌شده توسط خود برنامه) می‌نویسد؛ فایل‌ها هرگز اجرا نمی‌شوند.
    حجم واقعی هنگام خواندن هم شمرده می‌شود (اعلام نادرست حجم در ZIP کارساز نیست). مجموع بایت‌های نوشته‌شده را برمی‌گرداند.
    """
    dest = Path(dest)
    real_dest = os.path.realpath(dest)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    total = 0
    try:
        for entry in package.entries:
            target = dest.joinpath(*entry.parts)
            parent = target if entry.is_dir else target.parent
            os.makedirs(parent, mode=0o750, exist_ok=True)
            real_parent = os.path.realpath(parent)
            if real_parent != real_dest and not real_parent.startswith(real_dest + os.sep):
                raise ThemePackageError(gettext("Path outside the destination folder in ZIP (zip-slip)."), security=True)
            if entry.is_dir:
                continue
            written = 0
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o640)
            with os.fdopen(fd, "wb") as out, zf.open(entry.info) as src:
                while True:
                    chunk = src.read(65536)
                    if not chunk:
                        break
                    written += len(chunk)
                    total += len(chunk)
                    if written > MAX_FILE_BYTES or total > MAX_EXTRACTED_BYTES:
                        raise ThemePackageError(gettext("The real size of the extracted files exceeds the limit (possible zip bomb)."), security=True)
                    out.write(chunk)
            if written != entry.info.file_size:
                raise ThemePackageError(gettext("The real size of one of the files does not match the ZIP declaration; the ZIP is tampered with or corrupted."), security=True)
    except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError):
        raise ThemePackageError(gettext("The ZIP file is corrupted (the compressed content is not valid).")) from None
    except FileExistsError:
        raise ThemePackageError(gettext("Duplicate name in ZIP."), security=True) from None
    except OSError as exc:
        if isinstance(exc, PermissionError) or exc.errno in (13, 28, 30):  # دسترسی/پر بودن دیسک/فقط‌خواندنی: پیام عمومی
            raise ThemePackageError(gettext("Could not write to the temporary folder; check the server’s permissions and disk space.")) from None
        raise
    return total


def posix_name(parts) -> str:
    return str(PurePosixPath(*parts))
