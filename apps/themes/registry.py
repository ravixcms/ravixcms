"""کشف و نگهداری فهرست قالب‌های نصب‌شده (از پوشه‌های ``settings.RAVIX_THEME_DIRS``)."""
from django.utils.translation import gettext
import logging
import os
import threading
from pathlib import Path

from django.conf import settings
from django.core.signals import setting_changed
from django.dispatch import receiver

from .constants import DEFAULT_THEME
from .engine import clear_engines
from .manifest import ThemeInfo, inspect_theme_dir

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_cache: dict[str, ThemeInfo] | None = None
_cache_stamp: int | None = None


def _stamp() -> int | None:
    """مهر نسخهٔ نصب‌ها: نصب‌کنندهٔ ZIP پس از هر نصب این فایل را به‌روز می‌کند تا همهٔ workerها کش خود را نو کنند (یک stat ارزان)."""
    try:
        return os.stat(Path(settings.RAVIX_THEME_INSTALL_DIR) / ".revision").st_mtime_ns
    except (OSError, AttributeError):
        return None


def _discover() -> dict[str, ThemeInfo]:
    found: dict[str, ThemeInfo] = {}
    for base in getattr(settings, "RAVIX_THEME_DIRS", []):
        base = Path(base)
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if not child.is_dir() or child.name.startswith((".", "_")):
                continue
            if child.name in found:
                logger.warning("قالب تکراری «%s» در %s نادیده گرفته شد.", child.name, base)
                continue
            info = inspect_theme_dir(child)
            if not info.valid:
                logger.warning("قالب «%s» نامعتبر است: %s", child.name, gettext("; ").join(info.errors))
            found[child.name] = info
    return found


def get_themes(refresh: bool = False) -> dict[str, ThemeInfo]:
    global _cache, _cache_stamp
    with _lock:
        stamp = _stamp()
        stale = _cache is not None and stamp != _cache_stamp
        if _cache is None or refresh or stale or settings.DEBUG:
            if refresh or stale:
                clear_engines()
            _cache_stamp = stamp
            _cache = _discover()
            from apps.core import catalogs

            catalogs.sync_themes(_cache.values())  # locale/<lang>.json of every valid theme
        return dict(_cache)


def get_theme(slug: str) -> ThemeInfo | None:
    return get_themes().get(slug)


def get_default_theme() -> ThemeInfo | None:
    return get_theme(DEFAULT_THEME)


def clear_cache() -> None:
    global _cache, _cache_stamp
    with _lock:
        _cache = None
        _cache_stamp = None
    clear_engines()


@receiver(setting_changed)
def _reset_on_setting_change(sender, setting, **kwargs):
    if setting in {"RAVIX_THEME_DIRS", "RAVIX_THEME_INSTALL_DIR", "DEBUG"}:
        clear_cache()
