"""هدرهای امنیتی سراسری: CSP برای پاسخ‌هایی که خودشان CSP ندارند (پنل، حساب‌ها، API) و Permissions-Policy.

صفحات عمومی CSP اختصاصی خودشان را می‌گذارند (apps/public/rendering.py) و بازنویسی نمی‌شود.
"""
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
        csp = getattr(settings, "RAVIN_CSP", DEFAULT_CSP)
        if csp and "Content-Security-Policy" not in response:
            name = "Content-Security-Policy-Report-Only" if getattr(settings, "RAVIN_CSP_REPORT_ONLY", False) else "Content-Security-Policy"
            response[name] = csp
        response.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        return response
