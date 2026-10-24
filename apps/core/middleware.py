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


NON_LOCALIZED_PREFIXES = ("/panel/", "/accounts/", "/api/", "/django-admin/", "/static/", "/theme-assets/", "/media/", "/ext/", "/registry/", "/install/",
                          "/healthz", "/jsi18n/", "/i18n/")


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
            if getattr(response, "status_code", None) == 404 and request.method in ("GET", "HEAD"):
                response = self._localize_redirect(request, site, response) or response
            if response is not None:
                patch_vary_headers(response, ("Cookie",))  # the language depends on the language cookie / session
            return response
        finally:
            timezone.deactivate()
            translation.deactivate()


    @staticmethod
    def _localize_redirect(request, site, response):
        """Old unprefixed public URLs (``/blog/x/``) and prefixed ones lacking the trailing slash redirect to the
        language-prefixed URL (``/fa/blog/x/``). The language comes from the visitor's cookie/profile (302, personal)
        or, failing that, the site default (301, but short-lived in caches because the default can change)."""
        from django.conf import settings
        from django.http import HttpResponseRedirect
        from django.urls import get_script_prefix, is_valid_path
        from django.utils.encoding import iri_to_uri

        from .regional import resolve_preferred_language, split_language_prefix

        path = request.path_info
        if path.startswith(NON_LOCALIZED_PREFIXES) or path.startswith(f"/{settings.RAVIX_ADMIN_PATH}/"):
            return None  # system URLs are never language-prefixed (a missing one is just a 404)
        code, rest = split_language_prefix(path)
        source = "url"
        if not code:
            code, source = resolve_preferred_language(request, site)
            rest = path
        if source == "url":  # already prefixed and (as the 404 shows) not valid as is: only a missing slash can help
            fulls = [path + "/"] if settings.APPEND_SLASH and not path.endswith("/") else []
        else:
            fulls = [f"/{code}{rest}"]
            if settings.APPEND_SLASH and not rest.endswith("/"):
                fulls.append(f"/{code}{rest}/")
        for full in fulls:
            if is_valid_path(full):
                target = get_script_prefix().rstrip("/") + full
                query = request.META.get("QUERY_STRING", "")
                if query:
                    target = f"{target}?{query}"
                redirect = HttpResponseRedirect(iri_to_uri(target))
                if source == "default":
                    redirect.status_code = 301
                    redirect["Cache-Control"] = "max-age=3600"
                elif source in ("cookie", "profile"):
                    redirect["Cache-Control"] = "private, no-store"
                else:  # a prefixed URL that only needed its trailing slash
                    redirect.status_code = 301
                return redirect
        return None


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
