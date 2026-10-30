"""محدودسازی تلاش‌های ناموفق ورود در برابر حدس رمز: هم به‌ازای (IP، نام‌کاربری) و هم مجموع هر IP (چرخاندن نام‌کاربری فایده ندارد)."""
from apps.core.clilang import cli_gettext
import logging
import time

from django.conf import settings
from django.core.cache import caches

from apps.core.clientip import client_ip as _client_ip

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5          # به‌ازای هر (IP، نام‌کاربری)
MAX_ATTEMPTS_PER_IP = 20  # مجموع هر IP
LOCK_SECONDS = 5 * 60
COUNT_SECONDS = 60 * 60   # failures are remembered this long, so the waiting time can grow with each round
# (failures, seconds to wait): the wait grows with every failure; nothing is ever locked for good (a permanent lock would be a tool for locking the owner out).
PAIR_TIERS = ((20, 60 * 60), (12, 15 * 60), (8, 5 * 60), (MAX_ATTEMPTS, 60))
IP_TIERS = ((60, 60 * 60), (40, 15 * 60), (MAX_ATTEMPTS_PER_IP, LOCK_SECONDS))
ACCOUNT_CAPTCHA_AFTER = 10  # failures on one account (from any address) before everybody signing in to it must pass the proof-of-work
IP_CAPTCHA_AFTER = 10
FALLBACK_ALIAS = "throttle_fallback"  # کش جدولی (دیتابیس) برای وقتی کش اصلی خراب است
_WARN_EVERY = 60  # ثانیه؛ هشدار لاگ هر بار تلاش ورود نباید لاگ را پر کند
_last_warn = None  # monotonic() counts from boot, so 0.0 would silence the first warning on a freshly booted machine


def client_ip(request) -> str | None:
    """IP کلاینت از تابع واحد apps.core.clientip؛ ``None`` یعنی قابل‌تشخیص نیست (آن‌گاه فقط محدودیت به‌ازای نام‌کاربری/ایمیل اعمال می‌شود)."""
    return _client_ip(request)


def _key(ip: str | None, identifier: str) -> str:
    return f"login-fail:{ip or '-'}:{(identifier or '').strip().lower()}"


def _ip_key(ip: str) -> str:
    return f"login-fail-ip:{ip}"


def _account_key(identifier: str) -> str:
    import hashlib

    return f"login-fail-acct:{hashlib.sha256((identifier or '').strip().lower().encode()).hexdigest()[:32]}"


def _lock_key(base: str) -> str:
    return base.replace("login-fail", "login-lock", 1)


def _wait_for(count: int, tiers) -> int:
    for threshold, seconds in tiers:
        if count >= threshold:
            return seconds
    return 0


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
        main = caches["default"]  # a plain FileBasedCache raises here when its folder cannot be created
        if getattr(main, "broken", False):  # SafeFileBasedCache: the cache folder cannot be written (it does not raise by design)
            raise OSError(cli_gettext("the cache folder is missing or not writable"))
        result = fn(main)
        if getattr(main, "broken", False):
            raise OSError(cli_gettext("the cache folder is missing or not writable"))
        return result
    except Exception as exc:  # noqa: BLE001 - هر خطای کش
        fb = _fallback_cache()
        if fb is None:
            raise
        now = time.monotonic()
        if _last_warn is None or now - _last_warn >= _WARN_EVERY:
            _last_warn = now
            logger.warning(cli_gettext("The main cache used for login throttling is broken (%s: %s); the database cache table is used instead. Check the cache folder / Redis."),
                           type(exc).__name__, exc)
        return fn(fb)


def _bump_on(c, key: str, ttl: int = COUNT_SECONDS) -> int:
    c.add(key, 0, ttl)
    try:
        return c.incr(key)
    except ValueError:
        c.set(key, 1, ttl)
        return 1


def is_locked(request, identifier: str) -> bool:
    """True while the visitor has to wait: after MAX_ATTEMPTS failures for this (IP, account), or too many from this IP in all."""
    ip = client_ip(request)
    k1, k2 = _lock_key(_key(ip, identifier)), (_lock_key(_ip_key(ip)) if ip else None)  # IP نامعلوم ⇒ محدودیت سراسری IP نیست
    return _with_cache(lambda c: bool(c.get(k1)) or (k2 is not None and bool(c.get(k2))))


def register_failure(request, identifier: str) -> None:
    ip = client_ip(request)
    k1, k2, k3 = _key(ip, identifier), (_ip_key(ip) if ip else None), _account_key(identifier)

    def bump(c):
        n_pair = _bump_on(c, k1)
        wait = _wait_for(n_pair, PAIR_TIERS)
        if wait:
            c.set(_lock_key(k1), 1, wait)
        if k2 is not None:
            wait = _wait_for(_bump_on(c, k2), IP_TIERS)
            if wait:
                c.set(_lock_key(k2), 1, wait)
        _bump_on(c, k3)

    _with_cache(bump)


def reset(request, identifier: str) -> None:
    """A correct password clears the (IP, account) slate and the account's own counter; the IP-wide count stays (it counts guesses against anybody)."""
    k = _key(client_ip(request), identifier)
    _with_cache(lambda c: (c.delete(k), c.delete(_lock_key(k)), c.delete(_account_key(identifier))))


def failure_counts(request, identifier: str) -> tuple[int, int, int]:
    """(failures of this IP + account, failures of this IP, failures against this account from anywhere)."""
    ip = client_ip(request)
    keys = (_key(ip, identifier), _ip_key(ip) if ip else None, _account_key(identifier))
    return _with_cache(lambda c: tuple(int(c.get(k, 0) or 0) if k else 0 for k in keys))


def allow_action(request, kind: str, limit: int, window: int = 3600) -> bool:
    """A plain per-IP rate limit for an action (every try counts). With an unknown IP nothing is limited here (the mail limits still apply)."""
    ip = client_ip(request)
    if not ip:
        return True
    key = f"action:{kind}:{ip}"
    return _with_cache(lambda c: _bump_on(c, key, window)) <= limit


def allow_send(request, email: str, kind: str) -> bool:
    """محدودیت نرخ ارسال ایمیل (بازیابی رمز/تأیید ایمیل) به‌ازای IP و به‌ازای ایمیل. هر درخواست شمرده می‌شود (موفق یا نه)،
    تا نتوان با درخواست مکرر صندوق کسی را پر کرد یا از وجود/نبود ایمیل سر درآورد. True = مجاز است.
    ایمیل در کلید به‌صورت هش می‌آید (آدرس خام در کش نمی‌ماند)."""
    import hashlib

    ip = client_ip(request)
    digest = hashlib.sha256((email or "").strip().lower().encode()).hexdigest()[:32]
    k_ip, k_mail = (f"mail-send:{kind}:ip:{ip}" if ip else None), f"mail-send:{kind}:em:{digest}"
    window = settings.RAVIX_MAIL_WINDOW_SECONDS

    def hit(c):
        counts = []
        for key in (k_ip, k_mail):
            if key is None:  # IP نامعلوم: فقط محدودیت به‌ازای ایمیل
                counts.append(0)
                continue
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
        logger.warning(cli_gettext("Email sending rate limit (%s) was triggered for IP %s."), kind, ip or cli_gettext("unknown"))
    return allowed
