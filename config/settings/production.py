"""تنظیمات تولید: امنیت سخت‌گیرانه و الزام به متغیرهای محیطی."""
from apps.core.clilang import cli_gettext
import os

from django.core.exceptions import ImproperlyConfigured

from apps.core.deprecation import warn_legacy

from .base import *  # noqa: F401,F403
from .base import DATABASES, SHOP_ALLOW_SANDBOX_IN_PRODUCTION, SHOP_ENABLED, SHOP_PAYMENT_GATEWAY, env, env_bool

DEBUG = False

SECRET_KEY = env("DJANGO_SECRET_KEY", "")
if not SECRET_KEY and env("RAVIX_SECRET_KEY_FILE"):  # مثل Docker secrets: کلید از فایل (مجوز 600) خوانده می‌شود
    try:
        with open(env("RAVIX_SECRET_KEY_FILE"), encoding="utf-8") as _fh:
            SECRET_KEY = _fh.read().strip()
    except OSError:
        SECRET_KEY = ""
if not SECRET_KEY or len(SECRET_KEY) < 32:
    raise ImproperlyConfigured(cli_gettext("DJANGO_SECRET_KEY must be set and at least 32 characters long."))

if not env("DJANGO_ALLOWED_HOSTS"):
    raise ImproperlyConfigured(cli_gettext("DJANGO_ALLOWED_HOSTS must be set in production."))

if DATABASES["default"]["ENGINE"].endswith("sqlite3") and not env_bool("RAVIX_ALLOW_SQLITE_IN_PRODUCTION"):
    raise ImproperlyConfigured(cli_gettext("In production DATABASE_URL must point to a PostgreSQL database."))

SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
LANGUAGE_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_HSTS_INCLUDE_SUBDOMAINS", False)
# preload تقریباً برگشت‌ناپذیر است (حذف از فهرست مرورگرها ماه‌ها طول می‌کشد)؛ پیش‌فرض خاموش، فقط با انتخاب آگاهانه روشن شود
SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD", False)
# W005/W021 («include_subdomains»/«preload» روشن نیست) عمداً: هر دو برگشت‌ناپذیرند و باید آگاهانه روشن شوند (بالا)؛ هشدار فقط وقتی خاموش است ساکت می‌شود
SILENCED_SYSTEM_CHECKS = [c for c, on in (("security.W005", SECURE_HSTS_INCLUDE_SUBDOMAINS), ("security.W021", SECURE_HSTS_PRELOAD)) if not on]

EMAIL_BACKEND = env("DJANGO_EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = int(env("EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)

# --- فایل‌های استاتیک با نام هش‌دار (کش طولانی امن) ---
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
WHITENOISE_MAX_AGE = int(env("RAVIX_STATIC_MAX_AGE", "31536000"))  # نام فایل‌ها هش‌دار است

# The folder is never created here (importing settings must not write to disk): systemd provides it (CacheDirectory=ravix),
# or set RAVIX_CACHE_DIR; ravix_preflight and the site-health page report a missing/read-only folder.
# --- کش مشترک بین ورکرهای Gunicorn (برای محدودیت نرخ و ...) ---
# با REDIS_URL از Redis استفاده می‌شود (پکیج redis لازم است)؛ در غیر این صورت کش فایلی که بین پردازه‌ها مشترک است.
_redis = env("REDIS_URL")
if _redis:
    CACHES["default"] = {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": _redis}
else:
    _cache_default = "/var/cache/ravix"
    if not env("RAVIX_CACHE_DIR") and not os.path.isdir(_cache_default) and os.path.isdir("/var/cache/ravin"):
        # نصب‌های قدیمی (ravin.service با CacheDirectory=ravin) تا 2.0 همان پوشه را به کار می‌برند.
        warn_legacy("/var/cache/ravin", _cache_default)
        _cache_default = "/var/cache/ravin"
    CACHES["default"] = {"BACKEND": "apps.core.cache.SafeFileBasedCache",
                         "LOCATION": env("RAVIX_CACHE_DIR", _cache_default)}
# CACHES["throttle_fallback"] (جدول دیتابیس) از base می‌آید و فقط هنگام خرابی کش اصلی به کار می‌رود.

# --- لاگ: stdout/stderr (systemd/journald آن را نگه می‌دارد) ---
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"std": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "std"}},
    "root": {"handlers": ["console"], "level": env("RAVIX_LOG_LEVEL", "INFO")},
    "loggers": {"django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False}},
}

# پشتیبان‌گیری/پردازش ورودی
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
SESSION_COOKIE_AGE = int(env("RAVIX_SESSION_AGE", str(60 * 60 * 24 * 14)))

# اگر فروشگاه روشن است و درگاه آزمایشی بدون اجازهٔ صریح انتخاب شده، هشدار بلند (checkout هم رد می‌شود)
if SHOP_ENABLED and SHOP_PAYMENT_GATEWAY == "sandbox" and not SHOP_ALLOW_SANDBOX_IN_PRODUCTION:
    import warnings

    warnings.warn(cli_gettext("The shop is on but no real gateway is connected; ordering stays disabled until a gateway is connected."))
