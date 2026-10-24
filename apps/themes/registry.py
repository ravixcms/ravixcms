"""کشف و نگهداری فهرست قالب‌های نصب‌شده (از پوشه‌های ``settings.RAVIX_THEME_DIRS``)."""
from apps.core.clilang import cli_gettext
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


_shadowed: list[tuple[str, Path, Path]] = []  # (slug, ignored folder, folder that won) from the last discovery


def shadowed_themes() -> list[tuple[str, Path, Path]]:
    """Themes whose ID is also used by an earlier folder (the earlier one wins; these are unused and can be deleted)."""
    get_themes()
    return list(_shadowed)


def _discover() -> dict[str, ThemeInfo]:
    found: dict[str, ThemeInfo] = {}
    shadowed: list[tuple[str, Path, Path]] = []
    for base in getattr(settings, "RAVIX_THEME_DIRS", []):
        base = Path(base)
        try:
            if not base.is_dir():
                continue
            children = sorted(base.iterdir())
        except OSError:
            from apps.core import fsaccess

            logger.warning("%s", fsaccess.unreadable_detail(base))
            continue
        for child in children:
            try:
                if not child.is_dir() or child.name.startswith((".", "_")):
                    continue
            except OSError:
                continue
            if child.name in found:
                logger.warning(cli_gettext("Duplicate theme “%s” in %s was ignored."), child.name, base)
                shadowed.append((child.name, child, found[child.name].path))
                continue
            info = inspect_theme_dir(child)
            if not info.valid:
                logger.warning(cli_gettext("The theme “%s” is invalid: %s"), child.name, gettext("; ").join(info.errors))
            found[child.name] = info
    _shadowed[:] = shadowed
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
