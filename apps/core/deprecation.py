"""هشدارهای منسوخ‌شدن (deprecation) برای نام‌های قدیمی «ravin» که تا نسخهٔ 2.0 با نام‌های «ravix» هم‌چنان کار می‌کنند.

``FutureWarning`` عمداً انتخاب شده است: برخلاف ``DeprecationWarning`` به‌صورت پیش‌فرض در stderr (journald) دیده می‌شود.
این ماژول نباید Django یا مدل‌ها را import کند (در ابتدای بارگذاری تنظیمات استفاده می‌شود).
"""
import warnings

from apps.core.clilang import cli_gettext

REMOVAL_VERSION = "2.0"


class RavixDeprecationWarning(FutureWarning):
    """نام قدیمی (ravin) هنوز کار می‌کند ولی در نسخهٔ 2.0 حذف می‌شود."""


def warn_legacy(old: str, new: str, *, stacklevel: int = 3) -> None:
    warnings.warn(
        cli_gettext("“%(old)s” is deprecated and will be removed in Ravix %(version)s; use “%(new)s” instead (docs/upgrade.md).")
        % {"old": old, "version": REMOVAL_VERSION, "new": new},
        RavixDeprecationWarning, stacklevel=stacklevel,
    )


def legacy_name(name: str) -> str:
    """RAVIX_FOO ← RAVIN_FOO (نام قدیمی متغیر محیطی)."""
    return "RAVIN_" + name[len("RAVIX_"):]


LEGACY_ENV_USED: list[str] = []  # نام‌های قدیمی RAVIN_* که در این اجرا استفاده شد (ravix_preflight آن‌ها را گزارش می‌کند)


def getenv(name: str):
    """مقدار متغیر محیطی. برای RAVIX_* اگر تنظیم نشده باشد، نام قدیمی RAVIN_* (منسوخ؛ حذف در 2.0) خوانده می‌شود."""
    import os

    value = os.environ.get(name)
    if value is None and name.startswith("RAVIX_"):
        old = legacy_name(name)
        value = os.environ.get(old)
        if value is not None and old not in LEGACY_ENV_USED:
            LEGACY_ENV_USED.append(old)
            warn_legacy(old, name, stacklevel=4)
    return value


def upgrade_legacy_key(data: dict, where: str, new: str = "ravix", old: str = "ravin") -> dict:
    """manifest/فهرست قدیمی کلید «ravin» دارد. اگر «ravix» نبود، مقدار قدیمی به کلید جدید منتقل می‌شود (نسخهٔ کپی؛ ورودی تغییر نمی‌کند)."""
    if isinstance(data, dict) and new not in data and old in data:
        warn_legacy(cli_gettext("%(where)s: key “%(key)s”") % {"where": where, "key": old}, cli_gettext("key “%(key)s”") % {"key": new}, stacklevel=3)
        data = dict(data)
        data[new] = data.pop(old)  # کلید قدیمی حذف می‌شود تا بررسی «کلید ناشناخته» نخورد
    return data


def read_legacy_key(data: dict, new: str = "ravix", old: str = "ravin", default=""):
    """مقدار کلید جدید، و در نبودش کلید قدیمی (برای داده‌های ذخیره‌شدهٔ قدیمی مثل manifest در دیتابیس)."""
    if not isinstance(data, dict):
        return default
    return data.get(new) or data.get(old) or default


def legacy_library(register, old: str, new: str):
    """کپی «register» (django.template.Library) که هنگام اولین *استفادهٔ واقعی* از هر تگ/فیلتر هشدار منسوخ‌شدن می‌دهد.

    ماژول‌های templatetags هنگام راه‌اندازی import می‌شوند، پس هشدار در import معنا ندارد؛ این‌جا تابع‌ها پوشانده می‌شوند.
    """
    import functools

    from django import template

    lib = template.Library()
    warned = []

    def wrap(func):
        @functools.wraps(func)
        def inner(*args, **kwargs):
            if not warned:
                warned.append(1)
                warn_legacy(f"{{% load {old} %}}", f"{{% load {new} %}}", stacklevel=3)
            return func(*args, **kwargs)
        return inner

    for name, func in register.tags.items():
        lib.tags[name] = wrap(func)
    for name, func in register.filters.items():
        lib.filters[name] = wrap(func)
    return lib
