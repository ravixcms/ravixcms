"""نسخهٔ هسته (در حال اجرا و روی دیسک)، فهرست نسخهٔ قالب‌ها/افزونه‌ها، و بررسی وابستگی‌ها (requirements) بدون اجرای کد."""
from django.utils.translation import gettext
import re
import sys
from importlib import metadata
from pathlib import Path

from django.conf import settings

from apps.core.version import PLUGIN_API_VERSION, RAVIX_VERSION, THEME_API_VERSION
from apps.plugins import versions as plugin_versions

VERSION_FILE = Path("apps/core/version.py")
from apps.core.deprecation import read_legacy_key
_VER = re.compile(r'^RAVI[XN]_VERSION\s*=\s*["\'](\d{1,4}\.\d{1,4}\.\d{1,4})["\']', re.M)
_REQ = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)$")
_CLAUSE = re.compile(r"^(>=|<=|==|!=|>|<|~=)\s*([0-9][0-9A-Za-z.]*)$")


def app_root() -> Path:
    return Path(settings.RAVIX_UPDATE_APP_ROOT)


def running_version() -> str:
    return RAVIX_VERSION


def version_in_file(path: Path) -> str | None:
    """RAVIX_VERSION را فقط با regex از متن می‌خواند (هرگز import/اجرا نمی‌کند)."""
    try:
        m = _VER.search(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return None
    return m.group(1) if m else None


def disk_version(root: Path | None = None) -> str | None:
    return version_in_file((root or app_root()) / VERSION_FILE)


def restart_pending() -> bool:
    """کد روی دیسک جدیدتر از کدِ در حال اجراست (ارتقا انجام شده ولی سرویس هنوز راه‌اندازی مجدد نشده)."""
    disk = disk_version()
    return bool(disk and disk != running_version())


def parse(v: str):
    return plugin_versions.parse(v)


def newer(a: str, b: str) -> bool:
    return (parse(a) or (0, 0, 0)) > (parse(b) or (0, 0, 0))


def satisfies(version: str, spec: str) -> bool:
    return plugin_versions.satisfies(version, spec)


def _loose(v: str):
    nums = re.findall(r"\d+", v.split("+")[0].split("-")[0])[:4]
    return tuple(int(n) for n in nums) if nums else None


def requirement_problem(line: str) -> str | None:
    """None اگر برآورده است؛ در غیر این صورت پیام. خطوط خالی/کامنت/گزینه‌ها (-r) و نشانگرها (;) نادیده گرفته می‌شوند."""
    line = line.split("#", 1)[0].split(";", 1)[0].strip()
    if not line or line.startswith("-"):
        return None
    m = _REQ.match(line)
    if not m:
        return (gettext("Invalid line in requirements: %(line)s") % {"line": line[:60]})
    name, spec = m.group(1), m.group(2).strip()
    try:
        installed = metadata.version(name)
    except metadata.PackageNotFoundError:
        return (gettext("Package “%(name)s” is not installed (required: %(value)s).") % {"name": name, "value": (spec or gettext("any version"))})
    have = _loose(installed)
    for part in [p.strip() for p in spec.split(",") if p.strip()]:
        c = _CLAUSE.match(part)
        if not c:
            return (gettext("Invalid constraint for “%(name)s”: %(part)s") % {"name": name, "part": part[:30]})
        op, ref = c.group(1), _loose(c.group(2))
        if have is None or ref is None:
            continue
        n = max(len(have), len(ref))
        a, b = have + (0,) * (n - len(have)), ref + (0,) * (n - len(ref))
        ok = {">=": a >= b, "<=": a <= b, "==": a == b, "!=": a != b, ">": a > b, "<": a < b, "~=": a >= b and a[: n - 1] == b[: n - 1]}[op]
        if not ok:
            return (gettext("“%(name)s” is version %(installed)s but %(op)s%(group)s is required.") % {"name": name, "installed": installed, "op": op, "group": c.group(2)})
    return None


def requirements_problems(lines) -> list[str]:
    return [p for p in (requirement_problem(l) for l in lines) if p]


def python_problem(spec: str) -> str | None:
    if not spec:
        return None
    cur = ".".join(map(str, sys.version_info[:3]))
    return None if satisfies(cur, spec) else (gettext("Requires Python “%(spec)s”; current version is %(cur)s.") % {"spec": spec, "cur": cur})


def installed_plugins() -> list[dict]:
    from apps.plugins.models import PluginRecord

    return [{"slug": r.slug, "version": r.version, "active": r.is_active, "ravix": read_legacy_key(r.manifest or {}), "api_version": (r.manifest or {}).get("api_version")}
            for r in PluginRecord.objects.all().order_by("slug")]


def installed_themes() -> list[dict]:
    from apps.themes import registry

    return [{"slug": s, "version": t.version, "valid": t.valid, "ravix": getattr(t, "ravix", ""), "path": str(t.path)} for s, t in sorted(registry.get_themes().items())]


def component_versions() -> dict:
    """نسخهٔ همهٔ اجزا (برای پنل/API): هسته، قراردادها، قالب‌ها، افزونه‌ها و وضعیت شمای دیتابیس."""
    from django.db import connection
    from django.db.migrations.recorder import MigrationRecorder

    schema: dict[str, str] = {}
    try:
        for app, name in sorted(MigrationRecorder(connection).applied_migrations()):
            schema[app] = name
    except Exception:  # noqa: BLE001
        pass
    return {"core": {"running": running_version(), "disk": disk_version(), "restart_pending": restart_pending()},
            "contracts": {"plugin_api": PLUGIN_API_VERSION, "theme_api": THEME_API_VERSION},
            "themes": installed_themes(), "plugins": installed_plugins(), "database_schema": schema,
            "python": ".".join(map(str, sys.version_info[:3]))}
