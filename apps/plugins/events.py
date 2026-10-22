"""ثبت رویداد/خطای افزونه‌ها و قطع‌کنندهٔ مدار (غیرفعال‌سازی خودکار). این توابع هرگز استثنا به بیرون نمی‌دهند."""
import logging
import traceback
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("ravin.plugins")


def log_event(slug, kind, message, *, level="info", detail="", actor=None):
    from .models import PluginEvent

    try:
        return PluginEvent.objects.create(slug=slug[:40], level=level, kind=kind[:30], message=(message or "")[:400],
                                          detail=(detail or "")[:6000], actor=actor if getattr(actor, "pk", None) else None)
    except Exception:  # noqa: BLE001 - ثبت گزارش نباید خودش خطا بسازد
        logger.exception("ثبت رویداد افزونه %s ناموفق بود", slug)
        return None


def disable(slug, *, kind, message, level="error", detail="", auto=True):
    """افزونه را غیرفعال و رویداد را ثبت می‌کند (بدون نیاز به کاربر؛ برای قطع‌کنندهٔ مدار و تشخیص دستکاری)."""
    from .models import PluginRecord

    try:
        PluginRecord.objects.filter(slug=slug).update(is_active=False, auto_disabled=auto)
    except Exception:  # noqa: BLE001
        logger.exception("غیرفعال‌سازی افزونه %s ناموفق بود", slug)
    from . import state

    state.invalidate()
    log_event(slug, kind, message, level=level, detail=detail)


def record_error(slug, where, exc):
    """خطای افزونه را ثبت می‌کند؛ اگر در بازهٔ مشخص از حد مجاز بگذرد افزونه خودکار غیرفعال می‌شود. True یعنی غیرفعال شد."""
    from .models import PluginEvent

    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-5500:]
    logger.error("خطای افزونه %s (%s): %r", slug, where, exc)
    log_event(slug, "error", f"{where}: {type(exc).__name__}: {exc}"[:400], level="error", detail=detail)
    try:
        limit = int(getattr(settings, "RAVIN_PLUGIN_ERROR_LIMIT", 5))
        window = int(getattr(settings, "RAVIN_PLUGIN_ERROR_WINDOW", 600))
        since = timezone.now() - timedelta(seconds=window)
        recent = PluginEvent.objects.filter(slug=slug, kind="error", created_at__gte=since).count()
        if limit > 0 and recent >= limit:
            disable(slug, kind="auto_disable", message=f"افزونه به‌دلیل {recent} خطا در {window} ثانیه به‌طور خودکار غیرفعال شد.")
            return True
    except Exception:  # noqa: BLE001
        logger.exception("بررسی قطع‌کنندهٔ مدار افزونه %s ناموفق بود", slug)
    return False
