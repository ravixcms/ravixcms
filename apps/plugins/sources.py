"""
منبع‌های افزونه برای نصب‌کننده: مخزن رسمی راه دور (HTTPS) یا مخزن همین نمونه (apps.pluginhub). هر دو یک شکل داده برمی‌گردانند.
هیچ اعتمادی به منبع نیست: تمامیت (SHA-256) و امضا را نصب‌کننده خودش دوباره می‌سنجد.
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.apps import apps as django_apps
from django.conf import settings
from django.core.cache import cache

from . import versions
from .services import PluginError

logger = logging.getLogger("ravin.plugins")
MAX_JSON = 2 * 1024 * 1024


class RegistryUnavailable(PluginError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):  # ریدایرکت (که می‌تواند به http/دامنهٔ دیگر برود) دنبال نمی‌شود
        return None


def http_get(url: str, max_bytes: int, allow_insecure: bool = False) -> tuple[bytes, dict]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" and not (allow_insecure and settings.DEBUG and parsed.scheme == "http"):
        raise RegistryUnavailable("فقط نشانی HTTPS مجاز است.")
    req = urllib.request.Request(url, headers={"User-Agent": "ravin-plugin-installer/1"})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=30) as resp:
            data = resp.read(max_bytes + 1)
            headers = dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raise RegistryUnavailable(f"مخزن پاسخ خطا داد ({exc.code}).")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RegistryUnavailable(f"اتصال به مخزن ممکن نیست: {exc}")
    if len(data) > max_bytes:
        raise RegistryUnavailable("پاسخ مخزن از سقف حجم بزرگ‌تر است.")
    return data, headers


class RemoteSource:
    kind = "registry"

    def __init__(self, base_url: str, allow_insecure: bool = False):
        self.base = base_url.rstrip("/")
        self.allow_insecure = allow_insecure
        self.ref = self.base

    def info(self, slug: str) -> dict:
        raw, _ = http_get(f"{self.base}/registry/v1/plugins/{urllib.parse.quote(slug)}/", MAX_JSON, self.allow_insecure)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise RegistryUnavailable("پاسخ مخزن JSON معتبر نیست.")
        if not isinstance(data, dict) or "versions" not in data:
            raise RegistryUnavailable("پاسخ مخزن ناقص است.")
        return data

    def download(self, slug: str, entry: dict) -> bytes:
        url = entry.get("download_url") or ""
        if urllib.parse.urlparse(url).netloc != urllib.parse.urlparse(self.base).netloc:
            raise RegistryUnavailable("نشانی دریافت روی دامنهٔ دیگری است؛ برای امنیت رد شد.")
        data, _ = http_get(url, getattr(settings, "RAVIN_REGISTRY_MAX_PACKAGE_BYTES", 5 * 1024 * 1024), self.allow_insecure)
        return data

    def latest_map(self) -> dict:
        raw, _ = http_get(f"{self.base}/registry/v1/plugins/?page_size=50", MAX_JSON, self.allow_insecure)
        return {r["slug"]: r.get("latest_compatible_version") for r in json.loads(raw).get("results", [])}


class LocalHubSource:
    """مخزن همین نمونه (بدون شبکه). همان بررسی‌های تمامیت/امضا بعداً در نصب‌کننده انجام می‌شود."""

    kind = "registry"
    ref = "local-registry"

    def info(self, slug: str) -> dict:
        from apps.pluginhub import services as hub
        from apps.pluginhub.models import HubPlugin, HubVersion

        p = HubPlugin.objects.filter(slug=slug, is_listed=True, publisher__is_active=True).first()
        if p is None:
            raise RegistryUnavailable("افزونه در مخزن پیدا نشد.")
        vs = [v for v in p.versions.all() if v.status == HubVersion.Status.PUBLISHED]
        return {"slug": p.slug, "name": p.name, "versions": [
            {"version": v.version, "status": "published", "sha256": v.sha256, "fingerprint": v.fingerprint, "signature": v.signature or None,
             "signature_key_id": v.signature_key_id or None, "compatibility": hub.version_compat(v).as_dict()}
            for v in sorted(vs, key=lambda v: v.parsed, reverse=True)]}

    def download(self, slug: str, entry: dict) -> bytes:
        from apps.pluginhub.models import HubVersion

        v = HubVersion.objects.filter(plugin__slug=slug, version=entry["version"], status=HubVersion.Status.PUBLISHED).first()
        if v is None:
            raise RegistryUnavailable("نسخه پیدا نشد.")
        try:
            v.package.open("rb")
            return v.package.read()
        except OSError:
            raise RegistryUnavailable("فایل بسته در مخزن در دسترس نیست.")
        finally:
            v.package.close()

    def latest_map(self) -> dict:
        from apps.pluginhub import services as hub

        return {p.slug: (c.version if c else None) for p, _l, c in hub.search()}


def get_source():
    url = getattr(settings, "RAVIN_OFFICIAL_REGISTRY_URL", "")
    if url:
        return RemoteSource(url)
    if getattr(settings, "RAVIN_REGISTRY_ENABLED", False) and django_apps.is_installed("apps.pluginhub"):
        return LocalHubSource()
    raise RegistryUnavailable("مخزن رسمی تنظیم نشده است (RAVIN_OFFICIAL_REGISTRY_URL).")


def has_source() -> bool:
    try:
        get_source()
        return True
    except RegistryUnavailable:
        return False


def pick_version(info: dict, version: str | None) -> dict:
    """نسخهٔ منتشرشده: مشخص‌شده یا آخرین نسخهٔ سازگار."""
    pub = [v for v in info.get("versions", []) if v.get("status", "published") == "published"]
    if version:
        entry = next((v for v in pub if v.get("version") == version), None)
        if entry is None:
            raise RegistryUnavailable(f"نسخهٔ {version} در مخزن منتشر نشده است.")
    else:
        ok = [v for v in pub if v.get("compatibility", {}).get("compatible")]
        if not ok:
            raise RegistryUnavailable("هیچ نسخهٔ سازگار با این Ravin در مخزن نیست.")
        entry = max(ok, key=lambda v: versions.parse(v["version"]) or (0, 0, 0))
    if not entry.get("compatibility", {}).get("compatible"):
        raise RegistryUnavailable("این نسخه با Ravin فعلی سازگار نیست: " + "؛ ".join(entry.get("compatibility", {}).get("problems", [])))
    return entry


def latest_versions() -> dict:
    """نقشهٔ slug ← آخرین نسخهٔ سازگار برای نمایش «نسخهٔ جدید» (کش ۱۰ دقیقه‌ای؛ خطا ⇒ نقشهٔ خالی، پنل نباید بشکند)."""
    cached = cache.get("plugins:latest_versions")
    if cached is not None:
        return cached
    try:
        data = get_source().latest_map()
    except Exception as exc:  # noqa: BLE001
        logger.warning("دریافت نسخه‌های مخزن ناموفق بود: %s", exc)
        data = {}
    cache.set("plugins:latest_versions", data, 600 if data else 60)
    return data


def update_available(slug: str, current: str) -> str | None:
    latest = latest_versions().get(slug)
    if latest and (versions.parse(latest) or (0, 0, 0)) > (versions.parse(current) or (0, 0, 0)):
        return latest
    return None
