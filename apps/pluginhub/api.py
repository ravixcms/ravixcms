"""API مخزن افزونه‌ها (``/registry/v1/``). خواندنی‌ها عمومی‌اند؛ انتشار فقط با توکن ناشر. همهٔ پاسخ‌ها JSON هستند."""
import hashlib
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.db.models import F
from django.http import FileResponse, Http404, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.core.version import PLUGIN_API_VERSION, RAVIN_VERSION

from . import services
from .models import Category, HubPlugin, HubVersion

API_VERSION = 1
PAGE_SIZE, MAX_PAGE_SIZE = 20, 50


def _err(message, status, **extra):
    return JsonResponse({"error": message, **extra}, status=status, json_dumps_params={"ensure_ascii": False})


def _json(data, status=200):
    resp = JsonResponse(data, status=status, json_dumps_params={"ensure_ascii": False})
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


def _https_ok(request):
    return settings.DEBUG or settings.RAVIN_REGISTRY_ALLOW_INSECURE_DOWNLOAD or request.is_secure()


def hub_enabled(view):
    @wraps(view)
    def wrapper(request, *a, **kw):
        if not settings.RAVIN_REGISTRY_ENABLED:
            return _err("مخزن غیرفعال است.", 404)
        return view(request, *a, **kw)
    return wrapper


def _base(request):
    base = settings.SITE_URL or request.build_absolute_uri("/").rstrip("/")
    if not (settings.DEBUG or settings.RAVIN_REGISTRY_ALLOW_INSECURE_DOWNLOAD) and base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    return base


def ser_version(request, v: HubVersion, *, full=True):
    c = services.version_compat(v)
    d = {
        "version": v.version, "status": v.status, "published_at": v.published_at.isoformat() if v.published_at else None,
        "api_version": v.api_version, "ravin": v.ravin_spec, "compatibility": c.as_dict(), "size": v.size,
        "sha256": v.sha256, "fingerprint": v.fingerprint, "changelog": v.changelog,
        "signature": v.signature or None, "signature_key_id": v.signature_key_id or None, "signature_algorithm": "ed25519" if v.signature else None,
        "download_url": f"{_base(request)}/registry/v1/plugins/{v.plugin.slug}/versions/{v.version}/download/",
    }
    if v.status == HubVersion.Status.YANKED:
        d["yank_reason"] = v.yank_reason
        d["download_url"] = None
    if full:
        m = v.manifest or {}
        d["manifest"] = {k: m.get(k) for k in ("slug", "name", "version", "description", "author", "homepage", "api_version", "ravin", "requires",
                                              "capabilities", "models", "category", "license")}
    return d


def ser_plugin(request, p: HubPlugin, latest, compat_latest, *, versions=None):
    d = {
        "id": p.slug, "slug": p.slug, "name": p.name, "summary": p.summary,
        "category": {"slug": p.category.slug, "name": p.category.name} if p.category else None,
        "author": p.publisher.display_name, "publisher": {"name": p.publisher.display_name, "verified": p.publisher.is_verified},
        "homepage": p.homepage, "license": p.license,
        "latest_version": latest.version if latest else None,
        "latest_compatible_version": compat_latest.version if compat_latest else None,
        "compatible": compat_latest is not None,
        "updated_at": p.updated_at.isoformat(),
        "url": f"{_base(request)}/registry/v1/plugins/{p.slug}/",
    }
    if latest:
        d["latest"] = ser_version(request, latest, full=False)
    if versions is not None:
        d["description"] = p.description
        d["versions"] = [ser_version(request, v) for v in versions]
    return d


@require_GET
@hub_enabled
def meta(request):
    return _json({
        "name": settings.RAVIN_REGISTRY_NAME, "api_version": API_VERSION, "ravin_version": RAVIN_VERSION, "plugin_api_version": PLUGIN_API_VERSION,
        "https_required": not (settings.DEBUG or settings.RAVIN_REGISTRY_ALLOW_INSECURE_DOWNLOAD),
        "integrity": {"algorithm": "sha256", "header": "X-Checksum-SHA256"},
        "max_package_bytes": settings.RAVIN_REGISTRY_MAX_PACKAGE_BYTES,
        "signature": {"algorithm": "ed25519", "message": "ravin-plugin-v1\\n<slug>\\n<version>\\n<sha256>\\n<fingerprint>",
                      "note": "کلید عمومی را از کانال مستقل بگیرید و دستی در کلیدهای مورد اعتماد ثبت کنید؛ به این پاسخ برای اعتماد تکیه نکنید."},
    })


@require_GET
@hub_enabled
def categories(request):
    return _json({"results": [{"slug": c.slug, "name": c.name} for c in Category.objects.all()]})


@require_GET
@hub_enabled
def plugin_list(request):
    q, cat = request.GET.get("q", ""), request.GET.get("category", "")[:30]
    compatible = request.GET.get("compatible") in ("1", "true")
    try:
        page = max(1, int(request.GET.get("page", 1)))
        size = min(MAX_PAGE_SIZE, max(1, int(request.GET.get("page_size", PAGE_SIZE))))
    except ValueError:
        return _err("پارامتر صفحه‌بندی نامعتبر است.", 400)
    items = services.search(q, cat, compatible)
    chunk = items[(page - 1) * size: page * size]
    return _json({"count": len(items), "page": page, "page_size": size, "results": [ser_plugin(request, p, l, c) for p, l, c in chunk]})


def _public_plugin(slug):
    p = HubPlugin.objects.select_related("category", "publisher").filter(slug=slug, is_listed=True, publisher__is_active=True).first()
    if p is None:
        raise Http404
    return p


@require_GET
@hub_enabled
def plugin_detail(request, slug):
    try:
        p = _public_plugin(slug)
    except Http404:
        return _err("افزونه پیدا نشد.", 404)
    vs = sorted([v for v in p.versions.all() if v.status != HubVersion.Status.DRAFT], key=lambda v: v.parsed, reverse=True)
    if not any(v.status == HubVersion.Status.PUBLISHED for v in vs):
        return _err("افزونه پیدا نشد.", 404)
    return _json(ser_plugin(request, p, services.latest_published(p), services.latest_published(p, compatible_only=True), versions=vs))


def _version_or_none(slug, version, *, statuses):
    try:
        p = _public_plugin(slug)
    except Http404:
        return None
    return HubVersion.objects.select_related("plugin").filter(plugin=p, version=version, status__in=statuses).first()


@require_GET
@hub_enabled
def version_detail(request, slug, version):
    v = _version_or_none(slug, version, statuses=[HubVersion.Status.PUBLISHED, HubVersion.Status.YANKED])
    if v is None:
        return _err("نسخه پیدا نشد.", 404)
    return _json(ser_version(request, v))


@require_GET
@hub_enabled
def download(request, slug, version):
    if not _https_ok(request):
        return _err("دریافت بسته فقط از طریق HTTPS مجاز است.", 403)
    v = _version_or_none(slug, version, statuses=[HubVersion.Status.PUBLISHED])
    if v is None:
        return _err("نسخه پیدا نشد یا پس گرفته شده است.", 404)
    try:
        v.package.open("rb")
        data = v.package.read()
    except (FileNotFoundError, ValueError, OSError):
        return _err("فایل بسته در دسترس نیست.", 500)
    finally:
        v.package.close()
    if hashlib.sha256(data).hexdigest() != v.sha256:  # فایل روی دیسک دست‌کاری شده؛ هرگز سرو نمی‌شود
        return _err("تمامیت فایل تأیید نشد؛ بسته سرو نمی‌شود.", 500)
    HubVersion.objects.filter(pk=v.pk).update(downloads=F("downloads") + 1)
    import io
    resp = FileResponse(io.BytesIO(data), content_type="application/zip", as_attachment=True, filename=f"{slug}-{version}.zip")
    resp["X-Checksum-SHA256"] = v.sha256
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Cache-Control"] = "public, max-age=3600"
    resp["Content-Length"] = str(len(data))
    return resp


# ------------------------------------------------------------------ انتشار با توکن ناشر
def _throttled(request, bucket, limit, window):
    ip = (request.META.get("REMOTE_ADDR") or "?")[:45]
    key = f"hub:{bucket}:{ip}"
    n = cache.get(key, 0)
    if n >= limit:
        return True
    cache.set(key, n + 1, window)
    return False


@csrf_exempt  # احراز هویت با توکن Bearer است، نه کوکی؛ پس CSRF موضوعیت ندارد
@require_POST
@hub_enabled
def publish(request):
    if not _https_ok(request):
        return _err("انتشار فقط از طریق HTTPS مجاز است.", 403)
    if _throttled(request, "publish", 30, 600):
        return _err("تعداد درخواست زیاد است؛ بعداً تلاش کنید.", 429)
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        return _err("توکن ناشر الزامی است.", 401)
    publisher = services.authenticate_token(auth[7:].strip())
    if publisher is None:
        return _err("توکن نامعتبر یا ناشر غیرمجاز.", 401)
    f = request.FILES.get("package")
    if f is None:
        return _err("فایل «package» الزامی است.", 400)
    if f.size > settings.RAVIN_REGISTRY_MAX_PACKAGE_BYTES:
        return _err("حجم بسته از سقف مجاز بیشتر است.", 400)
    try:
        v = services.upload_version(publisher, publisher.user, f.read(), changelog=request.POST.get("changelog", ""),
                                    publish=request.POST.get("publish") in ("1", "true"))
    except services.HubError as exc:
        return _err(str(exc), 400)
    return _json(ser_version(request, v), status=201)
