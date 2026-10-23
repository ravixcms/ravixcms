"""ریدایرکت مسیرهای قدیمی: فقط وقتی پاسخ ۴۰۴ است و فقط برای GET/HEAD؛ صفحهٔ موجود هرگز تحت‌تأثیر قرار نمی‌گیرد."""
from django.http import HttpResponseRedirect
from django.utils.encoding import iri_to_uri

from .models import RESERVED_REDIRECT_PREFIXES, Redirect


class RedirectFallbackMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.status_code != 404 or request.method not in ("GET", "HEAD"):
            return response
        path = request.path  # رمزگشایی‌شده، مثل old_path ذخیره‌شده
        if path.startswith(RESERVED_REDIRECT_PREFIXES):
            return response
        from apps.core.regional import split_language_prefix

        lang, bare = split_language_prefix(request.path_info)  # «/en/old/» رد «/old/» را هم پیدا می‌کند
        variants = {path, path.rstrip("/") or "/", path.rstrip("/") + "/"}
        if lang:
            variants |= {bare, bare.rstrip("/") or "/", bare.rstrip("/") + "/"}
        try:
            found = Redirect.objects.filter(old_path__in=variants).first()
        except Exception:  # noqa: BLE001 — خرابی دیتابیس/جدول نباید ۴۰۴ را به ۵۰۰ تبدیل کند
            return response
        if found is None:
            return response
        target = found.new_path
        if target.startswith("/"):
            if target.split("?", 1)[0].rstrip("/") in (path.rstrip("/"), bare.rstrip("/")):
                return response  # حلقهٔ مستقیم
            if not target.startswith("//") and split_language_prefix(target)[0] == "":
                from django.urls import is_valid_path
                from django.utils import translation

                code = lang or translation.get_language()
                if is_valid_path(f"/{code}{target.split('?', 1)[0]}"):
                    target = f"/{code}{target}"  # the visitor stays in their language
            query = request.META.get("QUERY_STRING", "")
            if query and "?" not in target:
                target = f"{target}?{query}"
        redirect_response = HttpResponseRedirect(iri_to_uri(target))
        redirect_response.status_code = found.status_code
        if found.status_code == 302:
            redirect_response["Cache-Control"] = "no-store"
        return redirect_response
