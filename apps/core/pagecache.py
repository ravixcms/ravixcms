"""Full-page cache for visitors who are not logged in.

A cached page is served before the session, the database or the theme is touched, so most requests need no query at all. Safety first:

* only ``GET`` of pages produced by the public site views (``apps.public``) are stored; plugin pages, the shop (carts, checkout, orders), the panel,
  accounts, API and the installer never are;
* a request that carries a session or flash-message cookie, or an Authorization header, is never served from the cache and never stored;
* a response is stored only if it is a plain 200 without ``Set-Cookie``, without ``Cache-Control: private/no-store`` and without a CSRF token
  in the page (forms are therefore never cached);
* the whole cache is emptied (a new "generation" starts) whenever any content-bearing model is saved or deleted, and entries live at most
  ``RAVIX_PAGE_CACHE_TTL`` seconds anyway.

Switch: Site settings ▸ Speed (auto = on for shared hosting, off for a VPS) or ``RAVIX_PAGE_CACHE=on|off``.
"""
import hashlib
import re
import secrets

from django.conf import settings
from django.core.cache import cache
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.http import HttpResponse
from django.utils.cache import get_conditional_response

GEN_KEY = "ravix:pc:gen"
CFG_KEY = "ravix:pc:cfg"
SKIP_COOKIES = ("messages",)
STORE_HEADERS_DROP = {"set-cookie", "content-length", "date", "connection", "transfer-encoding", "content-encoding", "x-ravix-cache"}
_PAGE_QUERY = re.compile(r"^page=\d{1,4}$")
_CSRF_MARKERS = (b"csrfmiddlewaretoken", b'name="csrf-token"', b"name='csrf-token'")

# Models whose changes cannot alter what a visitor sees (or change constantly): they never clear the cache.
IGNORED_MODELS = {
    "sessions.session", "admin.logentry", "audit.auditlog", "core.cronrun", "core.queuedemail", "core.throttlecacheentry", "core.mailsettings",
    "plugins.pluginevent", "contenttypes.contenttype", "auth.permission", "updater.updaterun", "updater.updatelock",
    "shop.order", "shop.orderitem", "shop.payment", "shop.paymentattempt", "shop.cart", "shop.cartitem", "shop.downloadgrant", "shop.orderevent",
}
IGNORED_APPS = {"audit", "updater"}


def _bump(**_kwargs):
    try:
        cache.set(GEN_KEY, secrets.token_hex(6), None)
    except Exception:  # noqa: BLE001 - a broken cache must never stop a save (the throttle has its own database fallback)
        pass


def clear() -> None:
    """Start a new generation: every stored page becomes unreachable."""
    _bump()


def _relevant(sender, instance=None, created=False, update_fields=None, **kwargs):
    label = sender._meta.label_lower
    if label in IGNORED_MODELS or sender._meta.app_label in IGNORED_APPS:
        return
    if label == "accounts.user" and update_fields and set(update_fields) <= {"last_login"}:
        return
    if label == "comments.comment" and created and getattr(instance, "status", "approved") != "approved":
        return
    if label == "core.sitesettings":
        try:
            cache.delete(CFG_KEY)
        except Exception:  # noqa: BLE001
            pass
    _bump()


def _relevant_m2m(sender, action=None, **kwargs):
    if action in {"post_add", "post_remove", "post_clear"}:
        _bump()


post_save.connect(_relevant, dispatch_uid="ravix-pagecache-save")
post_delete.connect(_relevant, dispatch_uid="ravix-pagecache-delete")
m2m_changed.connect(_relevant_m2m, dispatch_uid="ravix-pagecache-m2m")


def enabled(request=None) -> bool:
    """The effective switch: the panel choice, else the server default. Cached so a hit needs no database query."""
    try:
        value = cache.get(CFG_KEY)
    except Exception:  # noqa: BLE001 - storage down: behave as if the cache were off
        return False
    if value is None:
        server = str(getattr(settings, "RAVIX_PAGE_CACHE", "off")).lower() == "on"
        value = server
        try:
            from .regional import load_site_settings

            # Shared with the later middleware through request.site_settings, so this adds no query of its own.
            site = load_site_settings(request) if request is not None else None
            if site is None:
                from .models import SiteSettings

                site = SiteSettings.objects.filter(pk=1).first()
            choice = getattr(site, "page_cache", "auto")
            if choice == "on":
                value = True
            elif choice == "off":
                value = False
        except Exception:  # noqa: BLE001 - before the first migration: off, and not remembered (the cache is shared with the installed site)
            return False
        try:
            cache.set(CFG_KEY, value, 600)
        except Exception:  # noqa: BLE001
            pass
    return bool(value)


def _private_request(request) -> bool:
    if request.META.get("HTTP_AUTHORIZATION"):
        return True
    cookies = request.COOKIES
    return settings.SESSION_COOKIE_NAME in cookies or any(c in cookies for c in SKIP_COOKIES)


def _cacheable_query(request) -> bool:
    query = request.META.get("QUERY_STRING", "")
    return not query or bool(_PAGE_QUERY.match(query))


def _key(request, gen: str) -> str:
    lang = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME, "")[:12]
    raw = "|".join((gen, request.get_host(), "s" if request.is_secure() else "h", request.get_full_path(), lang))
    return "ravix:pc:p:" + hashlib.sha1(raw.encode("utf-8", "replace"), usedforsecurity=False).hexdigest()


def _from_public_views(request) -> bool:
    match = getattr(request, "resolver_match", None)
    module = getattr(getattr(match, "func", None), "__module__", "") or ""
    return module.startswith("apps.public.")


def _storable(request, response) -> bool:
    if response.status_code != 200 or response.streaming or not _from_public_views(request):
        return False
    if response.has_header("Set-Cookie") or response.cookies:
        return False
    cc = response.get("Cache-Control", "").lower()
    if "private" in cc or "no-store" in cc or "no-cache" in cc:
        return False
    ctype = response.get("Content-Type", "").lower()
    if not (ctype.startswith("text/") or "xml" in ctype or "json" in ctype):
        return False
    body = response.content
    if len(body) > 1_500_000 or any(marker in body for marker in _CSRF_MARKERS):
        return False
    return True


class PageCacheMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method != "GET" or getattr(settings, "SETUP_MODE", False) or settings.DEBUG and not getattr(settings, "RAVIX_PAGE_CACHE_IN_DEBUG", False):
            return self.get_response(request)
        if not enabled(request):
            return self.get_response(request)
        if _private_request(request) or not _cacheable_query(request):
            response = self.get_response(request)
            response["X-Ravix-Cache"] = "BYPASS"
            return response
        gen = cache.get(GEN_KEY)
        if gen is None:
            gen = secrets.token_hex(6)
            cache.add(GEN_KEY, gen, None)
            gen = cache.get(GEN_KEY) or gen
        key = _key(request, gen)
        hit = cache.get(key)
        if hit is not None:
            conditional = get_conditional_response(request, etag=hit["headers"].get("ETag"))
            if conditional is not None:
                conditional["X-Ravix-Cache"] = "HIT"
                return conditional
            response = HttpResponse(hit["content"], status=hit["status"], content_type=hit["content_type"])
            for name, value in hit["headers"].items():
                response[name] = value
            response["X-Ravix-Cache"] = "HIT"
            return response
        response = self.get_response(request)
        stored = False
        try:
            if _storable(request, response):
                headers = {k: v for k, v in response.items() if k.lower() not in STORE_HEADERS_DROP and k.lower() != "content-type"}
                cache.set(key, {"status": response.status_code, "content": response.content, "content_type": response["Content-Type"], "headers": headers},
                          int(getattr(settings, "RAVIX_PAGE_CACHE_TTL", 900)))
                stored = True
        except Exception:  # noqa: BLE001 - a cache problem must never break a page
            stored = False
        response["X-Ravix-Cache"] = "MISS" if stored else "BYPASS"
        return response
