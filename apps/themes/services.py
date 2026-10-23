"""منطق انتشار و پیش‌نویس ظاهر سایت. تمام تغییرات فقط روی رکورد ``ThemeConfig`` انجام می‌شود."""
from django.utils.translation import gettext, gettext_lazy
import copy

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.audit.services import log_event

from . import registry
from .colors import palette_errors
from .config import sanitize
from .constants import DEFAULT_THEME
from .models import ThemeConfig

AUDIT_TYPE = gettext_lazy("Site appearance")


class ThemeError(Exception):
    """خطای قابل نمایش به مدیر (پیام فارسی)."""


def published_doc(cfg: ThemeConfig) -> dict:
    return sanitize({**(cfg.published if isinstance(cfg.published, dict) else {}), "theme": cfg.active_theme})


def working_doc(cfg: ThemeConfig) -> dict:
    """سندی که ویرایشگر روی آن کار می‌کند: پیش‌نویس در صورت وجود، وگرنه نسخه منتشرشده."""
    if cfg.draft is not None:
        return sanitize(cfg.draft)
    return published_doc(cfg)


def _locked() -> ThemeConfig:
    ThemeConfig.load()
    return ThemeConfig.objects.select_for_update().get(pk=1)


def _usable_theme(slug: str):
    theme = registry.get_theme(slug)
    if theme is None:
        raise ThemeError(gettext("This theme was not found. Its folder must exist in the themes path."))
    if not theme.valid:
        raise ThemeError((gettext("This theme is invalid and cannot be used: %(items)s") % {"items": gettext("; ").join(theme.errors[:3])}))
    return theme


def _drop_draft_if_unchanged(cfg: ThemeConfig) -> None:
    if cfg.draft is not None and sanitize(cfg.draft) == published_doc(cfg):
        cfg.draft = None
        cfg.draft_updated_at = None


def save_draft_part(request, key: str, value) -> None:
    with transaction.atomic():
        cfg = _locked()
        doc = working_doc(cfg)
        if key not in doc or key == "theme":
            raise ThemeError(gettext("Invalid section."))
        doc[key] = value
        cfg.draft = sanitize(doc)
        cfg.draft_updated_at = timezone.now()
        _drop_draft_if_unchanged(cfg)
        cfg.save()
    labels = {"appearance": gettext("Appearance"), "header": gettext("Header"), "footer": gettext("Footer"), "menus": gettext("Menus"), "home": gettext("Home page")}
    log_event(request, AuditLog.Action.UPDATE, None, (gettext("The site appearance draft was saved (section %(labels)s).") % {"labels": labels.get(key, key)}), object_type=AUDIT_TYPE)


def set_draft_theme(request, slug: str) -> None:
    """قالب را فقط در پیش‌نویس تغییر می‌دهد (برای پیش‌نمایش قبل از فعال‌سازی)."""
    theme = _usable_theme(slug)
    with transaction.atomic():
        cfg = _locked()
        doc = working_doc(cfg)
        doc["theme"] = slug
        cfg.draft = sanitize(doc)
        cfg.draft_updated_at = timezone.now()
        _drop_draft_if_unchanged(cfg)
        cfg.save()
    log_event(request, AuditLog.Action.UPDATE, None, (gettext("Preview of theme “%(display_name)s” started.") % {"display_name": theme.display_name}), object_type=AUDIT_TYPE)


def discard_draft(request) -> None:
    with transaction.atomic():
        cfg = _locked()
        if cfg.draft is None:
            raise ThemeError(gettext("There is no draft to discard."))
        cfg.draft = None
        cfg.draft_updated_at = None
        cfg.save()
    log_event(request, AuditLog.Action.UPDATE, None, gettext("The site appearance draft changes were discarded."), object_type=AUDIT_TYPE)


def publish_draft(request) -> None:
    with transaction.atomic():
        cfg = _locked()
        if cfg.draft is None:
            raise ThemeError(gettext("There are no unpublished changes."))
        raw = cfg.draft if isinstance(cfg.draft, dict) else {}
        appearance = raw.get("appearance") if isinstance(raw.get("appearance"), dict) else {}
        doc = sanitize(raw)
        a = doc["appearance"]
        if (appearance.get("primary"), appearance.get("background"), appearance.get("text")) != (a["primary"], a["background"], a["text"]) or palette_errors(
            a["primary"], a["background"], a["text"]
        ):
            raise ThemeError(gettext("The draft colors are not valid or readable; first fix the “Appearance” section."))
        theme = _usable_theme(doc["theme"])
        cfg.published = doc
        cfg.active_theme = doc["theme"]
        cfg.draft = None
        cfg.draft_updated_at = None
        cfg.published_at = timezone.now()
        cfg.published_by = request.user if request.user.is_authenticated else None
        cfg.save()
    log_event(request, AuditLog.Action.PUBLISH, None, (gettext("The site appearance changes were published (theme “%(display_name)s”).") % {"display_name": theme.display_name}), object_type=AUDIT_TYPE)


def activate(request, slug: str) -> None:
    theme = _usable_theme(slug)
    with transaction.atomic():
        cfg = _locked()
        if cfg.active_theme == slug:
            raise ThemeError(gettext("This theme is already active."))
        cfg.active_theme = slug
        published = copy.deepcopy(cfg.published) if isinstance(cfg.published, dict) else {}
        published["theme"] = slug
        cfg.published = published
        if cfg.draft is not None:
            draft = copy.deepcopy(cfg.draft)
            draft["theme"] = slug
            cfg.draft = draft
            _drop_draft_if_unchanged(cfg)
        cfg.published_at = timezone.now()
        cfg.published_by = request.user if request.user.is_authenticated else None
        cfg.save()
    log_event(request, AuditLog.Action.PUBLISH, None, (gettext("Theme “%(display_name)s” was activated.") % {"display_name": theme.display_name}), object_type=AUDIT_TYPE)


def deactivate(request, slug: str) -> None:
    """غیرفعال‌سازی قالب فعال = بازگشت به قالب پیش‌فرض (سایت همیشه باید یک قالب فعال داشته باشد)."""
    if slug == DEFAULT_THEME:
        raise ThemeError(gettext("The default theme cannot be deactivated; to change the appearance, activate another theme."))
    with transaction.atomic():
        cfg = _locked()
        if cfg.active_theme != slug:
            raise ThemeError(gettext("This theme is not active."))
    activate(request, DEFAULT_THEME)
