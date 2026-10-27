"""
رابط برنامه‌نویسی داخلی افزونه‌ها (PluginAPI، نسخهٔ قرارداد ۱).

هر ویو/hook افزونه یک نمونه از این کلاس را می‌گیرد. این رابط **تنها** راه رسمی تعامل با هسته است: خواندن تنظیمات همین افزونه،
ثبت گزارش، رندر در قالب پنل/سایت و بررسی مجوز با همان سیستم هسته (افزونه راهی برای دور زدن مجوزها ندارد).
"""
from django.utils.translation import gettext
import hashlib

from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.safestring import mark_safe

from . import events, state


def _attachment(filename: str) -> str:
    """``attachment`` header with an ASCII fallback name and an RFC 5987 UTF-8 name."""
    from urllib.parse import quote

    ascii_name = "".join(c if 32 < ord(c) < 127 and c not in '"\\;' else "_" for c in filename) or "download"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


class PluginAPI:
    def __init__(self, slug: str):
        self.slug = slug

    # --- اطلاعات و تنظیمات
    @property
    def _info(self):
        return state.get_active(self.slug)

    @property
    def name(self) -> str:
        info = self._info
        return info.manifest.display_name if info else self.slug

    @property
    def version(self) -> str:
        info = self._info
        return info.manifest.version if info else ""

    def setting(self, key, default=None):
        """مقدار اعتبارسنجی‌شدهٔ تنظیم همین افزونه (با پیش‌فرض اسکیما)."""
        info = self._info
        return info.config.get(key, default) if info else default

    def settings(self) -> dict:
        info = self._info
        return dict(info.config) if info else {}

    @property
    def site(self):
        from apps.core.models import SiteSettings

        return SiteSettings.load()

    # --- language of messages written for somebody else
    def recipient_language(self, email: str = "") -> str:
        """Language for a message to ``email``: the profile language of the account that owns it, else the site default language."""
        from apps.core.recipient import language_for_email

        return language_for_email(email)

    def use_language(self, code: str = ""):
        """Context manager: build a message (``gettext``, templates, dates) in ``code`` (default: the site language). Use it for any
        email or notification whose reader is not the visitor of the current request."""
        from apps.core.recipient import use_language

        return use_language(code)

    # --- گزارش
    def log(self, message, level="info", detail=""):
        """رویداد را در «گزارش افزونه‌ها» ثبت می‌کند. level: info | warning | error"""
        level = level if level in ("info", "warning", "error") else "info"
        events.log_event(self.slug, "plugin", str(message), level=level, detail=str(detail))

    def audit(self, request, message, obj=None):
        from apps.audit.models import AuditLog
        from apps.audit.services import log_event

        log_event(request, AuditLog.Action.UPDATE, obj, f"[{self.slug}] {message}"[:400], object_type=(gettext("Plugin %(slug)s") % {"slug": self.slug}))

    # --- مجوز (همان سیستم هسته)
    def has_cap(self, user, capability: str) -> bool:
        return bool(getattr(user, "is_authenticated", False) and user.has_cap(capability))

    def cap(self, name: str) -> str:
        """نام کامل مجوزی که همین افزونه اعلام کرده (مثلاً cap('view') ← contact_form.view)."""
        return f"{self.slug}.{name}"

    # --- نشانی‌ها
    def admin_url(self, page_key: str) -> str:
        return reverse("plugins:page", args=[self.slug, page_key])

    def public_url(self, path: str = "") -> str:
        return reverse("plugins:public", args=[self.slug, path]) if path else reverse("plugins:public_root", args=[self.slug])

    def redirect_admin(self, page_key: str):
        return redirect(self.admin_url(page_key))

    # --- رندر
    def render_admin(self, request, template: str, context: dict | None = None, *, title: str = "", status: int = 200):
        """قالب افزونه را داخل چارچوب پنل رندر می‌کند. قالب باید ``{% extends "plugins/page.html" %}`` باشد."""
        ctx = {"plugin_title": title or self.name, "plugin_slug": self.slug, "plugin_name": self.name, **(context or {})}
        html = render_to_string(f"plugin:{self.slug}/{template}", ctx, request=request)
        response = HttpResponse(html, status=status)
        response["Cache-Control"] = "private, no-store"
        return response

    def render_fragment(self, request, template: str, context: dict | None = None) -> str:
        return render_to_string(f"plugin:{self.slug}/{template}", context or {}, request=request)

    def render_public(self, request, template: str, context: dict | None = None, *, title: str, status: int = 200, description: str = "", index: bool = False):
        """قالب افزونه را رندر و داخل چارچوب قالب فعال سایت نشان می‌دهد (هدر/فوتر/منو از قالب سایت)."""
        from apps.public.dto import PageItem
        from apps.public.rendering import render_page
        from apps.public.seo import make_seo
        from apps.public.views import _crumbs

        body = mark_safe('<div class="container">' + self.render_fragment(request, template, context) + "</div>")
        item = PageItem(title=title, url=request.path, body_html=body, date_display="", date_iso="", builder=True, show_title=True)

        def seo(site):
            return make_seo(request, site, title=title, description=description, path=request.path, robots="" if index else "noindex,follow")

        return render_page(request, "page_detail.html", {"page": item, "breadcrumbs": _crumbs((gettext("Home"), "/"), (title, ""))},
                           status=status, seo_factory=seo)

    # --- private files
    def data_dir(self):
        """This plugin's private folder (outside the public media; created on first use; owner and group (nginx) only). Returns a ``Path``."""
        from pathlib import Path

        from django.conf import settings

        path = Path(settings.RAVIX_PLUGIN_DATA_DIR) / self.slug
        path.mkdir(parents=True, exist_ok=True, mode=0o750)
        return path

    def file_response(self, request, relative_path: str, *, filename: str, content_type: str = "application/octet-stream"):
        """Send a file from ``data_dir()`` as a download (``Content-Disposition: attachment`` + ``nosniff``).

        Without ``RAVIX_ACCEL_REDIRECT_PREFIX`` the file is streamed by Django; with it, the response carries ``X-Accel-Redirect`` and
        nginx sends the bytes, so a busy download does not occupy a Gunicorn worker. Anything that is not a regular file inside
        ``data_dir()`` raises ``Http404``."""
        from urllib.parse import quote

        from django.conf import settings
        from django.http import FileResponse, Http404

        base = self.data_dir().resolve()
        target = (base / relative_path).resolve()
        if base not in target.parents or target.is_symlink() or not target.is_file():
            raise Http404
        prefix = getattr(settings, "RAVIX_ACCEL_REDIRECT_PREFIX", "") or ""
        if prefix:
            response = HttpResponse(content_type=content_type)
            response["X-Accel-Redirect"] = prefix.rstrip("/") + "/" + quote(f"{self.slug}/{target.relative_to(base).as_posix()}")
        else:
            response = FileResponse(open(target, "rb"), content_type=content_type)
        response["Content-Disposition"] = _attachment(filename)
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        return response

    # --- ابزار امنیتی
    def client_ip(self, request) -> str:
        from apps.core.clientip import client_ip

        return client_ip(request) or ""

    def rate_limit(self, request, bucket: str, limit: int, window: int) -> bool:
        """True اگر درخواست مجاز است. شمارش بر اساس IP و سطل ``bucket`` در بازهٔ ``window`` ثانیه."""
        digest = hashlib.sha256(f"{self.slug}|{bucket}|{self.client_ip(request)}".encode()).hexdigest()[:32]
        key = f"plugin-rl:{digest}"
        if cache.add(key, 1, window):
            return True
        try:
            return cache.incr(key) <= limit
        except ValueError:
            cache.set(key, 1, window)
            return True
