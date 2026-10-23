"""زبان و منطقهٔ زمانی سایت (از SiteSettings؛ در نصب‌کنندهٔ وب انتخاب می‌شود).

TIME_ZONE در settings ثابت است؛ منطقهٔ انتخابی مدیر در هر درخواست توسط ``SiteRegionalMiddleware`` فعال می‌شود
(ذخیرهٔ تاریخ‌ها همچنان UTC است). ردیف SiteSettings یک‌بار در هر درخواست خوانده و روی ``request.site_settings`` نگه داشته می‌شود؛
context processor همان را به قالب‌ها می‌دهد، پس برای صفحه‌های HTML کوئری اضافه‌ای نیست و بدون کش (بدون خطر مقدار کهنه) است.
"""
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


def resolve_language(request, site) -> str:
    """Language for this request: the visitor's cookie, then the signed-in user's profile, then the site default
    (SiteSettings.language), then settings.LANGUAGE_CODE. (A public URL prefix, when present, wins over all of these.)"""
    chosen = normalize_language(request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME))
    if chosen:
        return chosen
    if request.COOKIES.get(settings.SESSION_COOKIE_NAME):  # only consult the user (a session lookup) when there is a session
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            chosen = normalize_language(getattr(user, "language", ""))
            if chosen:
                return chosen
    return regional_for(site)[0]


def set_language_cookie(response, code: str) -> None:
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME, code, max_age=settings.LANGUAGE_COOKIE_AGE, path=settings.LANGUAGE_COOKIE_PATH,
        domain=settings.LANGUAGE_COOKIE_DOMAIN, secure=settings.LANGUAGE_COOKIE_SECURE, httponly=settings.LANGUAGE_COOKIE_HTTPONLY,
        samesite=settings.LANGUAGE_COOKIE_SAMESITE)
