"""صفحه‌های خطای مستقل (۵۰۰ و حالت تعمیر).

عمداً به دیتابیس، context processor، کش و فایل‌های static وابسته نیستند: وقتی همین‌ها خراب‌اند باید باز هم یک صفحهٔ فارسی تمیز دیده شود.
"""
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils.translation import get_language, get_language_bidi

from . import localefmt

_FALLBACK = ('<!doctype html><html lang="{lang}" dir="{direction}"><meta charset="utf-8"><title>{title}</title>'
             '<body style="font-family:Tahoma,sans-serif;text-align:center;padding:4rem"><h1>{title}</h1><p>{message}</p></body></html>')


def standalone_response(request, status: int, title: str, message: str, *, icon: str = "⚠️", refresh: int | None = None,
                        headers: dict | None = None) -> HttpResponse:
    try:
        html = render_to_string("errors/standalone.html", {"code": localefmt.digits(status), "title": title, "message": message,
                                                           "icon": icon, "refresh": refresh})
    except Exception:  # noqa: BLE001 — حتی خطای قالب هم نباید ۵۰۰ را به صفحهٔ خالی تبدیل کند
        html = _FALLBACK.format(title=title, message=message, lang=get_language() or "en", direction="rtl" if get_language_bidi() else "ltr")
    response = HttpResponse(html, status=status, content_type="text/html; charset=utf-8")
    for key, value in (headers or {}).items():
        response[key] = value
    response["Cache-Control"] = "no-store"
    return response
