"""
عملیات مدیریتی افزونه‌ها: نصب، فعال‌سازی، غیرفعال‌سازی، حذف، تأیید به‌روزرسانی، تنظیمات، سازگاری و مهاجرت دیتابیس.

هر عملیات تغییردهنده دوباره مجوز ``plugins.manage`` را بررسی می‌کند (دفاع در عمق؛ ویوها هم همین را اعمال می‌کنند) و در پایان
رویداد ثبت می‌کند. «تأیید» یعنی مدیر اثرانگشت دقیق همان کدی را که دیده است می‌فرستد؛ اگر کد بین نمایش و تأیید تغییر کند، رد می‌شود.
"""
from django.utils.translation import gettext, gettext_lazy
import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.management import call_command
from django.db import connection, transaction

from apps.audit.models import AuditLog
from apps.audit.services import log_event as audit_log
from apps.core.version import PLUGIN_API_VERSION, RAVIX_VERSION

from . import discovery, events, registry, state, versions
from .integrity import IntegrityError, fingerprint
from .manifest import Manifest, ManifestError, parse_manifest
from .models import PluginRecord
from .settings_schema import clean_all

logger = logging.getLogger("ravix.plugins")

ALLOWED_MIGRATION_OPS = {
    "CreateModel", "DeleteModel", "AddField", "RemoveField", "AlterField", "RenameField", "AlterModelOptions", "AddIndex",
    "RemoveIndex", "AddConstraint", "RemoveConstraint", "RenameIndex", "AlterUniqueTogether", "AlterIndexTogether",
}


class PluginError(Exception):
    """خطای قابل‌نمایش به مدیر (پیام فارسی)."""


class SettingsInvalid(Exception):
    def __init__(self, errors: dict):
        self.errors = errors
        super().__init__(gettext("The settings are invalid."))


@dataclass
class Row:
    slug: str
    name: str
    version: str
    description: str
    status: str  # available|invalid|incompatible|inactive|active|update_pending|missing|auto_disabled
    status_label: str
    problems: list
    manifest: Manifest | None = None
    record: PluginRecord | None = None
    path: object = None

    @property
    def installed(self):
        return self.record is not None


STATUS_LABELS = {
    "available": gettext_lazy("Ready to install"), "invalid": gettext_lazy("Invalid"), "incompatible": gettext_lazy("Incompatible"), "inactive": gettext_lazy("Installed, inactive"), "active": gettext_lazy("Active"),
    "update_pending": gettext_lazy("Code changed; approval required"), "missing": gettext_lazy("Folder not found"), "auto_disabled": gettext_lazy("Automatically disabled"),
}


def _require_admin(user):
    if user is None or not getattr(user, "is_authenticated", False) or not user.has_cap("plugins.manage"):
        raise PluginError(gettext("Only an administrator may do this."))


# ------------------------------------------------------------------ سازگاری
def check_compat(manifest: Manifest) -> list[str]:
    problems = []
    if manifest.api_version != PLUGIN_API_VERSION:
        problems.append((gettext("The plugin contract is version %(api_version)s; this CMS supports version %(PLUGIN_API_VERSION)s.") % {"api_version": manifest.api_version, "PLUGIN_API_VERSION": PLUGIN_API_VERSION}))
    if not versions.satisfies(RAVIX_VERSION, manifest.ravix):
        problems.append((gettext("Requires Ravix version “%(ravix)s”; the current version is %(RAVIX_VERSION)s.") % {"ravix": manifest.ravix, "RAVIX_VERSION": RAVIX_VERSION}))
    for dep, spec in manifest.requires.items():
        rec = PluginRecord.objects.filter(slug=dep).first()
        if rec is None or not rec.is_active:
            problems.append((gettext("Depends on plugin “%(dep)s”, which is not installed and active.") % {"dep": dep}))
        elif not versions.satisfies(rec.version, spec):
            problems.append((gettext("Plugin “%(dep)s” must be “%(spec)s”; the installed version is %(version)s.") % {"dep": dep, "spec": spec, "version": rec.version}))
    return problems


# ------------------------------------------------------------------ فهرست
def overview() -> list[Row]:
    records = {r.slug: r for r in PluginRecord.objects.all()}
    rows, seen = [], set()
    for item in discovery.discover():
        seen.add(item.slug)
        rec = records.get(item.slug)
        m = item.manifest
        problems = list(item.errors)
        if m is not None and not problems:
            problems = check_compat(m) if rec is None else []
        if rec is None:
            status = "invalid" if item.errors or m is None else ("incompatible" if problems else "available")
        else:
            try:
                changed = fingerprint(item.path) != rec.fingerprint
            except IntegrityError:
                changed = True
            if changed or item.errors:
                status = "update_pending"
            elif rec.is_active:
                status = "active"
            else:
                status = "auto_disabled" if rec.auto_disabled else "inactive"
        rows.append(Row(slug=item.slug, name=(m.name if m else (rec.name if rec else item.slug)), version=(m.version if m else (rec.version if rec else "")),
                        description=m.description if m else "", status=status, status_label=STATUS_LABELS[status], problems=problems,
                        manifest=m, record=rec, path=item.path))
    for slug, rec in records.items():
        if slug not in seen:
            rows.append(Row(slug=slug, name=rec.name, version=rec.version, description="", status="missing", status_label=STATUS_LABELS["missing"],
                            problems=[gettext("The plugin folder was not found on disk.")], record=rec))
    order = {"active": 0, "update_pending": 1, "auto_disabled": 2, "inactive": 3, "available": 4, "incompatible": 5, "invalid": 6, "missing": 7}
    return sorted(rows, key=lambda r: (order[r.status], r.name))


def get_row(slug) -> Row | None:
    return next((r for r in overview() if r.slug == slug), None)


# ------------------------------------------------------------------ مهاجرت
def _validate_migrations(label):
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(None, ignore_no_migrations=True)
    user_model = settings.AUTH_USER_MODEL.lower()
    for (app_label, name), migration in loader.disk_migrations.items():
        if app_label != label:
            continue
        for dep_app, _dep_name in migration.dependencies:
            if dep_app not in (label, "accounts"):
                raise PluginError((gettext("Migration %(name)s: a dependency on “%(dep_app)s” is not allowed.") % {"name": name, "dep_app": dep_app}))
        for op in migration.operations:
            op_name = type(op).__name__
            if op_name not in ALLOWED_MIGRATION_OPS:
                raise PluginError((gettext("Migration %(name)s: the operation %(op_name)s is not allowed (RunPython/RunSQL are forbidden).") % {"name": name, "op_name": op_name}))
            fields = list(getattr(op, "fields", [])) if op_name == "CreateModel" else ([("", op.field)] if hasattr(op, "field") else [])
            for _fname, field in fields:
                rel = getattr(getattr(field, "remote_field", None), "model", None)
                if rel is None:
                    continue
                target = (rel if isinstance(rel, str) else rel._meta.label_lower).lower()
                if not (target.startswith(label + ".") or target == user_model):
                    raise PluginError((gettext("Migration %(name)s: a relation to “%(target)s” is not allowed.") % {"name": name, "target": target}))
            if op_name == "CreateModel":
                table = (op.options or {}).get("db_table")
                if table and not table.startswith(label + "_"):
                    raise PluginError((gettext("Migration %(name)s: the table name must start with “%(label)s_”.") % {"name": name, "label": label}))


def run_migrations(slug, root, fp, *, target=None):
    """مهاجرت‌های افزونه را (پس از اعتبارسنجی ایستا) اجرا می‌کند. فقط جدول‌های plugin_<slug>_* را لمس می‌کنند."""
    try:
        registry.load_for_install(slug, root, fp)
    except Exception as exc:  # noqa: BLE001
        raise PluginError((gettext("Loading the plugin models failed: %(exc)s") % {"exc": exc}))
    label = f"plugin_{slug}"
    _validate_migrations(label)
    if connection.in_atomic_block and connection.vendor == "sqlite":
        raise PluginError(gettext("The migration cannot run inside a transaction on SQLite."))
    try:
        args = [label] + ([target] if target else [])
        call_command("migrate", *args, interactive=False, verbosity=0)
    except Exception as exc:  # noqa: BLE001
        raise PluginError((gettext("The database migration failed: %(exc)s") % {"exc": exc}))


# ------------------------------------------------------------------ چرخهٔ عمر
def _audit(request, message, slug):
    try:
        audit_log(request, AuditLog.Action.UPDATE, None, message, object_type=(gettext("Plugin %(slug)s") % {"slug": slug}))
    except Exception:  # noqa: BLE001
        logger.exception("ثبت audit ناموفق بود")


def _prepare(slug, approved_fp):
    item = discovery.find(slug)
    if item is None:
        raise PluginError(gettext("Plugin not found."))
    if not item.valid:
        raise PluginError((gettext("The plugin is not valid: %(errors)s") % {"errors": "؛ ".join(item.errors)}))
    try:
        fp = fingerprint(item.path)
    except IntegrityError as exc:
        raise PluginError(str(exc))
    if not approved_fp or fp != approved_fp:
        raise PluginError(gettext("The plugin code has changed since you reviewed it; reopen the page and review again."))
    return item, fp


def install(slug, user, approved_fp, request=None):
    _require_admin(user)
    if PluginRecord.objects.filter(slug=slug).exists():
        raise PluginError(gettext("This plugin is already installed."))
    item, fp = _prepare(slug, approved_fp)
    m = item.manifest
    problems = check_compat(m)
    if problems:
        raise PluginError((gettext("Incompatible: %(problems)s") % {"problems": "؛ ".join(problems)}))
    config, _ = clean_all(m.settings, {})
    rec = PluginRecord.objects.create(slug=slug, name=m.name, version=m.version, fingerprint=fp, manifest=m.to_json(), is_active=False,
                                      config=config, installed_by=user)
    if m.models:
        try:
            run_migrations(slug, item.path, fp)
        except PluginError as exc:
            rec.delete()
            events.log_event(slug, "install_failed", str(exc), level="error", actor=user)
            raise
    events.log_event(slug, "install", (gettext("Installed (version %(version)s, fingerprint %(fp)s).") % {"version": m.version, "fp": fp[:12]}), actor=user)
    _audit(request, (gettext("Install plugin “%(name)s”") % {"name": m.name}), slug)
    return rec


def activate(slug, user, request=None):
    _require_admin(user)
    rec = PluginRecord.objects.filter(slug=slug).first()
    if rec is None:
        raise PluginError(gettext("The plugin is not installed."))
    item = discovery.find(slug)
    if item is None or not item.valid:
        raise PluginError(gettext("The plugin folder was not found or is invalid."))
    if fingerprint(item.path) != rec.fingerprint:
        raise PluginError(gettext("The plugin code has changed from the approved version; review and approve the changes first."))
    problems = check_compat(item.manifest)
    if problems:
        raise PluginError((gettext("Incompatible: %(problems)s") % {"problems": "؛ ".join(problems)}))
    rec.is_active, rec.auto_disabled = True, False
    rec.save(update_fields=["is_active", "auto_disabled", "updated_at"])
    state.invalidate()
    if registry.get(slug) is None:  # بارگذاری آزمایشی؛ در صورت خطا بازگردانده می‌شود (علت در گزارش ثبت شده)
        PluginRecord.objects.filter(slug=slug).update(is_active=False)
        state.invalidate()
        raise PluginError(gettext("The plugin failed to load and was not enabled; details are in the “Plugin log”."))
    events.log_event(slug, "activate", gettext("Enabled."), actor=user)
    _audit(request, (gettext("Enable plugin “%(name)s”") % {"name": rec.name}), slug)
    return rec


def deactivate(slug, user, request=None):
    _require_admin(user)
    rec = PluginRecord.objects.filter(slug=slug).first()
    if rec is None:
        raise PluginError(gettext("The plugin is not installed."))
    dependents = [r.name for r in PluginRecord.objects.filter(is_active=True).exclude(slug=slug) if slug in (r.manifest.get("requires") or {})]
    if dependents:
        raise PluginError((gettext("The following active plugins depend on it: %(dependents)s") % {"dependents": "، ".join(dependents)}))
    rec.is_active = False
    rec.save(update_fields=["is_active", "updated_at"])
    registry.unload(slug)
    events.log_event(slug, "deactivate", gettext("Disabled."), actor=user)
    _audit(request, (gettext("Disable plugin “%(name)s”") % {"name": rec.name}), slug)
    return rec


def uninstall(slug, user, *, purge_data=False, remove_files=False, request=None):
    _require_admin(user)
    rec = PluginRecord.objects.filter(slug=slug).first()
    if rec is None:
        raise PluginError(gettext("The plugin is not installed."))
    if rec.is_active:
        raise PluginError(gettext("Disable the plugin first."))
    if any(slug in (r.manifest.get("requires") or {}) for r in PluginRecord.objects.exclude(slug=slug)):
        raise PluginError(gettext("Other installed plugins depend on it."))
    if purge_data and rec.manifest.get("models"):
        item = discovery.find(slug)
        if item is None or not item.valid or fingerprint(item.path) != rec.fingerprint:
            raise PluginError(gettext("To delete the data, the approved plugin code must be in place."))
        run_migrations(slug, item.path, rec.fingerprint, target="zero")
    rec.delete()
    registry.unload(slug)
    events.log_event(slug, "uninstall", (gettext("Removed%(value)s") % {"value": (" (داده‌ها هم پاک شد)." if purge_data else " (داده‌ها نگه داشته شد).")}), actor=user)
    _audit(request, (gettext("Remove plugin “%(name)s”") % {"name": rec.name}), slug)
    if remove_files:
        from . import installer  # import تنبل: installer به services وابسته است

        installer.remove_files(slug, user)


def approve_update(slug, user, approved_fp, request=None):
    """پس از تغییر کد (به‌روزرسانی یا دستکاری)، مدیر اثرانگشت جدید را تأیید می‌کند؛ افزونه تا فعال‌سازی دوباره غیرفعال می‌ماند."""
    _require_admin(user)
    rec = PluginRecord.objects.filter(slug=slug).first()
    if rec is None:
        raise PluginError(gettext("The plugin is not installed."))
    item, fp = _prepare(slug, approved_fp)
    m = item.manifest
    if fp == rec.fingerprint:
        raise PluginError(gettext("There are no changes to approve."))
    was_active = rec.is_active
    rec.is_active = False
    rec.save(update_fields=["is_active"])
    registry.unload(slug)
    problems = check_compat(m)
    if problems:
        raise PluginError((gettext("Incompatible: %(problems)s") % {"problems": "؛ ".join(problems)}))
    old_version = rec.version
    config, _ = clean_all(m.settings, rec.config or {})
    rec.name, rec.version, rec.fingerprint, rec.manifest, rec.config = m.name, m.version, fp, m.to_json(), config
    rec.auto_disabled = False
    rec.save()
    if m.models:
        try:
            run_migrations(slug, item.path, fp)
        except PluginError as exc:
            events.log_event(slug, "update_failed", str(exc), level="error", actor=user)
            raise
    events.log_event(slug, "update_approved", (gettext("Version %(old_version)s → %(version)s approved (fingerprint %(fp)s). Enable it again to use it.") % {"old_version": old_version, "version": m.version, "fp": fp[:12]}), actor=user)
    _audit(request, (gettext("Approve update of plugin “%(name)s” (%(old_version)s → %(version)s)") % {"name": m.name, "old_version": old_version, "version": m.version}), slug)
    return rec, was_active


# ------------------------------------------------------------------ تنظیمات
def save_settings(slug, user, raw: dict, request=None):
    _require_admin(user)
    rec = PluginRecord.objects.filter(slug=slug).first()
    if rec is None:
        raise PluginError(gettext("The plugin is not installed."))
    try:
        manifest = parse_manifest(rec.manifest, expected_slug=slug)
    except ManifestError:
        raise PluginError(gettext("The stored manifest is corrupt."))
    clean, errors = clean_all(manifest.settings, raw)
    if errors:
        raise SettingsInvalid(errors)
    rec.config = clean
    rec.save(update_fields=["config", "updated_at"])
    state.invalidate()
    events.log_event(slug, "settings", gettext("The settings were saved."), actor=user)
    _audit(request, (gettext("Change settings of plugin “%(name)s”") % {"name": rec.name}), slug)
    return clean


def current_settings(rec: PluginRecord):
    manifest = parse_manifest(rec.manifest, expected_slug=rec.slug)
    clean, _errors = clean_all(manifest.settings, rec.config or {})
    return manifest, clean
