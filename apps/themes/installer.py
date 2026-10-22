"""
نصب قالب از فایل ZIP (بارگذاری از پنل).

مسیر نصب: دریافت ← اعتبارسنجی فهرست ZIP (``package.validate``) ← قفل ← استخراج در پوشهٔ موقتِ تصادفی و محدود (کنار پوشهٔ نصب،
روی همان فایل‌سیستم) ← ``inspect_theme_dir`` (همان اعتبارسنجی قراردادی قالب‌ها) ← بررسی slug یکتا ← ``os.rename`` اتمی ← اعتبارسنجی نهایی.

- هیچ فایلی پیش از تکمیل اعتبارسنجی وارد مسیر قابل‌استفادهٔ قالب‌ها نمی‌شود (پوشه‌های ``.staging-*`` را registry نمی‌بیند).
- قالب موجود (هر پوشه‌ای با همان نام در هر یک از مسیرهای قالب) و قالب ``default`` هرگز بازنویسی نمی‌شود.
- قالب پس از نصب **فعال نمی‌شود**؛ فعال‌سازی همان مسیر فعلی ``services.activate`` است.
- ``collectstatic`` هرگز از داخل درخواست وب اجرا نمی‌شود.
- اگر قفل فایل‌سیستمی قابل‌اعتماد نباشد یا ``rename`` اتمی ممکن نشود، نصب انجام **نمی‌شود** (جایگزین ناامن نداریم).
"""
import fcntl
import logging
import os
import shutil
import stat
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from django.apps import apps as django_apps
from django.conf import settings

from apps.audit.models import AuditLog
from apps.audit.services import log_event

from . import package, registry
from .constants import DEFAULT_THEME, MAX_ZIP_BYTES
from .manifest import inspect_theme_dir

logger = logging.getLogger(__name__)

AUDIT_TYPE = "ظاهر سایت"
CAPABILITY = "themes.install"
STAGING_PREFIX = ".staging-"
LOCK_FILE = ".install.lock"
REVISION_FILE = ".revision"
STALE_STAGING_SECONDS = 3600
UNRELIABLE_LOCK_FS = frozenset({"nfs", "nfs4", "cifs", "smb3", "smbfs", "afs", "9p", "fuse.sshfs"})


class ThemeInstallError(Exception):
    """خطای قابل‌نمایش به مدیر (پیام فارسی، بدون مسیر سرور یا جزئیات داخلی)."""

    def __init__(self, message: str, *, security: bool = False, details: tuple[str, ...] = ()):
        super().__init__(message)
        self.message = message
        self.security = security
        self.details = details


@dataclass(frozen=True)
class InstallResult:
    slug: str
    name: str
    version: str
    file_count: int
    extracted_bytes: int
    has_static: bool


def install_root() -> Path:
    """مسیر واقعی پوشهٔ نصب (اگر مدیر سرور آن را symlink کرده باشد یک بار resolve می‌شود و از آن به بعد همان مسیر به‌کار می‌رود)."""
    return Path(settings.RAVIN_THEME_INSTALL_DIR).resolve()


def revision_path() -> Path:
    return Path(settings.RAVIN_THEME_INSTALL_DIR) / REVISION_FILE


# ------------------------------------------------------------------ ثبت رویداد
def _audit(request, kind: str, text: str, *, ok: bool = False) -> None:
    """
    از مقادیر موجود AuditLog.Action استفاده می‌شود (migration جدید نداریم): موفقیت = «ایجاد»؛ آغاز/رد/خطا = «ویرایش» با پیشوند
    متنی صریح تا هرگز شکست یا رد امنیتی به‌عنوان موفقیت دیده نشود. هیچ مقدار محرمانه‌ای ثبت نمی‌شود.
    """
    action = AuditLog.Action.CREATE if ok else AuditLog.Action.UPDATE
    log_event(request, action, None, f"نصب قالب ZIP — {kind}: {text}"[:400], object_type=AUDIT_TYPE)


# ------------------------------------------------------------------ پیش‌نیازها
def _require_enabled() -> None:
    if not getattr(settings, "RAVIN_THEME_WEB_INSTALL", False):
        raise ThemeInstallError(
            "نصب قالب از طریق وب در این سرور خاموش است. مدیر سرور باید آگاهانه RAVIN_THEME_WEB_INSTALL=true را تنظیم کند "
            "(راهنما: docs/themes.md)."
        )


def _require_capability(user) -> None:
    if user is None or not getattr(user, "is_authenticated", False) or not user.has_cap(CAPABILITY):
        raise ThemeInstallError("فقط مدیرکل مجاز به نصب قالب است.")


def _mount_fstype(path: Path) -> str:
    """نوع فایل‌سیستمِ محل path (تلاش بهینه؛ در خطا رشتهٔ خالی)."""
    try:
        real = os.path.realpath(path)
        best, fstype = "", ""
        with open("/proc/mounts", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                fields = line.split()
                if len(fields) < 3:
                    continue
                mount = fields[1].replace("\\040", " ")
                if (real == mount or real.startswith(mount.rstrip("/") + "/")) and len(mount) > len(best):
                    best, fstype = mount, fields[2]
        return fstype
    except OSError:
        return ""


def _prepare_root() -> Path:
    root = install_root()
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o750)
    except OSError:
        raise ThemeInstallError("پوشهٔ نصب قالب ساخته نشد یا قابل‌نوشتن نیست؛ RAVIN_THEME_INSTALL_DIR و مجوزهای آن را بررسی کنید.") from None
    if not root.is_dir() or not os.access(root, os.W_OK | os.X_OK):
        raise ThemeInstallError("پوشهٔ نصب قالب قابل‌نوشتن نیست؛ RAVIN_THEME_INSTALL_DIR و مجوزهای آن را بررسی کنید.")
    fstype = _mount_fstype(root)
    if fstype in UNRELIABLE_LOCK_FS:
        raise ThemeInstallError(
            f"فایل‌سیستم پوشهٔ نصب ({fstype}) قفل قابل‌اعتماد ندارد؛ نصب برای جلوگیری از تداخل انجام نشد. "
            "پوشهٔ نصب را روی دیسک محلی بگذارید."
        )
    return root


@contextmanager
def _install_lock(root: Path):
    """قفل فایل‌سیستمی غیرمسدودکننده: دو نصب هم‌زمان ممکن نیست و با مرگ فرایند خودکار آزاد می‌شود. در هر خطا بسته می‌شود."""
    if django_apps.is_installed("apps.updater"):
        from apps.updater.pipeline import core_update_active

        if core_update_active():
            raise ThemeInstallError("ارتقای هسته یا قالب در جریان است؛ پس از پایان آن دوباره تلاش کنید.")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(root / LOCK_FILE, flags, 0o600)
    except OSError:
        raise ThemeInstallError("فایل قفل نصب ساخته نشد؛ مجوزهای پوشهٔ نصب را بررسی کنید.") from None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ThemeInstallError("نصب دیگری هم‌اکنون در جریان است؛ کمی بعد دوباره تلاش کنید.") from None
        except OSError:
            raise ThemeInstallError(
                "قفل فایل‌سیستمی روی پوشهٔ نصب پشتیبانی نمی‌شود؛ برای ایمنی نصب انجام نشد. پوشهٔ نصب را روی دیسک محلی بگذارید."
            ) from None
        yield
    finally:
        os.close(fd)  # بستن fd قفل را آزاد می‌کند


def _cleanup_stale_staging(root: Path) -> None:
    """پوشه‌های .staging-* باقی‌مانده از کرش (قدیمی‌تر از یک ساعت) را پاک می‌کند؛ هرگز چیز دیگری را لمس نمی‌کند."""
    now = time.time()
    try:
        children = list(root.iterdir())
    except OSError:
        return
    for child in children:
        try:
            st = child.lstat()
            if child.name.startswith(STAGING_PREFIX) and stat.S_ISDIR(st.st_mode) and now - st.st_mtime > STALE_STAGING_SECONDS:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue


def _tighten_permissions(tree: Path) -> None:
    os.chmod(tree, 0o750)
    for dirpath, dirnames, filenames in os.walk(tree, followlinks=False):
        for name in dirnames:
            os.chmod(os.path.join(dirpath, name), 0o750)
        for name in filenames:
            os.chmod(os.path.join(dirpath, name), 0o640)


def _conflict(slug: str) -> str | None:
    if slug == DEFAULT_THEME:
        return "قالب پیش‌فرض («default») با این روش جایگزین یا بازنویسی نمی‌شود."
    for base in getattr(settings, "RAVIN_THEME_DIRS", []):
        if os.path.lexists(Path(base) / slug):
            return f"قالبی با شناسهٔ «{slug}» از قبل وجود دارد و بازنویسی نمی‌شود. شناسهٔ قالب را در theme.json و نام پوشه تغییر دهید."
    return None


def _touch_revision(root: Path) -> None:
    """مهر نسخه برای سایر workerها: registry با دیدن تغییر آن فهرست قالب‌ها را دوباره می‌خواند."""
    try:
        (root / REVISION_FILE).write_text(uuid.uuid4().hex, encoding="utf-8")
    except OSError:
        logger.warning("نوشتن مهر نسخهٔ قالب‌ها ممکن نشد؛ workerهای دیگر تا کش بعدی قالب جدید را نمی‌بینند.")


# ------------------------------------------------------------------ نصب
def _install(data: bytes) -> InstallResult:
    zf = package.open_zip(data)
    with zf:
        pkg = package.validate(zf, len(data))
        slug = pkg.root
        message = _conflict(slug)
        if message:
            raise ThemeInstallError(message)
        root = _prepare_root()
        with _install_lock(root):
            _cleanup_stale_staging(root)
            staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=root))  # 0700 و نام تصادفی
            try:
                if staging.parent != root:
                    raise ThemeInstallError("پوشهٔ موقت در جای نامعتبر ساخته شد؛ نصب لغو شد.")
                extracted = package.extract(zf, pkg, staging)
                theme_dir = staging / slug
                _tighten_permissions(theme_dir)
                info = inspect_theme_dir(theme_dir)
                if not info.valid:
                    raise ThemeInstallError("قالب با قرارداد قالب‌های Ravix سازگار نیست.", details=info.errors[:10])
                # بررسی دوباره زیر قفل؛ از این لحظه تا rename فقط همین فرایند در پوشهٔ نصب می‌نویسد.
                message = _conflict(slug)
                if message:
                    raise ThemeInstallError(message)
                target = root / slug
                if target.parent.resolve() != root:
                    raise ThemeInstallError("مسیر مقصد نامعتبر است؛ نصب لغو شد.", security=True)
                try:
                    os.rename(theme_dir, target)  # هم‌دیسک ⇒ اتمی؛ هیچ حالت نیمه‌نصبی دیده نمی‌شود
                except OSError:
                    raise ThemeInstallError("جایگذاری نهایی قالب ناموفق بود و هیچ تغییری اعمال نشد.") from None
                final = inspect_theme_dir(target)
                if not final.valid:
                    if not target.is_symlink():
                        shutil.rmtree(target, ignore_errors=True)  # فقط پوشه‌ای که همین فرایند و زیر همین قفل ساخت
                    raise ThemeInstallError("اعتبارسنجی نهایی پس از نصب ناموفق بود؛ نصب لغو شد.")
                _touch_revision(root)
                registry.get_themes(refresh=True)
                return InstallResult(
                    slug=slug,
                    name=final.display_name,
                    version=final.version,
                    file_count=pkg.file_count,
                    extracted_bytes=extracted,
                    has_static=(target / "static").is_dir(),
                )
            finally:
                if staging.parent == root and staging.name.startswith(STAGING_PREFIX) and not staging.is_symlink():
                    shutil.rmtree(staging, ignore_errors=True)


def install_theme_zip(request, uploaded) -> InstallResult:
    """نقطهٔ ورود: ``uploaded`` یک UploadedFile است. در هر شکست ThemeInstallError (پیام فارسی امن) می‌اندازد و ممیزی ثبت می‌شود."""
    user = getattr(request, "user", None)
    size = getattr(uploaded, "size", None)
    try:
        _require_enabled()
        _require_capability(user)
        _audit(request, "آغاز", f"درخواست دریافت شد (حجم {int(size or 0) // 1024} کیلوبایت).")
        if size is None or size <= 0:
            raise ThemeInstallError("فایلی ارسال نشد یا فایل خالی است.")
        if size > MAX_ZIP_BYTES:
            raise ThemeInstallError(f"حجم فایل بیشتر از {MAX_ZIP_BYTES // (1024 * 1024)} مگابایت است.")
        data = uploaded.read(MAX_ZIP_BYTES + 1)
        try:
            result = _install(data)
        except package.ThemePackageError as exc:
            raise ThemeInstallError(exc.message, security=exc.security) from None
    except ThemeInstallError as exc:
        extra = (" — " + "؛ ".join(exc.details[:3])) if exc.details else ""
        _audit(request, "رد امنیتی" if exc.security else "رد شد", exc.message + extra)
        raise
    except Exception:  # noqa: BLE001
        logger.exception("خطای غیرمنتظره در نصب قالب ZIP")
        _audit(request, "خطای داخلی", "نصب با خطای غیرمنتظره متوقف شد؛ جزئیات فقط در لاگ سرور است و هیچ تغییری اعمال نشد.")
        raise ThemeInstallError("خطای غیرمنتظره رخ داد و هیچ تغییری اعمال نشد؛ مدیر سرور جزئیات را در لاگ‌ها می‌بیند.") from None
    _audit(request, "موفق", f"قالب «{result.slug}» نسخه {result.version or '—'} نصب شد ({result.file_count} فایل)؛ فعال نشد.", ok=True)
    return result
