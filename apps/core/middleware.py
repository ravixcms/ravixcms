"""هدرهای امنیتی سراسری: CSP برای پاسخ‌هایی که خودشان CSP ندارند (پنل، حساب‌ها، API) و Permissions-Policy.

صفحات عمومی CSP اختصاصی خودشان را می‌گذارند (apps/public/rendering.py) و بازنویسی نمی‌شود.
"""
from django.utils.translation import gettext
from django.conf import settings

DEFAULT_CSP = (
    "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; script-src 'self'; font-src 'self' data:; "
    "media-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
)
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        csp = getattr(settings, "RAVIX_CSP", DEFAULT_CSP)
        if csp and "Content-Security-Policy" not in response:
            name = "Content-Security-Policy-Report-Only" if getattr(settings, "RAVIX_CSP_REPORT_ONLY", False) else "Content-Security-Policy"
            response[name] = csp
        response.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        return response


class SiteRegionalMiddleware:
    """زبان و منطقهٔ زمانی سایت (SiteSettings) را برای این درخواست فعال می‌کند؛ ورودی/نمایش تاریخ‌ها در پنل با همین منطقه است.
    خطا (مثلاً قبل از migrate) هرگز درخواست را نمی‌شکند."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from zoneinfo import ZoneInfo

        from django.utils import timezone, translation

        from django.utils.cache import patch_vary_headers

        from .regional import load_site_settings, regional_for, resolve_language

        site = load_site_settings(request)
        tz = regional_for(site)[1]
        try:
            language = resolve_language(request, site)
        except Exception:  # noqa: BLE001 — a broken session/user lookup must not break the request
            language = regional_for(site)[0]
        try:
            timezone.activate(ZoneInfo(tz))
            translation.activate(language)
            request.LANGUAGE_CODE = translation.get_language()
        except Exception:  # noqa: BLE001
            timezone.deactivate()
        try:
            response = self.get_response(request)
            patch_vary_headers(response, ("Cookie",))  # the language depends on the language cookie / session
            return response
        finally:
            timezone.deactivate()
            translation.deactivate()


class NumericOverflowMiddleware:
    """عدد بزرگ‌تر از بازهٔ INTEGER دیتابیس در query/POST (مثلاً ``?author=99999999999999999999`` در فیلترهای خود django-admin)
    ۵۰۰ نمی‌شود؛ ۴۰۰ فارسی برمی‌گردد. فقط OverflowError و DataError «out of range» را می‌گیرد، نه هر خطای دیگر."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        from django.db import DataError

        overflow = isinstance(exception, OverflowError) or (isinstance(exception, DataError) and "out of range" in str(exception).lower())
        if not overflow:
            return None
        from .errorpages import standalone_response

        return standalone_response(request, 400, gettext("Invalid request"), gettext("One of the numeric values in the request is too large. Open the page without that value."), icon="⚠️")
