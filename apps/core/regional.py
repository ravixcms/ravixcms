"""زبان و منطقهٔ زمانی سایت (از SiteSettings؛ در نصب‌کنندهٔ وب انتخاب می‌شود).

TIME_ZONE در settings ثابت است؛ منطقهٔ انتخابی مدیر در هر درخواست توسط ``SiteRegionalMiddleware`` فعال می‌شود
(ذخیرهٔ تاریخ‌ها همچنان UTC است). ردیف SiteSettings یک‌بار در هر درخواست خوانده و روی ``request.site_settings`` نگه داشته می‌شود؛
context processor همان را به قالب‌ها می‌دهد، پس برای صفحه‌های HTML کوئری اضافه‌ای نیست و بدون کش (بدون خطر مقدار کهنه) است.
"""
import re
from zoneinfo import ZoneInfo, available_timezones

from django.conf import settings
from django.db import DatabaseError

_FALLBACK_ZONES = ["Asia/Tehran", "Asia/Kabul", "Asia/Dubai", "Asia/Baghdad", "Asia/Istanbul", "Europe/London",
                   "Europe/Berlin", "America/New_York", "UTC"]


def timezone_choices():
    names = sorted(available_timezones()) or _FALLBACK_ZONES
    if "UTC" not in names:
        names.append("UTC")
    return [(n, n) for n in names]


def language_choices():
    """زبان‌های موجود = settings.LANGUAGES (فعلاً فقط فارسی؛ افزودن زبان تازه یعنی افزودن ترجمه و ورودی در LANGUAGES)."""
    return list(settings.LANGUAGES)


def valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except Exception:  # noqa: BLE001  (ZoneInfoNotFoundError، ValueError، OSError)
        return False
    return True


def load_site_settings(request):
    """SiteSettings این درخواست (یک‌بار خوانده می‌شود). قبل از migrate (خطای دیتابیس) None برمی‌گرداند."""
    cached = getattr(request, "site_settings", None)
    if cached is not None:
        return cached
    from .models import SiteSettings

    try:
        site = SiteSettings.load()
    except DatabaseError:
        return None
    try:
        request.site_settings = site
    except AttributeError:  # شیء درخواست ساختگی (آزمون)
        pass
    return site


def regional_for(site) -> tuple[str, str]:
    """(language, timezone) معتبر؛ مقدار نامعتبر/ناموجود به پیش‌فرض settings برمی‌گردد."""
    language = getattr(site, "language", "") or settings.LANGUAGE_CODE
    tz = getattr(site, "timezone", "") or settings.TIME_ZONE
    if language not in dict(language_choices()):
        language = settings.LANGUAGE_CODE
    if not valid_timezone(tz):
        tz = settings.TIME_ZONE
    return language, tz


# ---------------------------------------------------------------------------------------------------------------------
# UI language selection
# ---------------------------------------------------------------------------------------------------------------------
def normalize_language(code) -> str:
    """"pt_BR" / "PT-br" → "pt-br"; unknown or unsupported codes → "" (callers fall back)."""
    code = str(code or "").strip().replace("_", "-").lower()
    return code if code in dict(language_choices()) else ""


_PREFIX_RE = re.compile(r"^([A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*)(?=/|$)")


def split_language_prefix(path: str):
    """``/de/blog/`` → ("de", "/blog/"); a path without a supported language prefix → ("", path). Works on a path_info
    (no script prefix)."""
    m = _PREFIX_RE.match(path.lstrip("/")) if path.startswith("/") else None
    if m:
        code = normalize_language(m.group(1))
        if code:
            return code, "/" + path.lstrip("/")[m.end():].lstrip("/") if path.lstrip("/")[m.end():] else "/"
    return "", path


def swap_language_prefix(path: str, code: str):
    """Rewrite a *full* URL path (script prefix included, as produced by ``reverse``) to language ``code``.
    Returns None when the path carries no language prefix (i.e. is not a localized public URL)."""
    from django.urls import get_script_prefix

    script = get_script_prefix()
    if not path.startswith(script):
        return None
    current, rest = split_language_prefix("/" + path[len(script):])
    if not current:
        return None
    return f"{script}{code}{rest}"


def resolve_language(request, site) -> str:
    """Language for this request: a public URL prefix (``/de/...``) wins; otherwise the visitor's cookie, then the
    signed-in user's profile, then the site default (SiteSettings.language), then settings.LANGUAGE_CODE."""
    from_path = split_language_prefix(request.path_info)[0]
    if from_path:
        return from_path
    return resolve_preferred_language(request, site)[0]


def resolve_preferred_language(request, site):
    """(language, source) ignoring the URL; source is "cookie", "profile" or "default"."""
    chosen = normalize_language(request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME))
    if chosen:
        return chosen, "cookie"
    if request.COOKIES.get(settings.SESSION_COOKIE_NAME):  # only consult the user (a session lookup) when there is a session
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            chosen = normalize_language(getattr(user, "language", ""))
            if chosen:
                return chosen, "profile"
    return regional_for(site)[0], "default"


def set_language_cookie(response, code: str) -> None:
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME, code, max_age=settings.LANGUAGE_COOKIE_AGE, path=settings.LANGUAGE_COOKIE_PATH,
        domain=settings.LANGUAGE_COOKIE_DOMAIN, secure=settings.LANGUAGE_COOKIE_SECURE, httponly=settings.LANGUAGE_COOKIE_HTTPONLY,
        samesite=settings.LANGUAGE_COOKIE_SAMESITE)


# ---------------------------------------------------------------------------------------------------------------------
# Multilingual manifest text (theme.json / plugin.json «name» and «description»)
# ---------------------------------------------------------------------------------------------------------------------
def parse_localized_text(value, key, errors, *, max_len, required=False):
    """Read a manifest text that is either a plain string or ``{"fa": "…", "en": "…"}``.

    Returns ``(default, translations)``: ``default`` is the plain string (or the English text, else the first one) and is what
    old code and any language without its own entry see; ``translations`` is ``{language code: text}`` (empty for a plain string).
    Problems are appended to ``errors`` (as translated messages).
    """
    from django.utils.translation import gettext

    if value is None or value == "":
        if required:
            errors.append(gettext("%(key)s: required.") % {"key": key})
        return "", {}
    if isinstance(value, str):
        text = value.strip()
        if len(text) > max_len:
            errors.append(gettext("%(key)s: at most %(max_len)s characters.") % {"key": key, "max_len": max_len})
        if required and not text:
            errors.append(gettext("%(key)s: required.") % {"key": key})
        return text, {}
    if not isinstance(value, dict) or not value:
        errors.append(gettext("%(key)s: must be text or an object {language code: text}.") % {"key": key})
        return "", {}
    translations: dict[str, str] = {}
    for code, text in value.items():
        lang = normalize_language(code)
        if not lang:
            errors.append(gettext("%(key)s: unsupported language code “%(code)s”.") % {"key": key, "code": code})
            continue
        if not isinstance(text, str) or not text.strip():
            errors.append(gettext("%(key)s.%(code)s: must be non-empty text.") % {"key": key, "code": code})
            continue
        text = text.strip()
        if len(text) > max_len:
            errors.append(gettext("%(key)s.%(code)s: at most %(max_len)s characters.") % {"key": key, "code": code, "max_len": max_len})
        translations[lang] = text
    if not translations:
        return "", {}
    default = translations.get("en") or next(iter(translations.values()))
    return default, translations


def pick_localized(default: str, translations: dict, language: str | None = None) -> str:
    """The text in ``language`` (default: the active one), else the default."""
    if not translations:
        return default
    if language is None:
        from django.utils.translation import get_language

        language = get_language() or ""
    language = normalize_language(language) or ""
    return translations.get(language) or translations.get(language.split("-")[0]) or default
