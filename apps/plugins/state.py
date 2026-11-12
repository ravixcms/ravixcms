"""
وضعیت فعال‌بودن افزونه‌ها (کش کوتاه‌مدت در حافظهٔ هر پردازه). همهٔ دروازه‌ها (منو، مجوز، مسیرها، hookها، قالب‌ها) از همین‌جا می‌پرسند؛
پس حالت امن/خاموش‌بودن سیستم افزونه در یک نقطه اعمال می‌شود. خطای دیتابیس هرگز به بیرون نشت نمی‌کند.
"""
import threading
import time
from dataclasses import dataclass

from django.utils.translation import gettext_lazy
from django.conf import settings
from django.db import DatabaseError

from apps.accounts.permissions import Role

from .manifest import ManifestError, Manifest, parse_manifest
from .settings_schema import clean_all

TTL = 3.0
_lock = threading.Lock()
_cache = {"at": 0.0, "data": None}


@dataclass(frozen=True)
class ActiveInfo:
    slug: str
    manifest: Manifest
    fingerprint: str
    config: dict

    def setting(self, key, default=None):
        return self.config.get(key, default)


def enabled() -> bool:
    return bool(getattr(settings, "RAVIX_PLUGINS_ENABLED", True)) and not getattr(settings, "RAVIX_PLUGINS_SAFE_MODE", False)


def invalidate(*_args, **_kwargs):
    with _lock:
        _cache["data"] = None


invalidate_signal = invalidate


def _load() -> dict[str, ActiveInfo]:
    from .models import PluginRecord

    data = {}
    try:
        rows = list(PluginRecord.objects.filter(is_active=True))
    except DatabaseError:  # پیش از migrate یا خطای اتصال
        return data
    for row in rows:
        try:
            manifest = parse_manifest(row.manifest, expected_slug=row.slug)
        except ManifestError:
            continue  # snapshot خراب: افزونه فعال حساب نمی‌شود
        config, _errors = clean_all(manifest.settings, row.config or {})
        data[row.slug] = ActiveInfo(slug=row.slug, manifest=manifest, fingerprint=row.fingerprint, config=config)
    from . import registry

    registry.ensure_catalogs(data)  # menu and page titles are translated before any plugin code runs
    return data


def active_plugins() -> dict[str, ActiveInfo]:
    if not enabled():
        return {}
    now = time.monotonic()
    with _lock:
        if _cache["data"] is not None and now - _cache["at"] < TTL:
            return _cache["data"]
    data = _load()
    with _lock:
        _cache["data"], _cache["at"] = data, now
    return data


def get_active(slug: str) -> ActiveInfo | None:
    return active_plugins().get(slug)


# ------------------------------------------------------------------ مجوزها و منو
def plugin_capability_granted(role: str, capability: str) -> bool:
    """مجوز اعلام‌شدهٔ یک افزونهٔ فعال فقط به مدیر و مدیرکل داده می‌شود؛ هرگز به نقش‌های پایین‌تر و هرگز برای مجوزهای هسته."""
    if "." not in capability or role not in (Role.ADMIN, Role.SUPER_ADMIN):
        return False
    slug = capability.split(".", 1)[0]
    info = active_plugins().get(slug)
    return info is not None and capability in info.manifest.capabilities


def nav_items():
    """آیتم‌های منوی افزونه‌های فعال (فقط صفحاتی که menu=true دارند)."""
    from django.urls import reverse

    from apps.core.navigation import NavItem

    items = []
    for slug, info in active_plugins().items():
        for page in info.manifest.admin_pages:
            if not page["menu"]:
                continue
            url = reverse("plugins:page", args=[slug, page["key"]])
            items.append(NavItem(key=f"plugin.{slug}.{page['key']}", label=gettext_lazy(page["title"]), url_name="", url=url, icon=page["icon"],
                                 section=page["section"], order=page["order"], capability=page["capability"]))
    return items


def find_site_route(path: str, language_prefix: bool):
    """``(slug, route)`` of the active plugin that declared this site route, or ``None``. Cheap enough for every public request."""
    wanted = (path or "").strip("/")
    if not wanted:
        return None
    for slug, info in active_plugins().items():
        for route in info.manifest.site_routes:
            if route["language_prefix"] is language_prefix and route["path"].strip("/") == wanted:
                return slug, route
    return None
