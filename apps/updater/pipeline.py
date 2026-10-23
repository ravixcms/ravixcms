"""
خط لولهٔ ارتقا: بررسی ← دریافت به پوشهٔ موقت ← SHA-256 ← امضا ← استخراج و اعتبارسنجی ← سازگاری ← پشتیبان ← (حالت تعمیر) جایگزینی ← بررسی ← مهاجرت ← تأیید.

اصل‌ها:
* تا پیش از «جایگزینی» هیچ فایلی از سایت تغییر نمی‌کند؛ هر شکست پیش از آن ⇐ وضعیت FAILED (بدون تغییر).
* پس از شروع جایگزینی هر شکستی ⇐ بازگشت خودکار (فایل‌ها از نسخهٔ کنار گذاشته‌شده؛ دیتابیس از پشتیبان تأییدشده). اگر بازگشت هم شکست خورد ⇐ NEEDS_RECOVERY با دستور بازیابی دستی.
* قفل سراسری (یکتایی در دیتابیس): دو ارتقای هم‌زمان ممکن نیست؛ قفل مانده پس از مدتی آزاد می‌شود و اجرای نیمه‌کاره «نیازمند بازیابی» علامت می‌خورد.
* هیچ کلید/رمزی ثبت نمی‌شود: همهٔ متن‌های ذخیره‌شده از logs.sanitize می‌گذرند.
"""
from django.utils.translation import gettext
import logging
import os
import shutil
import traceback
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.color import no_style
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone

from apps.core.deprecation import read_legacy_key
from apps.core.version import PLUGIN_API_VERSION, THEME_API_VERSION

from . import backup as backups
from . import package, signing, sources, versions
from .errors import UpdateBlocked, UpdateBusy, UpdateError
from .logs import sanitize
from .models import UpdateLock, UpdateRun
from .runner import SubprocessRunner

logger = logging.getLogger("ravix.updater")


# ------------------------------------------------------------------ قفل و تاریخچه

def _columns(model) -> set:
    with connection.cursor() as c:
        return {col.name for col in connection.introspection.get_table_description(c, model._meta.db_table)}


def safe_save(obj):
    """ذخیرهٔ سازگار با «ناهمخوانی شما»: پس از بازگردانی/مهاجرت دیتابیس ممکن است جدول ستون‌هایی را نداشته باشد که مدل در حافظه دارد (و برعکس).
    فقط ستون‌هایی نوشته می‌شود که در جدول واقعی هستند؛ همیشه UPDATE روی ردیف موجود."""
    cols = _columns(type(obj))
    names = [f.name for f in type(obj)._meta.concrete_fields if not f.primary_key and f.column in cols]
    obj.save(update_fields=names)


def _restore_table(model, rows):
    """جدول را با ردیف‌های ذخیره‌شده پر می‌کند و فقط ستون‌هایی را می‌نویسد که در جدول فعلی هستند (سازگار با تغییر شما بین نسخه‌ها)."""
    qn = connection.ops.quote_name
    cols = _columns(model)
    fields = [f for f in model._meta.concrete_fields if f.column in cols]
    with connection.cursor() as c:
        c.execute(f"DELETE FROM {qn(model._meta.db_table)}")
        for r in rows:
            names, vals = [], []
            for f in fields:
                if f.attname in r:
                    names.append(qn(f.column))
                    vals.append(f.get_db_prep_save(r[f.attname], connection))
            if names:
                c.execute(f"INSERT INTO {qn(model._meta.db_table)} ({', '.join(names)}) VALUES ({', '.join(['%s'] * len(names))})", vals)
        # PostgreSQL: درج با شناسهٔ صریح، sequence را جلو نمی‌برد؛ بدون این، ردیف بعدی با کلید تکراری شکست می‌خورد
        for sql in connection.ops.sequence_reset_sql(no_style(), [model]):
            c.execute(sql)

def _stale_cutoff():
    return timezone.now() - timedelta(minutes=settings.RAVIX_UPDATE_LOCK_STALE_MINUTES)


def mark_interrupted():
    """اجرای «در حال اجرا»یی که قفل زنده‌ای ندارد (کرش/قطع برق) ⇐ نیازمند بازیابی دستی."""
    live = set(UpdateLock.objects.filter(run__isnull=False).values_list("run_id", flat=True))
    grace = timezone.now() - timedelta(seconds=120)  # اجرایی که همین حالا ساخته شده و هنوز قفل نگرفته، «نیمه‌کاره» نیست
    for run in UpdateRun.objects.filter(status=UpdateRun.Status.RUNNING, started_at__lt=grace).exclude(pk__in=live):
        run.status = UpdateRun.Status.NEEDS_RECOVERY
        run.error = (gettext("%(value)sThe run was left unfinished (process/server interrupted); check the state of the files and the database.") % {"value": (run.error + "\n" if run.error else "")})
        run.recovery = run.recovery or recovery_text(run)
        run.finished_at = timezone.now()
        safe_save(run)


@contextmanager
def update_lock(operation: str, run: UpdateRun | None = None):
    holder = uuid.uuid4().hex
    UpdateLock.objects.filter(acquired_at__lt=_stale_cutoff()).delete()
    mark_interrupted()
    try:
        with transaction.atomic():
            UpdateLock.objects.create(name="global", holder=holder, operation=operation, run=run)
    except IntegrityError:
        cur = UpdateLock.objects.filter(name="global").first()
        raise UpdateBusy((gettext("Another upgrade is in progress (%(value)s); try again after it finishes.") % {"value": (cur.operation if cur else '?')}))
    try:
        yield holder
    finally:
        UpdateLock.objects.filter(holder=holder).delete()
        from .middleware import reset_cache

        reset_cache()


def _set_lock(holder: str, operation: str):
    UpdateLock.objects.filter(holder=holder).update(operation=operation)
    from .middleware import reset_cache

    reset_cache()


def lock_state():
    lock = UpdateLock.objects.filter(name="global", acquired_at__gte=_stale_cutoff()).first()
    return {"operation": lock.operation, "since": lock.acquired_at} if lock else None


def core_update_active() -> bool:
    """برای نصب‌کنندهٔ افزونه: وقتی ارتقای هسته/قالب در جریان است، نصب/ارتقای افزونه ممنوع است."""
    return UpdateLock.objects.filter(name="global", acquired_at__gte=_stale_cutoff()).exists()


def step(run: UpdateRun, name: str, ok: bool = True, detail: str = ""):
    run.steps = [*run.steps, {"name": name, "ok": ok, "at": timezone.now().isoformat(timespec="seconds"), "detail": sanitize(detail, 600)}]
    run.phase = name
    try:
        safe_save(run)
    except DatabaseError:
        if ok:
            raise
        logger.exception("ثبت مرحلهٔ شکست در تاریخچه ممکن نشد")


def finish(run: UpdateRun, status: str, error: str = ""):
    run.status = status
    if error:
        run.error = sanitize(error)
    run.finished_at = timezone.now()
    safe_save(run)


def migration_state() -> list[str]:
    return sorted(f"{a}.{n}" for a, n in MigrationRecorder(connection).applied_migrations())


def recovery_text(run: UpdateRun) -> str:
    b = run.backup or {}
    lines = [gettext("Manual recovery (stop the web service):")]
    if b.get("dir"):
        d = Path(settings.RAVIX_UPDATE_BACKUP_DIR) / b["dir"]
        lines += [(gettext("1) Backup: %(d)s") % {"d": d}),
                  (gettext("2) Files: tar -xzf %(value)s -C %(RAVIX_UPDATE_APP_ROOT)s  (first set aside the apps, config, static, templates, etc. folders)") % {"value": (d / b.get('files_archive', 'files.tar.gz')), "RAVIX_UPDATE_APP_ROOT": settings.RAVIX_UPDATE_APP_ROOT})]
        if b.get("db_file"):
            if b.get("backend") == "postgres":
                lines.append((gettext("3) Database: pg_restore --clean --if-exists -d <database_name> %(value)s") % {"value": (d / b['db_file'])}))
            else:
                lines.append((gettext("3) Database: put the file %(value)s in place of the site’s database file (and delete the -wal/-shm files next to it).") % {"value": (d / b['db_file'])}))
    else:
        lines.append(gettext("No backup was recorded; use the latest manual backup (deploy/backup.sh)."))
    lines += [gettext("4) Restart the service and run `python manage.py ravix_update status` and `python manage.py ravix_preflight`."),
              gettext("Full guide: docs/upgrade.md")]
    return "\n".join(lines)


# ------------------------------------------------------------------ سازگاری
def compat(new_version: str, requires: dict, *, component="core") -> tuple[list[str], list[str]]:
    """(مانع‌ها، هشدارها). پیش از هر تغییری بررسی می‌شود؛ مانع ⇐ ارتقا انجام نمی‌شود."""
    problems, warnings = [], []
    cur = versions.running_version()
    if component == "core":
        if not versions.newer(new_version, cur):
            problems.append((gettext("Version %(new_version)s is not newer than the current version (%(cur)s) (downgrading is done with “Roll back”).") % {"new_version": new_version, "cur": cur}))
        if requires.get("min_from") and versions.newer(requires["min_from"], cur):
            problems.append((gettext("To reach %(new_version)s you must first upgrade to %(requires)s (or higher); the current version is %(cur)s.") % {"new_version": new_version, "requires": requires['min_from'], "cur": cur}))
    else:
        need = read_legacy_key(requires, default="")  # بسته‌های قدیمی کلید «ravin» دارند
        if need and not versions.satisfies(cur, need):
            problems.append((gettext("This version needs Ravix “%(need)s”; the current version is %(cur)s.") % {"need": need, "cur": cur}))
    if requires.get("python"):
        p = versions.python_problem(requires["python"])
        if p:
            problems.append(p)
    problems += versions.requirements_problems(requires.get("packages") or [])
    if component == "core":
        pa, ta = requires.get("plugin_api"), requires.get("theme_api")
        for pl in versions.installed_plugins():
            bad = []
            if pa is not None and pl.get("api_version") != pa:
                bad.append((gettext("Plugin contract %(pl)s≠%(pa)s") % {"pl": pl.get('api_version'), "pa": pa}))
            if pl["ravix"] and not versions.satisfies(new_version, pl["ravix"]):
                bad.append((gettext("Requires Ravix “%(pl)s”") % {"pl": pl['ravix']}))
            if bad:
                msg = (gettext("Plugin “%(pl)s” is not compatible with the new version (%(bad)s).") % {"pl": pl['slug'], "bad": '؛ '.join(bad)})
                (problems if pl["active"] else warnings).append(msg + ("" if pl["active"] else gettext(" (it is disabled; it will not be enabled after the upgrade.)")))
        for th in versions.installed_themes():
            if th["slug"] == "default":
                continue
            bad = []
            if ta is not None and ta != THEME_API_VERSION:
                bad.append((gettext("Theme contract %(THEME_API_VERSION)s≠%(ta)s") % {"THEME_API_VERSION": THEME_API_VERSION, "ta": ta}))
            if th["ravix"] and not versions.satisfies(new_version, th["ravix"]):
                bad.append((gettext("Requires Ravix “%(th)s”") % {"th": th['ravix']}))
            if bad:
                warnings.append((gettext("Theme “%(th)s” may not be compatible with the new version (%(bad)s).") % {"th": th['slug'], "bad": '؛ '.join(bad)}))
    return problems, warnings


def pick_entry(entries: list[dict], version: str | None) -> dict:
    if version:
        e = next((x for x in entries if x["version"] == version), None)
        if e is None:
            raise UpdateBlocked((gettext("Version %(version)s is not in the update source.") % {"version": version}))
        return e
    cand = [x for x in entries if versions.newer(x["version"], versions.running_version())]
    if not cand:
        raise UpdateBlocked(gettext("There is no newer version in the update source; the site is up to date."))
    ok = [x for x in cand if not x["requires"].get("min_from") or not versions.newer(x["requires"]["min_from"], versions.running_version())]
    return max(ok or cand, key=lambda x: versions.parse(x["version"]) or (0, 0, 0))


def plan_core(index: dict | None = None) -> dict:
    """گزارش بدون تغییر برای پنل: نسخهٔ فعلی، آخرین نسخه، و موانع/هشدارهای پیش‌نمایشی (ازروی فهرست؛ وابستگی‌های داخل بسته هنگام اعمال بررسی می‌شود)."""
    idx = index or sources.cached_index()
    cur = versions.running_version()
    newer = [e for e in idx["core"] if versions.newer(e["version"], cur)]
    if not newer:
        return {"current": cur, "latest": None, "problems": [], "warnings": [], "can_apply": False}
    entry = pick_entry(idx["core"], None)
    problems, warnings = compat(entry["version"], entry["requires"])
    return {"current": cur, "latest": entry, "problems": problems, "warnings": warnings, "can_apply": not problems}


# ------------------------------------------------------------------ دریافت و اعتبارسنجی بسته (بدون تغییر در سایت)
def _work_dir() -> Path:
    base = Path(settings.RAVIX_UPDATE_WORK_DIR)
    base.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(base, 0o700)
    d = base / uuid.uuid4().hex
    d.mkdir(mode=0o700)
    return d


def fetch_verified(src, kind: str, slug: str, entry: dict, run: UpdateRun) -> bytes:
    data = src.download(entry)
    step(run, "download", True, (gettext("%(data_count)s bytes") % {"data_count": len(data)}))
    if len(data) > settings.RAVIX_UPDATE_MAX_PACKAGE_BYTES:
        raise UpdateBlocked(gettext("The package is larger than the size limit."))
    digest = sources.sha256_hex(data)
    run.sha256 = digest
    if digest != entry["sha256"]:
        raise UpdateBlocked(gettext("The SHA-256 of the downloaded package does not match the index (corrupted or tampered download); rejected."))
    step(run, "checksum", True, digest[:16])
    ver = signing.verify(kind, slug, entry["version"], digest, entry.get("signature", ""), entry.get("signature_key_id", ""))
    if not ver.ok:
        step(run, "signature", False, ver.reason)
        raise UpdateBlocked((gettext("The package’s digital signature could not be verified: %(reason)s") % {"reason": ver.reason}))
    run.signer_key_id, run.signer_label = ver.key_id, ver.label
    step(run, "signature", True, (gettext("Key %(key_id)s (%(label)s)") % {"key_id": ver.key_id, "label": ver.label}))
    return data


def _check_disk(root: Path, size: int):
    free = shutil.disk_usage(root).free
    need = size * 4 + 64 * 1024 * 1024
    if free < need:
        raise UpdateBlocked(gettext("Not enough disk space for the upgrade (download, extraction and backup)."))


def _new_run(component, slug, kind, to_version, user, source_ref, parent=None) -> UpdateRun:
    return UpdateRun.objects.create(component=component, slug=slug, kind=kind, from_version=_current(component, slug), to_version=to_version,
                                    source=source_ref[:200], started_by=user if getattr(user, "is_authenticated", False) else None, parent=parent)


def _current(component, slug):
    if component == "core":
        return versions.running_version()
    if component == "theme":
        t = next((x for x in versions.installed_themes() if x["slug"] == slug), None)
        return t["version"] if t else ""
    return next((p["version"] for p in versions.installed_plugins() if p["slug"] == slug), "")


# ------------------------------------------------------------------ جایگزینی اتمیک مسیرها با امکان بازگشت
def swap_entries(root: Path, tree: Path, entries, old_dir: Path, *, remove_missing=False) -> list:
    """هر مسیر را با نسخهٔ ``tree`` عوض می‌کند و نسخهٔ قبلی را در ``old_dir`` نگه می‌دارد. خطا ⇐ همه برمی‌گردد و استثنا بازپرتاب می‌شود."""
    done = []
    try:
        for e in entries:
            new, cur = tree / e, root / e
            exists_cur = cur.exists() or cur.is_symlink()
            if not new.exists():
                if remove_missing and exists_cur:
                    dst = old_dir / e
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(cur, dst)
                    done.append((e, True))
                continue
            if exists_cur:
                dst = old_dir / e
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.replace(cur, dst)
            done.append((e, exists_cur))
            cur.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(new), str(cur))
    except BaseException:
        reverse_swap(root, old_dir, done)
        raise
    return done


def reverse_swap(root: Path, old_dir: Path, done):
    for e, had_old in reversed(done):
        cur = root / e
        if cur.is_dir() and not cur.is_symlink():
            shutil.rmtree(cur, ignore_errors=True)
        elif cur.exists() or cur.is_symlink():
            cur.unlink()
        if had_old and (old_dir / e).exists():
            cur.parent.mkdir(parents=True, exist_ok=True)
            os.replace(old_dir / e, cur)


def _history_snapshot():
    return list(UpdateRun.objects.all().values())


def _history_restore(rows):
    """پس از جایگزینی فایل دیتابیس، تاریخچهٔ ارتقا (که ممکن است در پشتیبانِ قدیمی‌تر نباشد یا شمای متفاوتی داشته باشد) را به همان حالت برمی‌گرداند."""
    from django.contrib.auth import get_user_model

    users = set(get_user_model().objects.values_list("pk", flat=True))
    ids = {r["id"] for r in rows}
    fixed = []
    for r in sorted(rows, key=lambda x: x["id"]):  # والد (ارتقا) پیش از فرزند (بازگشت) درج شود
        r = dict(r)
        if r.get("started_by_id") not in users:
            r["started_by_id"] = None
        if r.get("parent_id") not in ids:
            r["parent_id"] = None
        fixed.append(r)
    _restore_table(UpdateRun, fixed)


def _db_restore(backend, info: dict, holder: str, run: UpdateRun):
    """دیتابیس را از پشتیبانِ تأییدشده برمی‌گرداند؛ تاریخچه و قفل جاری حفظ می‌شود (حتی اگر شمای جدول‌های updater در پشتیبان فرق داشته باشد)."""
    d = backups.verify_backup(info)
    safe_save(run)
    snap = _history_snapshot()
    lock_rows = list(UpdateLock.objects.filter(holder=holder).values())
    backend.restore(d / info["db_file"])
    _restore_table(UpdateLock, [])  # قفل‌ها به ردیف‌های تاریخچه ارجاع دارند؛ اول خالی، بعد تاریخچه، بعد قفل جاری
    _history_restore(snap)
    _restore_table(UpdateLock, lock_rows)
    from .middleware import reset_cache

    reset_cache()


# ------------------------------------------------------------------ ارتقای هسته
def apply_core(version: str | None = None, user=None, *, source=None, runner=None, db_backend=None, root: Path | None = None,
               dry_run: bool = False) -> UpdateRun:
    root = Path(root or versions.app_root())
    src = source or sources.get_source()
    runner = runner or SubprocessRunner(root)
    index = src.index()
    entry = pick_entry(index["core"], version)
    run = _new_run("core", "", UpdateRun.Kind.UPGRADE, entry["version"], user, src.ref)
    work = None
    try:
        with update_lock("prepare-core", run) as holder:
            UpdateLock.objects.filter(holder=holder).update(run=run)
            run.migrations_before = migration_state()
            step(run, "index", True, (gettext("Target version %(entry)s") % {"entry": entry['version']}))
            problems, _w = compat(entry["version"], entry["requires"])
            if problems:
                raise UpdateBlocked((gettext("Incompatible: %(problems)s") % {"problems": "؛ ".join(problems)}))
            step(run, "compat_index", True)
            if core_blocked_by_plugins():
                raise UpdateBlocked(gettext("A plugin install/upgrade is in progress; try again after it finishes."))
            _check_disk(root, entry["size"] or 0)
            data = fetch_verified(src, "core", "", entry, run)
            work = _work_dir()
            tree = package.extract(data, work / "pkg")
            ex = package.validate_core(tree, entry["version"])
            step(run, "validate", True, (gettext("%(files)s files") % {"files": ex.files}))
            req = dict(ex.release.get("requires") or {})
            req_lines = (tree / "requirements.txt").read_text(encoding="utf-8").splitlines()
            req["packages"] = [*(req.get("packages") or []), *req_lines]
            problems, warnings = compat(entry["version"], req)
            if problems:
                raise UpdateBlocked((gettext("Incompatible with the new package: %(problems)s") % {"problems": "؛ ".join(problems)}))
            step(run, "compat", True, gettext("; ").join(warnings))
            if dry_run:
                step(run, "dry_run", True, gettext("Dry run; no files were changed"))
                run.recovery = gettext("Dry run: the package was downloaded and fully validated but no files were changed.")
                finish(run, UpdateRun.Status.SUCCEEDED)
                return run
            _apply_core_files(run, holder, root, tree, work, runner, db_backend)
        return run
    except UpdateError as exc:
        if run.status == UpdateRun.Status.RUNNING:
            finish(run, UpdateRun.Status.FAILED, str(exc))
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("ارتقای هسته با خطای غیرمنتظره شکست خورد")
        if run.status == UpdateRun.Status.RUNNING:
            finish(run, UpdateRun.Status.FAILED, (gettext("Unexpected error: %(exc)s\n%(format_exc)s") % {"exc": exc, "format_exc": traceback.format_exc()}))
        raise UpdateError(gettext("An unexpected error occurred; the details were recorded in the update history.")) from exc
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)


def core_blocked_by_plugins() -> bool:
    from apps.plugins.models import PluginOperationLock

    return PluginOperationLock.objects.filter(acquired_at__gte=timezone.now() - timedelta(minutes=15)).exists()


def _apply_core_files(run, holder, root: Path, tree: Path, work: Path, runner, db_backend):
    backend = db_backend or backups.default_db_backend()
    run.backup = backups.create_backup(f"core-{run.from_version}-to-{run.to_version}", root, backend)
    run.recovery = recovery_text(run)
    step(run, "backup", True, run.backup["dir"])
    backups.verify_backup(run.backup)
    step(run, "backup_verify", True)
    _set_lock(holder, "apply-core")  # از این‌جا حالت تعمیر (۵۰۳) فعال است
    old_dir = work / "old"
    swapped = False
    db_touched = False
    try:
        step(run, "swap", True, gettext("Started replacing files"))
        swap_entries(root, tree, package.CORE_ENTRIES, old_dir)
        swapped = True
        disk = versions.disk_version(root)
        if disk != run.to_version:
            raise UpdateError((gettext("After replacement, the version on disk (%(disk)s) does not match the target version.") % {"disk": disk}))
        step(run, "swap", True, gettext("Files replaced"))
        runner.check()
        step(run, "code_check", True)
        db_touched = True
        out = runner.migrate()
        run.migration_output = sanitize(out, 6000)
        after = migration_state()
        run.migrations_applied = sorted(set(after) - set(run.migrations_before))
        step(run, "migrate", True, (gettext("%(migrations_applied_count)s new migrations") % {"migrations_applied_count": len(run.migrations_applied)}))
        runner.check()
        step(run, "verify", True)
    except BaseException as exc:  # noqa: BLE001 - حتی KeyboardInterrupt باید بازگردانده شود
        err = sanitize(str(exc) if isinstance(exc, UpdateError) else f"{type(exc).__name__}: {exc}")
        step(run, "failed", False, err)
        _auto_rollback(run, holder, root, old_dir, work, backend, swapped, db_touched, err)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise UpdateError((gettext("The upgrade failed (%(status)s): %(err)s") % {"status": run.get_status_display(), "err": err})) from None
    shutil.rmtree(old_dir, ignore_errors=True)
    run.restart_required = True
    run.recovery = gettext("The upgrade succeeded. Restart the web service to load the new code. If there is a problem: `python manage.py ravix_update rollback --run %d`.") % run.pk
    finish(run, UpdateRun.Status.SUCCEEDED)
    try:
        backups.prune(settings.RAVIX_UPDATE_BACKUPS_KEEP)
    except OSError:
        pass
    _clear_caches()


def _clear_caches():
    try:
        from apps.themes import registry

        registry.clear_cache()
    except Exception:  # noqa: BLE001
        pass


def _auto_rollback(run, holder, root, old_dir, work, backend, swapped, db_touched, err):
    problems = []
    if db_touched:
        try:
            _db_restore(backend, run.backup, holder, run)
            step(run, "rollback_db", True, gettext("The database was restored from backup"))
        except Exception as exc:  # noqa: BLE001
            problems.append((gettext("Database restore: %(sanitize)s") % {"sanitize": sanitize(exc)}))
            step(run, "rollback_db", False, str(exc))
    done_files = False
    if swapped or old_dir.exists():
        try:
            entries = [e for e in package.CORE_ENTRIES if (old_dir / e).exists() or (root / e).exists()]
            for e in entries:
                cur = root / e
                if (old_dir / e).exists():
                    if cur.is_dir() and not cur.is_symlink():
                        shutil.rmtree(cur, ignore_errors=True)
                    elif cur.exists():
                        cur.unlink()
                    cur.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(old_dir / e, cur)
            done_files = True
        except Exception as exc:  # noqa: BLE001
            problems.append((gettext("Restoring files from the set-aside version: %(sanitize)s") % {"sanitize": sanitize(exc)}))
    if swapped and not done_files:
        try:  # راه دوم: آرشیو پشتیبان
            d = backups.verify_backup(run.backup)
            tmp = backups.extract_archive(d / run.backup["files_archive"], work / "restore")
            swap_entries(root, tmp, package.CORE_ENTRIES, work / "old2", remove_missing=True)
            done_files = True
        except Exception as exc:  # noqa: BLE001
            problems.append((gettext("Restoring files from the backup archive: %(sanitize)s") % {"sanitize": sanitize(exc)}))
    ok = not problems and (versions.disk_version(root) == run.from_version)
    step(run, "rollback_files", ok, gettext("The previous version is on disk") if ok else gettext("; ").join(problems) or gettext("The version on disk could not be verified"))
    if ok:
        run.recovery = gettext("The upgrade failed and the site was rolled back to the previous version (files and database). The cause is given in the error section.")
        finish(run, UpdateRun.Status.ROLLED_BACK, err)
    else:
        run.recovery = recovery_text(run)
        finish(run, UpdateRun.Status.NEEDS_RECOVERY, err + "\n" + "\n".join(problems))
    _clear_caches()


# ------------------------------------------------------------------ بازگشت دستی به نسخهٔ قبل
def rollback_run(run_id: int, user=None, *, confirm_data_loss: bool = False, runner=None, db_backend=None, root: Path | None = None) -> UpdateRun:
    """هستهٔ سایت را به وضعیت پیش از یک ارتقای موفق برمی‌گرداند (فایل‌ها + دیتابیس). داده‌های ثبت‌شده پس از ارتقا از بین می‌روند."""
    root = Path(root or versions.app_root())
    parent = UpdateRun.objects.filter(pk=run_id, component="core", kind="upgrade").first()
    if parent is None:
        raise UpdateBlocked(gettext("The core upgrade run was not found."))
    if parent.status not in (UpdateRun.Status.SUCCEEDED, UpdateRun.Status.NEEDS_RECOVERY):
        raise UpdateBlocked(gettext("Only a successful upgrade (or one needing recovery) can be rolled back."))
    if not parent.backup.get("dir"):
        raise UpdateBlocked(gettext("No backup was recorded for this upgrade."))
    if not confirm_data_loss:
        raise UpdateBlocked(gettext("Rolling back restores the database to the moment before the upgrade and destroys data created after it; explicit confirmation is required."))
    backend = db_backend or backups.default_db_backend()
    run = _new_run("core", "", UpdateRun.Kind.ROLLBACK, parent.from_version, user, f"run#{parent.pk}", parent=parent)
    run.from_version = versions.disk_version(root) or parent.to_version
    safe_save(run)
    work = None
    try:
        with update_lock("prepare-rollback", run) as holder:
            UpdateLock.objects.filter(holder=holder).update(run=run)
            info = parent.backup
            backups.verify_backup(info)
            step(run, "backup_verify", True, info["dir"])
            disk = versions.disk_version(root)
            if disk != parent.to_version:
                raise UpdateBlocked((gettext("The code on disk (%(disk)s) is not the upgraded version (%(to_version)s); an automatic rollback is not safe.") % {"disk": disk, "to_version": parent.to_version}))
            run.backup = backups.create_backup(f"pre-rollback-{parent.pk}", root, backend)  # خودِ بازگشت هم قابل‌بازیابی بماند
            run.recovery = recovery_text(run)
            step(run, "backup", True, run.backup["dir"])
            work = _work_dir()
            _set_lock(holder, "apply-rollback")
            try:
                d = backups.backup_dir() / info["dir"]
                tmp = backups.extract_archive(d / info["files_archive"], work / "restore")
                swap_entries(root, tmp, package.CORE_ENTRIES, work / "old", remove_missing=True)
                step(run, "restore_files", True)
                _db_restore(backend, info, holder, run)
                step(run, "restore_db", True)
                if versions.disk_version(root) != parent.from_version:
                    raise UpdateError(gettext("After the rollback, the version on disk could not be verified."))
            except Exception as exc:  # noqa: BLE001
                err = sanitize(exc)
                step(run, "failed", False, err)
                run.recovery = recovery_text(run)
                finish(run, UpdateRun.Status.NEEDS_RECOVERY, err)
                raise UpdateError((gettext("The rollback failed; manual recovery required: %(err)s") % {"err": err})) from None
            Parent = UpdateRun.objects.filter(pk=parent.pk)
            Parent.update(status=UpdateRun.Status.REVERTED)
            run.restart_required = True
            run.recovery = gettext("Rollback completed. Restart the web service.")
            finish(run, UpdateRun.Status.SUCCEEDED)
            _clear_caches()
        return run
    except UpdateError:
        if run.status == UpdateRun.Status.RUNNING:
            finish(run, UpdateRun.Status.FAILED, "")
        raise
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ قالب
def apply_theme(slug: str, version: str | None = None, user=None, *, source=None, root_dirs=None, dry_run=False) -> UpdateRun:
    if slug == "default":
        raise UpdateBlocked(gettext("The default theme is updated together with the core package."))
    from apps.themes import registry
    from apps.themes.manifest import inspect_theme_dir

    src = source or sources.get_source()
    index = src.index()
    entries = index["themes"].get(slug)
    if not entries:
        raise UpdateBlocked((gettext("Theme “%(slug)s” is not in the update source.") % {"slug": slug}))
    installed = registry.get_themes(refresh=True).get(slug)
    if installed is None:
        raise UpdateBlocked((gettext("Theme “%(slug)s” is not installed (new themes are not installed this way).") % {"slug": slug}))
    if version:
        entry = next((x for x in entries if x["version"] == version), None)
        if entry is None:
            raise UpdateBlocked((gettext("Version %(version)s of theme “%(slug)s” is not in the source.") % {"version": version, "slug": slug}))
    else:
        cand = [x for x in entries if versions.newer(x["version"], installed.version)
                and (not read_legacy_key(x["requires"]) or versions.satisfies(versions.running_version(), read_legacy_key(x["requires"])))]
        if not cand:
            raise UpdateBlocked(gettext("There is no compatible new version for this theme."))
        entry = max(cand, key=lambda x: versions.parse(x["version"]) or (0, 0, 0))
    run = _new_run("theme", slug, UpdateRun.Kind.UPGRADE, entry["version"], user, src.ref)
    run.from_version = installed.version
    safe_save(run)
    work = None
    try:
        with update_lock(f"prepare-theme:{slug}", run) as holder:
            UpdateLock.objects.filter(holder=holder).update(run=run)
            if not versions.newer(entry["version"], installed.version):
                raise UpdateBlocked((gettext("Version %(entry)s is not newer than the installed version (%(version)s).") % {"entry": entry['version'], "version": installed.version}))
            problems, _w = compat(entry["version"], entry["requires"], component="theme")
            if problems:
                raise UpdateBlocked((gettext("Incompatible: %(problems)s") % {"problems": "؛ ".join(problems)}))
            data = fetch_verified(src, "theme", slug, entry, run)
            work = _work_dir()
            tree = package.extract(data, work / "pkg")
            ex = package.validate_theme(tree, slug, entry["version"])
            ravin_spec = getattr(inspect_theme_dir(tree), "ravix", "")
            if ravin_spec and not versions.satisfies(versions.running_version(), ravin_spec):
                raise UpdateBlocked((gettext("The new theme needs Ravix “%(ravin_spec)s”; the current version is %(running_version)s.") % {"ravin_spec": ravin_spec, "running_version": versions.running_version()}))
            step(run, "validate", True, (gettext("%(files)s files") % {"files": ex.files}))
            if dry_run:
                step(run, "dry_run", True, gettext("Dry run; no files were changed"))
                run.recovery = gettext("Dry run: the package was downloaded and fully validated but no files were changed.")
                finish(run, UpdateRun.Status.SUCCEEDED)
                return run
            target = Path(installed.path)
            run.backup = backups.create_dir_backup(f"theme-{slug}-{installed.version}", target)
            run.recovery = recovery_text(run)
            step(run, "backup", True, run.backup["dir"])
            backups.verify_backup(run.backup)
            _set_lock(holder, f"apply-theme:{slug}")
            old = work / "old"
            old.mkdir()
            try:
                os.replace(target, old / slug)
                try:
                    shutil.move(str(tree), str(target))
                except BaseException:
                    shutil.rmtree(target, ignore_errors=True)
                    os.replace(old / slug, target)
                    raise
                registry.clear_cache()
                info = inspect_theme_dir(target)
                if not info.valid or info.version != entry["version"]:
                    raise UpdateError((gettext("The theme could not be verified after replacement: %(items)s") % {"items": "؛ ".join(info.errors[:3])}))
                step(run, "verify", True)
            except BaseException as exc:  # noqa: BLE001
                err = sanitize(exc)
                step(run, "failed", False, err)
                ok = False
                try:
                    if (old / slug).exists():
                        shutil.rmtree(target, ignore_errors=True)
                        os.replace(old / slug, target)
                    registry.clear_cache()
                    ok = inspect_theme_dir(target).version == run.from_version
                except Exception as e2:  # noqa: BLE001
                    err += (gettext("\nRestore: %(sanitize)s") % {"sanitize": sanitize(e2)})
                step(run, "rollback_files", ok, "")
                run.recovery = gettext("The theme was rolled back to the previous version.") if ok else recovery_text(run)
                finish(run, UpdateRun.Status.ROLLED_BACK if ok else UpdateRun.Status.NEEDS_RECOVERY, err)
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                raise UpdateError((gettext("The theme upgrade failed (%(status)s): %(err)s") % {"status": run.get_status_display(), "err": err})) from None
            run.restart_required = True
            run.recovery = gettext("The theme was updated. Restart the service to apply it in all processes.")
            finish(run, UpdateRun.Status.SUCCEEDED)
            backups.prune(settings.RAVIX_UPDATE_BACKUPS_KEEP)
        return run
    except UpdateError:
        if run.status == UpdateRun.Status.RUNNING:
            finish(run, UpdateRun.Status.FAILED, "")
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("ارتقای قالب با خطای غیرمنتظره شکست خورد")
        if run.status == UpdateRun.Status.RUNNING:
            finish(run, UpdateRun.Status.FAILED, (gettext("Unexpected error: %(exc)s") % {"exc": exc}))
        raise UpdateError(gettext("An unexpected error occurred; the details were recorded in the history.")) from exc
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ افزونه (واگذار به نصب‌کنندهٔ موجود)
def apply_plugin(slug: str, version: str | None, user) -> UpdateRun:
    """ارتقای افزونه: همهٔ بررسی‌ها (SHA/امضا/سازگاری/پشتیبان/قفل/بازگشت) را نصب‌کنندهٔ افزونه‌ها انجام می‌دهد؛ این‌جا فقط در تاریخچهٔ یکپارچه ثبت می‌شود."""
    from apps.plugins import installer
    from apps.plugins.services import PluginError

    if core_update_active():
        raise UpdateBusy(gettext("A core/theme upgrade is in progress; try again after it finishes."))
    run = _new_run("plugin", slug, UpdateRun.Kind.UPGRADE, version or "", user, "plugin-registry")
    try:
        result = installer.install_from_registry(slug, version, user, upgrade_existing=True)
    except (PluginError, UpdateError) as exc:
        step(run, "installer", False, str(exc))
        run.recovery = gettext("The previous version of the plugin is untouched; see the “Plugin report” for details.")
        finish(run, UpdateRun.Status.FAILED, str(exc))
        raise UpdateError(str(exc)) from None
    new_v = next((p["version"] for p in versions.installed_plugins() if p["slug"] == slug), version or "")
    run.to_version = new_v
    step(run, "installer", True, f"{result[0]}")
    run.recovery = gettext("The new code was put in place; the plugin is disabled. After reviewing the changes, enable it on the plugin page (rolling back from that page is possible).")
    finish(run, UpdateRun.Status.SUCCEEDED)
    return run
