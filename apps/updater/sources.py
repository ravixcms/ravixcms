"""
منبع رسمی به‌روزرسانی: ``<RAVIX_UPDATE_URL>/updates/v1/index.json`` (فقط HTTPS، بدون ریدایرکت، با سقف حجم) یا پوشهٔ محلی ``RAVIX_UPDATE_DIR``.

هیچ اعتمادی به منبع نیست: SHA-256 و امضای هر بسته را خود به‌روزرسان می‌سنجد (امضا نوع/شناسه/نسخه/هش را می‌پوشاند)، پس فهرست دست‌کاری‌شده
نمی‌تواند بستهٔ دیگری را جایگزین کند. فهرست اختیاراً امضا هم می‌شود (RAVIX_UPDATE_REQUIRE_SIGNED_INDEX).
"""
import hashlib
import json
import logging
import re
import urllib.parse
from pathlib import Path

from django.conf import settings
from django.core.cache import cache

from apps.core.deprecation import read_legacy_key
from apps.core.version import UPDATE_SCHEMA_VERSION

from . import signing
from .errors import UpdateError

logger = logging.getLogger("ravix.updater")
MAX_INDEX = 2 * 1024 * 1024
VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,48}$")


INDEX_TIMEOUT = 8  # ثانیه؛ فهرست کوچک است و پنل نباید برای سرور قطع‌شده منتظر بماند
DOWNLOAD_TIMEOUT = 60
FAIL_TTL = 300  # ثانیه؛ پس از شکست تا این مدت دوباره شبکه صدا زده نمی‌شود (کش منفی)


def _http_get(url: str, max_bytes: int, timeout: int = DOWNLOAD_TIMEOUT):
    from apps.plugins.sources import http_get

    try:
        return http_get(url, max_bytes, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - RegistryUnavailable و خطاهای شبکه
        raise UpdateError(f"دریافت از منبع به‌روزرسانی ممکن نشد: {exc}")


def normalize_entry(raw, kind: str) -> dict | None:
    """ورودی فهرست را اعتبارسنجی و عادی می‌کند؛ ورودی خراب ← None (نادیده گرفته می‌شود)."""
    if not isinstance(raw, dict):
        return None
    version, sha = str(raw.get("version", "")), str(raw.get("sha256", "")).lower()
    if not VERSION_RE.match(version) or not SHA_RE.match(sha):
        return None
    try:
        size = int(raw.get("size") or 0)
    except (TypeError, ValueError):
        return None
    if not 0 <= size <= settings.RAVIX_UPDATE_MAX_PACKAGE_BYTES:
        return None
    req = raw.get("requires") if isinstance(raw.get("requires"), dict) else {}
    pk = req.get("packages", [])
    return {"version": version, "sha256": sha, "size": size, "download_url": str(raw.get("download_url", ""))[:500],
            "signature": str(raw.get("signature") or "")[:200], "signature_key_id": str(raw.get("signature_key_id") or "")[:32],
            "released": str(raw.get("released", ""))[:40], "notes": str(raw.get("notes", ""))[:2000], "migrations": bool(raw.get("migrations")),
            "requires": {"python": str(req.get("python", ""))[:60], "min_from": str(req.get("min_from", ""))[:20], "ravix": str(read_legacy_key(req))[:60],
                         "plugin_api": req.get("plugin_api") if isinstance(req.get("plugin_api"), int) else None,
                         "theme_api": req.get("theme_api") if isinstance(req.get("theme_api"), int) else None,
                         "packages": [str(p)[:120] for p in pk[:100]] if isinstance(pk, list) else []}}


def parse_index(data: bytes, signature: str = "", key_id: str = "") -> dict:
    if settings.RAVIX_UPDATE_REQUIRE_SIGNED_INDEX and not signature:
        raise UpdateError("فهرست به‌روزرسانی امضا ندارد و سیاست این سایت امضای فهرست را الزامی کرده است.")
    if signature:
        ver = signing.verify_index(data, signature, key_id)
        if not ver.ok:
            raise UpdateError(f"امضای فهرست به‌روزرسانی نامعتبر است: {ver.reason}")
    try:
        doc = json.loads(data)
    except ValueError:
        raise UpdateError("فهرست به‌روزرسانی JSON معتبر نیست.")
    if not isinstance(doc, dict) or doc.get("schema") != UPDATE_SCHEMA_VERSION:
        raise UpdateError("قالب فهرست به‌روزرسانی پشتیبانی نمی‌شود.")
    core = [e for e in (normalize_entry(x, "core") for x in doc.get("core", [])[:200]) if e]
    themes = {}
    raw_themes = doc.get("themes") if isinstance(doc.get("themes"), dict) else {}
    for slug, entries in list(raw_themes.items())[:100]:
        if SLUG_RE.match(str(slug)) and isinstance(entries, list):
            themes[slug] = [e for e in (normalize_entry(x, "theme") for x in entries[:200]) if e]
    return {"channel": str(doc.get("channel", ""))[:20], "generated_at": str(doc.get("generated_at", ""))[:40], "core": core, "themes": themes}


class HttpSource:
    kind = "official"

    def __init__(self, base):
        self.base = base.rstrip("/")
        self.ref = self.base

    def index(self) -> dict:
        data, _ = _http_get(f"{self.base}/updates/v1/index.json", MAX_INDEX, INDEX_TIMEOUT)
        sig, kid = "", ""
        try:
            raw, _ = _http_get(f"{self.base}/updates/v1/index.json.sig", 4096, INDEX_TIMEOUT)
            sig_doc = json.loads(raw)
            sig, kid = str(sig_doc.get("signature", "")), str(sig_doc.get("key_id", ""))
        except (UpdateError, ValueError, AttributeError):
            pass
        return parse_index(data, sig, kid)

    def download(self, entry: dict) -> bytes:
        raw_url = entry.get("download_url") or ""
        # ravix_release فقط «نام فایل» می‌نویسد؛ نسبی نسبت به <base>/updates/v1/ حل می‌شود. نشانی مطلق باید همان دامنه (و https) باشد.
        url = urllib.parse.urljoin(f"{self.base}/updates/v1/", raw_url) if raw_url else ""
        parsed, base = urllib.parse.urlparse(url), urllib.parse.urlparse(self.base)
        if not url or parsed.scheme != "https" or parsed.netloc != base.netloc:
            raise UpdateError("نشانی دریافت روی دامنهٔ دیگری است؛ برای امنیت رد شد.")
        data, _ = _http_get(url, settings.RAVIX_UPDATE_MAX_PACKAGE_BYTES)
        return data


class DirSource:
    """منبع پوشه‌ای (آفلاین/آزمایشی): index.json، index.json.sig (اختیاری) و بسته‌ها کنار هم؛ download_url فقط نام فایل است."""

    kind = "directory"

    def __init__(self, path):
        self.dir = Path(path)
        self.ref = f"dir:{self.dir.name}"

    def index(self) -> dict:
        try:
            data = (self.dir / "index.json").read_bytes()
        except OSError:
            raise UpdateError("index.json در پوشهٔ منبع به‌روزرسانی پیدا نشد.")
        sig, kid = "", ""
        sig_path = self.dir / "index.json.sig"
        if sig_path.is_file():
            try:
                doc = json.loads(sig_path.read_text(encoding="utf-8"))
                sig, kid = str(doc.get("signature", "")), str(doc.get("key_id", ""))
            except ValueError:
                raise UpdateError("index.json.sig معتبر نیست.")
        return parse_index(data[:MAX_INDEX], sig, kid)

    def download(self, entry: dict) -> bytes:
        name = Path(entry.get("download_url") or "").name
        path = (self.dir / name).resolve()
        if not name or self.dir.resolve() not in path.parents:
            raise UpdateError("نام فایل بسته نامعتبر است.")
        try:
            if path.stat().st_size > settings.RAVIX_UPDATE_MAX_PACKAGE_BYTES:
                raise UpdateError("بسته از سقف حجم بزرگ‌تر است.")
            return path.read_bytes()
        except OSError:
            raise UpdateError("فایل بسته در منبع پیدا نشد.")


def get_source():
    if settings.RAVIX_UPDATE_DIR:
        return DirSource(settings.RAVIX_UPDATE_DIR)
    if settings.RAVIX_UPDATE_URL:
        return HttpSource(settings.RAVIX_UPDATE_URL)
    raise UpdateError("منبع رسمی به‌روزرسانی تنظیم نشده است (RAVIX_UPDATE_URL).")


def has_source() -> bool:
    return bool(settings.RAVIX_UPDATE_DIR or settings.RAVIX_UPDATE_URL)


def cached_index(force: bool = False) -> dict:
    """فهرست با کش کوتاه (۱۰ دقیقه) برای پنل. خطا ← UpdateError (پنل پیام می‌دهد و نمی‌شکند).

    شکست هم ۵ دقیقه کش می‌شود تا با قطع بودن سرور رسمی هر بازشدن صفحه ۸ ثانیه معطل نشود؛ ``force=True`` (دکمهٔ «بررسی») کش را دور می‌زند.
    """
    key, fail_key = "updater:index", "updater:index:fail"
    if not force:
        hit = cache.get(key)
        if hit is not None:
            return hit
        failed = cache.get(fail_key)
        if failed:
            raise UpdateError(failed)
    try:
        idx = get_source().index()
    except UpdateError as exc:
        logger.warning("منبع به‌روزرسانی در دسترس نیست: %s", exc)
        cache.set(fail_key, str(exc)[:300], FAIL_TTL)
        raise
    cache.delete(fail_key)
    cache.set(key, idx, 600)
    return idx


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
