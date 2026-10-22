"""حالت تعمیر: فقط در لحظهٔ جایگزینی فایل‌ها و مهاجرت (عملیات «apply») پاسخ ۵۰۳ با Retry-After می‌دهد؛ صفحهٔ وضعیت به‌روزرسانی و /healthz آزادند."""
import time

from django.http import HttpResponse

_cache = {"at": 0.0, "on": False}
EXEMPT = ("/healthz", "/panel/updates/", "/static/")


def maintenance_active() -> bool:
    now = time.monotonic()
    if now - _cache["at"] > 1.0:  # حداکثر یک پرس‌وجو در ثانیه برای هر پروسه
        from django.db import DatabaseError

        from .models import UpdateLock

        try:
            _cache["on"] = UpdateLock.objects.filter(operation__startswith="apply").exists()
        except DatabaseError:
            _cache["on"] = False  # جدول نیست/دیتابیس در حال تعویض: مزاحم درخواست‌ها نشو
        _cache["at"] = now
    return _cache["on"]


def reset_cache():
    _cache["at"] = 0.0


class MaintenanceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith(EXEMPT) and maintenance_active():
            message = "سایت در حال به‌روزرسانی است؛ چند لحظهٔ دیگر دوباره تلاش کنید."
            if "text/html" in request.headers.get("Accept", "text/html") or "*/*" in request.headers.get("Accept", ""):
                from apps.core.errorpages import standalone_response

                return standalone_response(request, 503, "سایت در حال تعمیر است", message, icon="🛠️", refresh=30,
                                           headers={"Retry-After": "30"})
            return HttpResponse(message, status=503, headers={"Retry-After": "30"}, content_type="text/plain; charset=utf-8")
        return self.get_response(request)
