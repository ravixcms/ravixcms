from django.db import connection
from django.http import JsonResponse


def healthz(request):
    """بررسی سلامت برای مانیتورینگ/لود بالانسر: برنامه بالا است و دیتابیس پاسخ می‌دهد. اطلاعات داخلی نشان نمی‌دهد."""
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
    except Exception:  # noqa: BLE001
        return JsonResponse({"status": "error"}, status=503)
