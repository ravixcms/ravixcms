"""محدودسازی تلاش‌های ناموفق ورود در برابر حدس رمز: هم به‌ازای (IP، نام‌کاربری) و هم مجموع هر IP (چرخاندن نام‌کاربری فایده ندارد)."""
import ipaddress
import logging
import time

from django.conf import settings
from django.core.cache import caches

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5          # به‌ازای هر (IP، نام‌کاربری)
MAX_ATTEMPTS_PER_IP = 20  # مجموع هر IP
LOCK_SECONDS = 5 * 60
FALLBACK_ALIAS = "throttle_fallback"  # کش جدولی (دیتابیس) برای وقتی کش اصلی خراب است
_WARN_EVERY = 60  # ثانیه؛ هشدار لاگ هر بار تلاش ورود نباید لاگ را پر کند
_last_warn = 0.0


def client_ip(request) -> str:
    """IP کلاینت. فقط وقتی RAVIX_TRUST_PROXY_HEADERS=true و برنامه تنها پشت پراکسی خودتان (که X-Real-IP را بازنویسی می‌کند) است، از X-Real-IP می‌خواند؛
    وگرنه سربرگ‌های قابل‌جعل نادیده گرفته می‌شوند."""
    if getattr(settings, "RAVIX_TRUST_PROXY_HEADERS", False):
        raw = request.META.get("HTTP_X_REAL_IP", "").strip()
        try:
            return str(ipaddress.ip_address(raw))
        except ValueError:
            pass
    return request.META.get("REMOTE_ADDR") or "unknown"


def _key(ip: str, identifier: str) -> str:
    return f"login-fail:{ip}:{(identifier or '').strip().lower()}"


def _ip_key(ip: str) -> str:
    return f"login-fail-ip:{ip}"


def _fallback_cache():
    if FALLBACK_ALIAS in settings.CACHES:
        return caches[FALLBACK_ALIAS]
    return None


def _with_cache(fn):
    """fn را روی کش اصلی اجرا می‌کند. اگر کش خراب بود (پوشهٔ کش نیست/قابل‌نوشتن نیست، Redis قطع است و …)،
    به کش جدولی در دیتابیس برمی‌گردد و هشدار لاگ می‌کند؛ محافظت در برابر حدس رمز خاموش نمی‌شود.
    اگر کش جایگزین هم خراب باشد (یعنی دیتابیس هم خراب است و ورود هرچه باشد ممکن نیست) خطا بالا می‌آید."""
    global _last_warn
    try:
        return fn(caches["default"])
    except Exception as exc:  # noqa: BLE001 - هر خطای کش
        fb = _fallback_cache()
        if fb is None:
            raise
        now = time.monotonic()
        if now - _last_warn >= _WARN_EVERY:
            _last_warn = now
            logger.warning("کش اصلی برای محدودسازی ورود خراب است (%s: %s)؛ از کش جدولی دیتابیس استفاده می‌شود. پوشهٔ کش/Redis را بررسی کنید.",
                           type(exc).__name__, exc)
        return fn(fb)


def _bump_on(c, key: str) -> None:
    c.add(key, 0, LOCK_SECONDS)
    try:
        c.incr(key)
    except ValueError:
        c.set(key, 1, LOCK_SECONDS)


def is_locked(request, identifier: str) -> bool:
    ip = client_ip(request)
    k1, k2 = _key(ip, identifier), _ip_key(ip)
    return _with_cache(lambda c: c.get(k1, 0) >= MAX_ATTEMPTS or c.get(k2, 0) >= MAX_ATTEMPTS_PER_IP)


def register_failure(request, identifier: str) -> None:
    ip = client_ip(request)
    k1, k2 = _key(ip, identifier), _ip_key(ip)

    def bump(c):
        _bump_on(c, k1)
        _bump_on(c, k2)

    _with_cache(bump)


def reset(request, identifier: str) -> None:
    k = _key(client_ip(request), identifier)
    _with_cache(lambda c: c.delete(k))


def allow_send(request, email: str, kind: str) -> bool:
    """محدودیت نرخ ارسال ایمیل (بازیابی رمز/تأیید ایمیل) به‌ازای IP و به‌ازای ایمیل. هر درخواست شمرده می‌شود (موفق یا نه)،
    تا نتوان با درخواست مکرر صندوق کسی را پر کرد یا از وجود/نبود ایمیل سر درآورد. True = مجاز است.
    ایمیل در کلید به‌صورت هش می‌آید (آدرس خام در کش نمی‌ماند)."""
    import hashlib

    ip = client_ip(request)
    digest = hashlib.sha256((email or "").strip().lower().encode()).hexdigest()[:32]
    k_ip, k_mail = f"mail-send:{kind}:ip:{ip}", f"mail-send:{kind}:em:{digest}"
    window = settings.RAVIX_MAIL_WINDOW_SECONDS

    def hit(c):
        counts = []
        for key in (k_ip, k_mail):
            c.add(key, 0, window)
            try:
                counts.append(c.incr(key))
            except ValueError:
                c.set(key, 1, window)
                counts.append(1)
        return counts

    n_ip, n_mail = _with_cache(hit)
    allowed = n_ip <= settings.RAVIX_MAIL_LIMIT_PER_IP and n_mail <= settings.RAVIX_MAIL_LIMIT_PER_EMAIL
    if not allowed:
        logger.warning("محدودیت نرخ ارسال ایمیل (%s) برای IP %s فعال شد.", kind, ip)
    return allowed
