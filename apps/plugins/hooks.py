"""اجرای hookهای افزونه‌ها با مهار خطا. خروجی هر hook پیش از استفاده توسط هسته پاک‌سازی (sanitize) می‌شود."""
import logging

from . import events, registry, state
from .hooks_registry import HOOKS

logger = logging.getLogger("ravix.plugins")

SEO_KEYS = {"title", "description", "canonical", "robots", "og_type", "og_image"}
ROBOTS = {"index,follow", "noindex,follow", "noindex,nofollow", "index,nofollow"}
OG_TYPES = {"website", "article"}


def _iter_handlers(name):
    for slug, info in state.active_plugins().items():
        if not any(h["name"] == name for h in info.manifest.hooks):
            continue
        loaded = registry.get(slug)
        if loaded is None:
            continue
        for _prio, fn in loaded.hooks.get(name, []):
            yield loaded, fn


def sanitize_seo(original: dict, result) -> dict:
    """فقط کلیدهای مجاز و مقادیر معتبر پذیرفته می‌شود؛ هر چیز دیگر نادیده گرفته می‌شود (افزونه نمی‌تواند چیز خطرناکی در <head> بگذارد)."""
    from apps.builder.schema import is_safe_url

    if not isinstance(result, dict):
        return original
    out = dict(original)
    for key in SEO_KEYS & set(result):
        value = result[key]
        if value is None or not isinstance(value, str):
            continue
        value = " ".join(value.split())
        if key == "title" and value:
            out[key] = value[:120]
        elif key == "description":
            out[key] = value[:320]
        elif key in ("canonical", "og_image"):
            if value == "" or (is_safe_url(value) and (value.startswith("/") or value.startswith("https://") or value.startswith("http://"))):
                out[key] = value[:500]
        elif key == "robots" and value in ROBOTS:
            out[key] = value
        elif key == "og_type" and value in OG_TYPES:
            out[key] = value
    return out


COMMENT_STATUSES = {"pending", "spam"}  # افزونه فقط می‌تواند سخت‌گیرتر کند؛ تأیید خودکار ممنوع است


def sanitize_comment(original: dict, result) -> dict:
    """از خروجی hook فقط status (pending یا spam) پذیرفته می‌شود؛ افزونه نمی‌تواند متن، نویسنده یا تأییدشدن را تغییر دهد."""
    out = dict(original)
    if isinstance(result, dict) and result.get("status") in COMMENT_STATUSES:
        out["status"] = result["status"]
    return out


_SANITIZERS = {"public.seo": sanitize_seo, "comments.before_save": sanitize_comment}


def apply_filter(name, value, **ctx):
    """hookهای فیلتر را به‌ترتیب اولویت اجرا می‌کند. خطای افزونه ثبت می‌شود و مقدار قبلی حفظ می‌شود."""
    if name not in HOOKS or HOOKS[name] != "filter" or not state.active_plugins():
        return value
    from apps.core.modules import hook_allowed

    if not hook_allowed(name):  # a switched-off built-in module owns this hook
        return value
    for loaded, fn in _iter_handlers(name):
        try:
            new = fn(value, loaded.api, **ctx)
        except Exception as exc:  # noqa: BLE001
            events.record_error(loaded.slug, f"hook {name}", exc)
            continue
        value = _SANITIZERS[name](value, new) if name in _SANITIZERS else new
    return value


def run_action(name, **ctx):
    if name not in HOOKS or HOOKS[name] != "action" or not state.active_plugins():
        return
    from apps.core.modules import hook_allowed

    if not hook_allowed(name):
        return
    for loaded, fn in _iter_handlers(name):
        try:
            fn(loaded.api, **ctx)
        except Exception as exc:  # noqa: BLE001
            events.record_error(loaded.slug, f"hook {name}", exc)
