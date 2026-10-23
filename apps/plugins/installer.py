"""
نصب‌کنندهٔ افزونه (توسعهٔ مدیر افزونه‌های موجود؛ سیستم موازی نیست).

جریان امن:
  دریافت/آپلود (فقط حافظه) ← اعتبارسنجی بسته (مسیر/حجم/manifest/سازگاری) ← SHA-256 و امضا ← استخراج در پوشهٔ موقت هم‌دیسک
  ← تطبیق اثرانگشت استخراج‌شده با اعتبارسنجی‌شده ← ``rename`` اتمی به پوشهٔ نصب.
نصب‌کننده **فقط فایل‌ها را جایگذاری می‌کند**. ثبت در دیتابیس (PluginRecord)، تأیید اثرانگشت و فعال‌سازی همچنان در
``services.install / approve_update / activate`` و با اعتماد صریح مدیر انجام می‌شود؛ امضا اصالت را می‌گوید نه بی‌خطری کد را.

ارتقا: قفل ← پشتیبان zip (با SHA-256) ← جایگزینی اتمی ← افزونه تا تأیید مجدد غیرفعال می‌ماند. بازگشت: همان پشتیبان.
"""
from apps.core.clilang import cli_gettext
from django.utils.translation import gettext
import hashlib
import logging
import os
import shutil
import tempfile
import traceback
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError as DbIntegrityError
from django.db import transaction
from django.utils import timezone

from . import discovery, events, registry, signing, sources, state, versions
from .integrity import IntegrityError as FingerprintError
from .integrity import fingerprint, iter_files
from .manifest import Manifest
from .models import PluginBackup, PluginOperationLock, PluginPackage, PluginRecord
from .package import PackageError, extract_to, inspect
from .services import PluginError, check_compat, run_migrations

logger = logging.getLogger("ravix.plugins")
LOCK_STALE = timedelta(minutes=15)


class InstallError(PluginError):
    """خطای قابل‌نمایش به مدیر (پیام فارسی)."""


# ------------------------------------------------------------------ پیش‌نیازها و قفل
def install_root() -> Path:
    return Path(settings.RAVIX_PLUGIN_INSTALL_DIR)


def _require_admin(user):
    if user is None or not getattr(user, "is_authenticated", False) or not user.has_cap("plugins.manage"):
        raise InstallError(gettext("Only an administrator may do this."))


def _require_web_install():
    if not settings.RAVIX_PLUGIN_WEB_INSTALL:
        raise InstallError(gettext("Web installation is off. To enable it, set RAVIX_PLUGIN_WEB_INSTALL=true in the server settings (only when you have made the install folder writable for the service)."))
    if not settings.RAVIX_PLUGINS_ENABLED:
        raise InstallError(gettext("The plugin system is off."))


@contextmanager
def plugin_lock(slug: str, operation: str):
    """قفل پایگاه‌داده‌ای به‌ازای slug: دو نصب/ارتقا/بازگشت هم‌زمان ممکن نیست. قفل مانده (کرش) پس از ۱۵ دقیقه آزاد می‌شود."""
    holder = uuid.uuid4().hex
    from django.apps import apps as _apps

    if _apps.is_installed("apps.updater"):
        from apps.updater.pipeline import core_update_active

        if core_update_active():
            raise InstallError(gettext("A core or theme upgrade is in progress; try again after it finishes."))
    PluginOperationLock.objects.filter(slug=slug, acquired_at__lt=timezone.now() - LOCK_STALE).delete()
    try:
        with transaction.atomic():
            PluginOperationLock.objects.create(slug=slug, operation=operation, holder=holder)
    except DbIntegrityError:
        raise InstallError(gettext("Another operation is in progress on this plugin; try again shortly."))
    try:
        yield
    finally:
        PluginOperationLock.objects.filter(slug=slug, holder=holder).delete()


@contextmanager
def _logged(slug, user, kind):
    """خطای قابل‌فهم را ثبت و بازمی‌گرداند؛ خطای غیرمنتظره را با جزئیات ثبت و پیام عمومی می‌دهد."""
    try:
        yield
    except InstallError as exc:
        events.log_event(slug or "_installer", f"{kind}_failed", str(exc), level="error", actor=user)
        raise
    except PluginError as exc:
        events.log_event(slug or "_installer", f"{kind}_failed", str(exc), level="error", actor=user)
        raise InstallError(str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception(cli_gettext("Unexpected error during %s of plugin %s"), kind, slug)
        events.log_event(slug or "_installer", f"{kind}_failed", gettext("Unexpected error"), level="error", detail=traceback.format_exc(), actor=user)
        raise InstallError(gettext("An unexpected error occurred and no change was applied; technical details were recorded in the “Plugin log”.")) from exc


# ------------------------------------------------------------------ مرحلهٔ اعتبارسنجی (بدون نوشتن در پوشهٔ نصب)
@dataclass
class Staged:
    data: bytes
    manifest: Manifest
    sha256: str
    fingerprint: str
    verification: signing.Verification
    source: str
    source_ref: str = ""


def stage(data: bytes, *, source: str, source_ref: str = "", expected_slug=None, expected_version=None, expected_sha256=None,
          expected_fingerprint=None, signature: str = "", key_id: str = "") -> Staged:
    actual = hashlib.sha256(data or b"").hexdigest()
    if expected_sha256 and actual != expected_sha256.lower():
        raise InstallError(gettext("The file’s SHA-256 does not match the declared value; the file is corrupt or tampered with and was discarded."))
    try:
        info = inspect(data)
    except PackageError as exc:
        raise InstallError((gettext("The package is not valid: %(exc)s") % {"exc": exc}))
    m = info.manifest
    if expected_slug and m.slug != expected_slug:
        raise InstallError((gettext("The identifier inside the package (“%(slug)s”) does not match the request (“%(expected_slug)s”).") % {"slug": m.slug, "expected_slug": expected_slug}))
    if expected_version and m.version != expected_version:
        raise InstallError((gettext("The version inside the package (%(version)s) does not match the requested version (%(expected_version)s).") % {"version": m.version, "expected_version": expected_version}))
    if expected_fingerprint and info.fingerprint != expected_fingerprint:
        raise InstallError(gettext("The package content fingerprint does not match the registry."))
    problems = check_compat(m)
    if problems:
        raise InstallError((gettext("This package is not compatible with this system: %(problems)s") % {"problems": gettext("; ").join(problems)}))
    ver = signing.verify(m.slug, m.version, info.sha256, info.fingerprint, signature, key_id)
    if ver.state == "invalid":
        raise InstallError((gettext("The digital signature is invalid: %(reason)s") % {"reason": ver.reason}))
    if ver.state == "unsigned" and (source == PluginPackage.Source.REGISTRY or not settings.RAVIX_PLUGINS_ALLOW_UNSIGNED):
        raise InstallError(gettext("The package has no valid digital signature and this site’s policy does not accept unsigned packages (registry packages must always be signed)."))
    return Staged(data, m, info.sha256, info.fingerprint, ver, source, source_ref)


def _extract_verified(staged: Staged, tmp: Path) -> Path:
    """بسته را در پوشهٔ موقت باز می‌کند و اثرانگشت واقعیِ استخراج‌شده را با مقدار اعتبارسنجی‌شده تطبیق می‌دهد."""
    try:
        root_name = extract_to(staged.data, tmp)
        pdir = tmp / root_name
        fp = fingerprint(pdir)
    except (PackageError, FingerprintError) as exc:
        raise InstallError((gettext("Extracting the package failed: %(exc)s") % {"exc": exc}))
    if root_name != staged.manifest.slug or fp != staged.fingerprint:
        raise InstallError(gettext("The extracted content does not match the validated package; the operation was stopped."))
    return pdir


def _record_package(staged: Staged, user) -> PluginPackage:
    v = staged.verification
    return PluginPackage.objects.create(slug=staged.manifest.slug, version=staged.manifest.version, source=staged.source, source_ref=staged.source_ref[:200],
                                        sha256=staged.sha256, fingerprint=staged.fingerprint, signature_state=v.state, signer_key_id=v.key_id,
                                        signer_label=v.label, installed_by=user if getattr(user, "pk", None) else None)


# ------------------------------------------------------------------ نصب تازه
def place_new(staged: Staged, user, *, root: Path | None = None) -> PluginPackage:
    """فایل‌ها را اتمی در پوشهٔ نصب می‌گذارد. ثبت/تأیید/فعال‌سازی بعداً در پنل انجام می‌شود."""
    slug = staged.manifest.slug
    root = Path(root) if root else install_root()
    with plugin_lock(slug, "install"):
        if PluginRecord.objects.filter(slug=slug).exists():
            raise InstallError(gettext("This plugin is already installed; use “Upgrade” for a new version."))
        existing = discovery.find(slug)
        if existing is not None or (root / slug).exists():
            raise InstallError((gettext("A folder named “%(slug)s” already exists on the server; we will not overwrite it.") % {"slug": slug}))
        try:
            root.mkdir(parents=True, exist_ok=True)
            tmp = Path(tempfile.mkdtemp(prefix=".staging-", dir=root))
        except OSError as exc:
            raise InstallError((gettext("The install folder is not writable (%(strerror)s). Check the permissions of RAVIX_PLUGIN_INSTALL_DIR.") % {"strerror": exc.strerror}))
        target = root / slug
        try:
            pdir = _extract_verified(staged, tmp)
            try:
                os.rename(pdir, target)  # هم‌دیسک ⇒ اتمی
            except OSError as exc:
                raise InstallError((gettext("The final placement failed: %(strerror)s") % {"strerror": exc.strerror}))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        try:
            pkg = _record_package(staged, user)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
        events.log_event(slug, "package_placed", (gettext("Package %(version)s (%(source_label)s) was placed; awaiting review and installation.") % {"version": staged.manifest.version, "source_label": _source_label(staged)}), actor=user)
    return pkg


def _source_label(staged):
    sig = gettext("Valid signature: ") + (staged.verification.label or staged.verification.key_id) if staged.verification.ok else gettext("Unsigned")
    return (gettext("%(source)s, %(sig)s") % {"source": staged.source, "sig": sig})


# ------------------------------------------------------------------ پشتیبان
def _applied_last_migration(slug) -> str:
    from django.db.migrations.recorder import MigrationRecorder

    return MigrationRecorder.Migration.objects.filter(app=f"plugin_{slug}").order_by("-id").values_list("name", flat=True).first() or ""


def _backup_dir() -> Path:
    d = Path(settings.RAVIX_PLUGIN_BACKUP_DIR)
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def make_backup(rec: PluginRecord, source_dir: Path, user, reason: str, *, was_active: bool) -> PluginBackup:
    slug = rec.slug
    sub = _backup_dir() / slug
    sub.mkdir(exist_ok=True, mode=0o700)
    name = f"{slug}-{rec.version}-{timezone.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}.zip"
    path = sub / name
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in iter_files(source_dir):
                zf.write(f, f"{slug}/{f.relative_to(source_dir).as_posix()}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, FingerprintError) as exc:
        path.unlink(missing_ok=True)
        raise InstallError((gettext("Creating the backup failed and the operation was not performed: %(exc)s") % {"exc": exc}))
    last = _applied_last_migration(slug)
    return PluginBackup.objects.create(slug=slug, version=rec.version, reason=reason, fingerprint=rec.fingerprint, manifest=rec.manifest, last_migration=last,
                                       had_migrations=bool(last), was_active=was_active, config=rec.config or {}, archive=f"{slug}/{name}",
                                       archive_sha256=digest, created_by=user if getattr(user, "pk", None) else None)


def _prune_backups(slug):
    keep = max(1, int(getattr(settings, "RAVIX_PLUGIN_BACKUPS_KEEP", 5)))
    for b in list(PluginBackup.objects.filter(slug=slug, restored_at__isnull=True))[keep:]:
        (Path(settings.RAVIX_PLUGIN_BACKUP_DIR) / b.archive).unlink(missing_ok=True)
        b.delete()


def _archive_bytes(b: PluginBackup) -> bytes:
    base = Path(settings.RAVIX_PLUGIN_BACKUP_DIR).resolve()
    path = (base / b.archive).resolve()
    if base not in path.parents:
        raise InstallError(gettext("The backup path is invalid."))
    try:
        data = path.read_bytes()
    except OSError:
        raise InstallError(gettext("The backup file was not found."))
    if hashlib.sha256(data).hexdigest() != b.archive_sha256:
        raise InstallError(gettext("The integrity of the backup file could not be verified (SHA-256 mismatch); restoring from it is forbidden."))
    return data


# ------------------------------------------------------------------ ارتقا
def _managed_dir(slug) -> Path:
    item = discovery.find(slug)
    if item is None:
        raise InstallError(gettext("The plugin folder was not found."))
    if item.path.parent.resolve() != install_root().resolve():
        raise InstallError(gettext("This plugin was placed manually (outside the automatic install folder); automatic upgrade and rollback are not possible for it."))
    return item.path


def _deactivate_quietly(rec):
    rec.is_active = False
    rec.save(update_fields=["is_active", "updated_at"])
    registry.unload(rec.slug)
    state.invalidate()


def _swap(target: Path, new_dir: Path, root: Path):
    """target را کنار می‌گذارد و new_dir را اتمی جایش می‌نشاند؛ در خطا وضعیت اولیه برمی‌گردد."""
    old = root / f".old-{target.name}-{uuid.uuid4().hex[:6]}"
    try:
        os.rename(target, old)
    except OSError as exc:
        raise InstallError((gettext("Replacement failed (no change): %(strerror)s") % {"strerror": exc.strerror}))
    try:
        os.rename(new_dir, target)
    except OSError as exc:
        os.rename(old, target)
        raise InstallError((gettext("Replacement failed and the previous version was restored: %(strerror)s") % {"strerror": exc.strerror}))
    shutil.rmtree(old, ignore_errors=True)


def upgrade(staged: Staged, user) -> PluginBackup:
    slug = staged.manifest.slug
    root = install_root()
    with plugin_lock(slug, "upgrade"):
        rec = PluginRecord.objects.filter(slug=slug).first()
        if rec is None:
            raise InstallError(gettext("This plugin is not installed yet."))
        new_v, cur_v = versions.parse(staged.manifest.version), versions.parse(rec.version)
        if not (new_v and cur_v and new_v > cur_v):
            raise InstallError((gettext("The package version (%(version)s) is not newer than the installed version (%(version2)s); use “Rollback” for an earlier version.") % {"version": staged.manifest.version, "version2": rec.version}))
        target = _managed_dir(slug)
        try:
            if fingerprint(target) != rec.fingerprint:
                raise InstallError(gettext("The current plugin code does not match the approved version; approve or revert the changes first."))
        except FingerprintError as exc:
            raise InstallError(str(exc))
        was_active = rec.is_active
        backup = make_backup(rec, target, user, PluginBackup.Reason.UPGRADE, was_active=was_active)
        tmp = Path(tempfile.mkdtemp(prefix=".staging-", dir=root))
        try:
            pdir = _extract_verified(staged, tmp)
            if was_active:
                _deactivate_quietly(rec)
            try:
                _swap(target, pdir, root)
            except InstallError:
                if was_active:
                    PluginRecord.objects.filter(pk=rec.pk).update(is_active=True)
                    state.invalidate()
                raise
        except InstallError:
            backup.delete()  # عملیات انجام نشد؛ پشتیبان بی‌مصرف است
            (Path(settings.RAVIX_PLUGIN_BACKUP_DIR) / backup.archive).unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        registry.unload(slug)
        _record_package(staged, user)
        _prune_backups(slug)
        events.log_event(slug, "upgrade_staged", (gettext("Code replaced %(version)s → %(version2)s (backup #%(pk)s). To continue, you must approve the changes and enable the plugin again.") % {"version": rec.version, "version2": staged.manifest.version, "pk": backup.pk}), actor=user)
    return backup


# ------------------------------------------------------------------ بازگشت
def rollback(backup_id: int, user, *, confirm_data_loss=False) -> PluginBackup:
    _require_admin(user)
    _require_web_install()
    b = PluginBackup.objects.filter(pk=backup_id).first()
    if b is None:
        raise InstallError(gettext("The backup was not found."))
    slug = b.slug
    with _logged(slug, user, "rollback"), plugin_lock(slug, "rollback"):
        b.refresh_from_db()
        if b.restored_at:
            raise InstallError(gettext("This backup has already been used."))
        data = _archive_bytes(b)
        root = install_root()
        rec = PluginRecord.objects.filter(slug=slug).first()
        tmp = Path(tempfile.mkdtemp(prefix=".staging-", dir=root))
        try:
            try:
                name = extract_to(data, tmp)
                old_dir = tmp / name
                if name != slug or fingerprint(old_dir) != b.fingerprint:
                    raise InstallError(gettext("The backup content does not match the recorded fingerprint; the rollback was stopped."))
            except (PackageError, FingerprintError) as exc:
                raise InstallError((gettext("The backup cannot be used: %(exc)s") % {"exc": exc}))
            if b.reason == PluginBackup.Reason.UNINSTALL or rec is None:
                if (root / slug).exists() or discovery.find(slug):
                    raise InstallError(gettext("A folder with this name now exists; restoring does not overwrite."))
                os.rename(old_dir, root / slug)
                b.restored_at = timezone.now()
                b.save(update_fields=["restored_at"])
                events.log_event(slug, "restored_files", (gettext("The files of version %(version)s were restored from the backup (it must be installed again before use).") % {"version": b.version}), actor=user)
                return b
            target = _managed_dir(slug)
            current_fp_ok = fingerprint(target) == rec.fingerprint
            applied = _applied_last_migration(slug)
            needs_reverse = bool(rec.manifest.get("models")) and applied != b.last_migration and current_fp_ok
            if needs_reverse and not confirm_data_loss:
                raise InstallError(gettext("Rolling back must reverse the database migrations of the newer version and the data they added will be deleted. After taking a database backup, tick the confirmation option."))
            if rec.is_active:
                _deactivate_quietly(rec)
            if needs_reverse:
                try:
                    run_migrations(slug, target, rec.fingerprint, target=b.last_migration or "zero")
                except PluginError as exc:
                    raise InstallError((gettext("Reversing the database migration failed; the files were left untouched. Use the database backup. (%(exc)s)") % {"exc": exc}))
            _swap(target, old_dir, root)
            registry.unload(slug)
            from .manifest import parse_manifest
            from .settings_schema import clean_all

            m = parse_manifest(b.manifest, expected_slug=slug)
            cfg, _ = clean_all(m.settings, rec.config or {})
            rec.name, rec.version, rec.fingerprint, rec.manifest, rec.config = m.name, m.version, b.fingerprint, m.to_json(), cfg
            rec.is_active, rec.auto_disabled = False, False
            rec.save()
            state.invalidate()
            b.restored_at = timezone.now()
            b.save(update_fields=["restored_at"])
            events.log_event(slug, "rollback", (gettext("Rolled back to version %(version)s. The plugin is disabled; enable it again after review.") % {"version": b.version}), actor=user)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return b


# ------------------------------------------------------------------ حذف فایل‌ها
def remove_files(slug: str, user) -> PluginBackup | None:
    """پس از حذف رکورد: پوشهٔ افزونهٔ نصب‌شده با نصب‌کننده به آرشیو پشتیبان می‌رود و از پوشهٔ نصب پاک می‌شود (قابل بازیابی)."""
    _require_admin(user)
    with plugin_lock(slug, "remove"):
        if PluginRecord.objects.filter(slug=slug).exists():
            raise InstallError(gettext("Uninstall the plugin first."))
        item = discovery.find(slug)
        if item is None:
            return None
        if item.path.parent.resolve() != install_root().resolve():
            raise InstallError(gettext("This folder was placed manually; delete the files yourself."))
        fake = PluginRecord(slug=slug, version=(item.manifest.version if item.manifest else "0.0.0"), fingerprint=fingerprint(item.path),
                            manifest=item.manifest.to_json() if item.manifest else {}, config={})
        b = make_backup(fake, item.path, user, PluginBackup.Reason.UNINSTALL, was_active=False)
        shutil.rmtree(item.path)
        _prune_backups(slug)
        events.log_event(slug, "files_removed", (gettext("The plugin files were deleted (backup #%(pk)s).") % {"pk": b.pk}), actor=user)
        return b


# ------------------------------------------------------------------ نقطه‌های ورود (برای ویو/دستور)
def install_from_registry(slug: str, version: str | None, user, *, upgrade_existing=False):
    """نصب (یا ارتقای) افزونه از مخزن رسمی. برمی‌گرداند: ("installed"|"upgraded", slug)."""
    _require_admin(user)
    _require_web_install()
    with _logged(slug, user, "install"):
        src = sources.get_source()
        info = src.info(slug)
        entry = sources.pick_version(info, version)
        data = src.download(slug, entry)
        staged = stage(data, source=PluginPackage.Source.REGISTRY, source_ref=f"{src.ref}#{slug}@{entry['version']}", expected_slug=slug,
                       expected_version=entry["version"], expected_sha256=entry.get("sha256"), expected_fingerprint=entry.get("fingerprint"),
                       signature=entry.get("signature") or "", key_id=entry.get("signature_key_id") or "")
        return _place_or_upgrade(staged, user, upgrade_existing)


def install_from_file(data: bytes, user, *, signature: str = "", key_id: str = "", upgrade_existing=False):
    _require_admin(user)
    _require_web_install()
    slug = None
    with _logged(slug, user, "install"):
        staged = stage(data, source=PluginPackage.Source.LOCAL, source_ref="upload", signature=signature.strip(), key_id=key_id.strip())
        return _place_or_upgrade(staged, user, upgrade_existing)


def _place_or_upgrade(staged, user, upgrade_existing):
    slug = staged.manifest.slug
    if PluginRecord.objects.filter(slug=slug).exists():
        if not upgrade_existing:
            raise InstallError(gettext("This plugin is already installed; use “Upgrade” for a new version."))
        upgrade(staged, user)
        return "upgraded", slug
    place_new(staged, user)
    return "installed", slug
