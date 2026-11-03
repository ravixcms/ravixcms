"""
تنظیمات پایه Ravix.

تمام مقادیر حساس از متغیرهای محیطی (یا فایل .env) خوانده می‌شوند.
"""
from apps.core.clilang import cli_gettext
import os
import sys
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv

from apps.core import official
from apps.core.deprecation import getenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

load_dotenv(BASE_DIR / ".env")


_raw = getenv  # RAVIX_* با بازگشت به RAVIN_* (منسوخ)


def env(name: str, default=None):
    value = _raw(name)
    return default if value is None else value


def env_bool(name: str, default: bool = False) -> bool:
    value = _raw(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, *, lo: int = 0) -> int:
    value = _raw(name)
    try:
        return max(lo, int(value)) if value is not None else default
    except ValueError:
        return default


def env_list(name: str, default: str = "") -> list[str]:
    value = _raw(name)
    return [item.strip() for item in (default if value is None else value).split(",") if item.strip()]


# ---------------------------------------------------------------------------
# امنیت
# ---------------------------------------------------------------------------
# SECRET_KEY در development مقدار پیش‌فرض ناامن دارد و در production اجباری است.
SECRET_KEY = env("DJANGO_SECRET_KEY", "")
DEBUG = False
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# ---------------------------------------------------------------------------
# اپلیکیشن‌ها
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sitemaps",
]

THIRD_PARTY_APPS = [
    "rest_framework",
]

# ماژول‌های Ravix؛ ماژول‌های آینده (محتوا، رسانه، افزونه‌ها و ...) اینجا اضافه می‌شوند.
# Built-in modules without a saved on/off row: off (a new module waits for its first activation in Plugins). Tests turn it on.
BUILTIN_MODULES_DEFAULT_ON = env_bool("RAVIX_BUILTIN_MODULES_DEFAULT_ON", False)
STUDIO_ENABLED = env_bool("RAVIX_STUDIO_ENABLED", True)  # false: Ravix Studio's editor is locked off (pages already published keep showing)
STUDIO_MAX_VERSIONS = env_int("RAVIX_STUDIO_MAX_VERSIONS", 50)  # versions kept per page/post (the published one and a pending one are never removed)
STUDIO_INLINE_CSS_MAX = env_int("RAVIX_STUDIO_INLINE_CSS_MAX", 50_000)  # bytes; bigger CSS is served as a hashed file
SHOP_ENABLED = env_bool("RAVIX_SHOP_ENABLED", True)  # false: ماژول فروشگاه اصلاً نصب/بارگذاری نمی‌شود (جدول، مسیر، منو، API ندارد)

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.audit",
    "apps.mcp",
    "apps.content",
    "apps.comments",
    "apps.media_library",
    "apps.themes",
    "apps.builder",  # legacy page builder: only its tables remain, to import them into Studio
    "apps.studio",
    "apps.plugins",
    "apps.sites",
    "apps.pluginhub",
    "apps.updater",
    "apps.public",
    "apps.dashboard",
    "apps.installer",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS + (["apps.shop"] if SHOP_ENABLED else [])

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # فشرده‌سازی پاسخ‌های HTML (سرعت بارگذاری). توکن CSRF جنگو در هر پاسخ ماسک می‌شود و حمله BREACH را خنثی می‌کند.
    "django.middleware.gzip.GZipMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "apps.core.pagecache.PageCacheMiddleware",  # ready-made pages for visitors who are not logged in (off unless enabled; see apps/core/pagecache.py)
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "apps.installer.middleware.InstallGateMiddleware",  # تا پایان نصب همه‌چیز را به /install/ می‌برد
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "apps.accounts.middleware.ForcePasswordChangeMiddleware",  # a flagged person can only change their password or sign out
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.NumericOverflowMiddleware",  # عدد بزرگ در query ← ۴۰۰ به‌جای ۵۰۰
    "apps.core.middleware.SecurityHeadersMiddleware",  # CSP برای پنل/حساب‌ها/API + Permissions-Policy
    "django.middleware.http.ConditionalGetMiddleware",  # ETag/304 برای صفحات عمومی
    "apps.updater.middleware.MaintenanceMiddleware",  # ۵۰۳ فقط در بازهٔ اعمال ارتقا
    "apps.core.middleware.SiteRegionalMiddleware",  # زبان/منطقهٔ زمانی انتخابی مدیر (SiteSettings)
    "apps.sites.middleware.CurrentSiteMiddleware",
    "apps.core.middleware_modules.ModuleGateMiddleware",  # a switched-off built-in module (Ravix Shop ...) answers 404 on its routes  # request.site (تنبل؛ فقط در صورت استفاده کوئری می‌زند)
    "apps.content.middleware.RedirectFallbackMiddleware",  # ریدایرکت مسیرهای قدیمی (فقط روی پاسخ ۴۰۴)
]

# کش: پیش‌فرض حافظهٔ محلی (توسعه)؛ production آن را عوض می‌کند. «throttle_fallback» جدول دیتابیس است (مهاجرت apps.core.0003)
# و فقط وقتی استفاده می‌شود که کش اصلی خراب باشد (apps/accounts/throttle.py).
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    "throttle_fallback": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "ravin_cache"},
}

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "OPTIONS": {
            # لودر افزونه‌ها فقط نام‌های «plugin:<slug>/…» را از افزونه‌های فعال و تأییدشده می‌دهد؛ نمی‌تواند قالب‌های هسته را بپوشاند.
            "loaders": [
                ("django.template.loaders.cached.Loader", [
                    "django.template.loaders.filesystem.Loader",
                    "django.template.loaders.app_directories.Loader",
                ]),
                "apps.plugins.template_loader.Loader",  # بدون کش: هر بار وضعیت تأیید را می‌سنجد
            ],
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.site_settings",
                "apps.mcp.context.mcp",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# پایگاه داده: SQLite برای توسعه، PostgreSQL برای تولید (از طریق DATABASE_URL)
# ---------------------------------------------------------------------------


def _database_from_env() -> dict:
    url = env("DATABASE_URL", "")
    if not url:
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    parsed = urlparse(url)
    if parsed.scheme in {"postgres", "postgresql", "pgsql"}:
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": parsed.path.lstrip("/"),
            "USER": parsed.username or "",
            "PASSWORD": parsed.password or "",
            "HOST": parsed.hostname or "localhost",
            "PORT": str(parsed.port or 5432),
            "CONN_MAX_AGE": int(env("DB_CONN_MAX_AGE", "60")),
        }
    if parsed.scheme in {"mysql", "mariadb"}:
        # Shared hosting: MySQL/MariaDB through PyMySQL (pure Python; mysqlclient usually cannot be compiled there).
        import pymysql

        pymysql.install_as_MySQLdb()
        query = dict(part.split("=", 1) for part in parsed.query.split("&") if "=" in part)
        options = {"charset": "utf8mb4", "init_command": "SET sql_mode='STRICT_TRANS_TABLES'"}
        if query.get("unix_socket"):
            options["unix_socket"] = unquote(query["unix_socket"])
        return {
            "ENGINE": "django.db.backends.mysql",
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "localhost",
            "PORT": str(parsed.port or 3306),
            "CONN_MAX_AGE": int(env("DB_CONN_MAX_AGE", "60")),
            "OPTIONS": options,
            "TEST": {"CHARSET": "utf8mb4", "COLLATION": "utf8mb4_unicode_ci"},
        }
    if parsed.scheme == "sqlite":
        path = parsed.path.lstrip("/") or "db.sqlite3"
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / path}
    raise ValueError(cli_gettext("DATABASE_URL with the scheme “%(a1)s” is not supported.") % {"a1": parsed.scheme})


DATABASES = {"default": _database_from_env()}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# احراز هویت
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["apps.accounts.backends.EmailOrUsernameBackend"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "apps.accounts.password_validators.LengthPolicyValidator"},  # the minimum length is an account setting (never below 8)
    {"NAME": "apps.accounts.password_validators.CommonPasswordPolicyValidator"},  # Django's list + the local Persian/ravix list (apps/accounts/data)
    {"NAME": "apps.accounts.password_validators.SiteContextValidator"},  # site name, domain, product name, the person's own details
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Argon2id first (when the argon2-cffi package is installed; it is in requirements*.txt). Old PBKDF2 hashes keep working and are re-made as Argon2
# the next time their owner signs in. Without the package the list falls back to PBKDF2 and `ravix_preflight` says so.
import importlib.util as _importlib_util  # noqa: E402

PASSWORD_HASHERS = [
    *(["apps.accounts.hashers.RavixArgon2PasswordHasher"] if _importlib_util.find_spec("argon2") else []),
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
]

# --- ایمیل: بازیابی رمز و تأیید ایمیل ---
PASSWORD_RESET_TIMEOUT = int(env("RAVIX_PASSWORD_RESET_TIMEOUT", str(60 * 60)))  # ثانیه؛ پیوند بازیابی (یک‌بارمصرف) ۱ ساعت اعتبار دارد
RAVIX_EMAIL_VERIFY_MAX_AGE = int(env("RAVIX_EMAIL_VERIFY_MAX_AGE", str(60 * 60 * 24 * 3)))  # ثانیه؛ پیوند تأیید ایمیل ۳ روز
# اگر روشن باشد، ورود تا تأیید ایمیل ممکن نیست (فقط وقتی SMTP کار می‌کند روشن کنید؛ پیش‌فرض خاموش تا نصب بدون SMTP قفل نشود)
RAVIX_REQUIRE_EMAIL_VERIFICATION = env_bool("RAVIX_REQUIRE_EMAIL_VERIFICATION", False)
RAVIX_MAIL_WINDOW_SECONDS = int(env("RAVIX_MAIL_WINDOW_SECONDS", str(60 * 60)))
RAVIX_MAIL_LIMIT_PER_EMAIL = int(env("RAVIX_MAIL_LIMIT_PER_EMAIL", "3"))  # ارسال در هر بازه به‌ازای هر ایمیل
RAVIX_MAIL_LIMIT_PER_IP = int(env("RAVIX_MAIL_LIMIT_PER_IP", "10"))  # ارسال در هر بازه به‌ازای هر IP

# Self-hosted proof-of-work check (no third party, no tracking). Sign-up, password recovery and "resend verification" always ask for it;
# sign-in asks for it only after a few failed attempts. Each extra bit doubles the work the visitor's browser does.
RAVIX_CAPTCHA_ENABLED = env_bool("RAVIX_CAPTCHA_ENABLED", True) and "test" not in sys.argv  # off while running the test suite; the captcha tests switch it on
RAVIX_CAPTCHA_BITS = int(env("RAVIX_CAPTCHA_BITS", "18"))  # about a quarter of a million hashes (well under a second in a browser)
RAVIX_CAPTCHA_MAX_AGE = int(env("RAVIX_CAPTCHA_MAX_AGE", "900"))  # seconds a challenge stays valid (and each one works once)
RAVIX_CAPTCHA_LOGIN_AFTER = int(env("RAVIX_CAPTCHA_LOGIN_AFTER", "3"))  # failed sign-ins (same IP + account) before the check appears
RAVIX_REGISTER_LIMIT_PER_IP = int(env("RAVIX_REGISTER_LIMIT_PER_IP", "10"))  # sign-up submissions per IP per hour
RAVIX_COMMENT_LIMIT_PER_IP = int(env("RAVIX_COMMENT_LIMIT_PER_IP", "5"))  # دیدگاه در هر بازه به‌ازای هر IP (همهٔ تلاش‌ها شمرده می‌شود)
RAVIX_COMMENT_WINDOW_SECONDS = int(env("RAVIX_COMMENT_WINDOW_SECONDS", "600"))
RAVIX_COMMENT_MAX_DEPTH = int(env("RAVIX_COMMENT_MAX_DEPTH", "2"))  # عمق پاسخ: دیدگاه اصلی ۰، پاسخ ۱، پاسخ به پاسخ ۲
RAVIX_COMMENT_MAX_LENGTH = int(env("RAVIX_COMMENT_MAX_LENGTH", "2000"))
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "Ravix <noreply@localhost>")
EMAIL_TIMEOUT = int(env("EMAIL_TIMEOUT", "10"))  # قطع‌شدن SMTP نباید ورکر وب را معلق نگه دارد

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:home"
LOGOUT_REDIRECT_URL = "accounts:login"

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------
# LANGUAGE_CODE is only the last-resort language of the web interface (used when the site settings hold no valid default language;
# existing sites keep their stored default, "fa" for every site created before multilingual support).
# RAVIX_CLI_LANG alone decides the language of operator-facing text (management commands, start-up errors, preflight, logs):
# English by default, "fa" for Persian.
LANGUAGE_CODE = env("LANGUAGE_CODE", "en")
RAVIX_CLI_LANG = env("RAVIX_CLI_LANG", "")
# Language names are endonyms (each in its own language) and are intentionally not translated.
LANGUAGES = [
    ("fa", "فارسی"),
    ("en", "English"),
    ("ar", "العربية"),
    ("tr", "Türkçe"),
    ("es", "Español"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("ru", "Русский"),
    ("pt-br", "Português (Brasil)"),
    ("zh-hans", "简体中文"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
# The visitor's / user's chosen UI language (see apps.core.regional.resolve_language).
LANGUAGE_COOKIE_NAME = "ravix_lang"
LANGUAGE_COOKIE_AGE = 60 * 60 * 24 * 365
LANGUAGE_COOKIE_PATH = "/"
LANGUAGE_COOKIE_SAMESITE = "Lax"
LANGUAGE_COOKIE_HTTPONLY = True
TIME_ZONE = "Asia/Tehran"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# فایل‌های استاتیک و رسانه
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
# ---------------------------------------------------------------------------
# قالب‌های سایت (Themes)
# ---------------------------------------------------------------------------
# هر قالب یک پوشه است: themes/<slug>/{theme.json, templates/, static/}. پوشه‌های قالب فقط از مسیرهای زیر خوانده می‌شوند.
# نصب قالب جدید = کپی پوشه آن در همین مسیرها توسط مدیر سرور، یا (اگر RAVIX_THEME_WEB_INSTALL=true) بارگذاری ZIP توسط مدیرکل در پنل.
# --- افزونه‌ها (مرحله پنجم) ---
# فقط پوشه‌های این فهرست (تنظیم سرور) برای افزونه بررسی می‌شوند؛ بارگذاری کد از هیچ مسیر دیگری ممکن نیست.
RAVIX_PLUGIN_DIRS = [BASE_DIR / "plugins"] + [Path(p) for p in env_list("RAVIX_EXTRA_PLUGIN_DIRS")]
# نصب‌شده‌ها از طریق پنل/مخزن در این پوشه قرار می‌گیرند (جدا از plugins/ که می‌تواند برای سرویس فقط‌خواندنی باشد).
RAVIX_PLUGIN_INSTALL_DIR = Path(env("RAVIX_PLUGIN_INSTALL_DIR", str(BASE_DIR / "plugins_installed")))
RAVIX_PLUGIN_DIRS = RAVIX_PLUGIN_DIRS + [RAVIX_PLUGIN_INSTALL_DIR]
RAVIX_PLUGIN_BACKUP_DIR = Path(env("RAVIX_PLUGIN_BACKUP_DIR", str(BASE_DIR / "plugin_backups")))
# Private files of plugins (one sub-folder per plugin), outside the public media; never executed. With nginx in front, RAVIX_ACCEL_REDIRECT_PREFIX
# (e.g. /_plugin_files/, an ``internal`` location aliased to this folder) lets nginx send the files instead of the Gunicorn workers.
RAVIX_PLUGIN_DATA_DIR = Path(env("RAVIX_PLUGIN_DATA_DIR", str(BASE_DIR / "plugin_data")))
RAVIX_ACCEL_REDIRECT_PREFIX = env("RAVIX_ACCEL_REDIRECT_PREFIX", "")
RAVIX_PLUGIN_BACKUPS_KEEP = int(env("RAVIX_PLUGIN_BACKUPS_KEEP", "5"))
# نصب از طریق وب (آپلود/مخزن) صراحتاً باید روشن شود؛ بدون آن فقط نصب دستی پوشه‌ای + تأیید در پنل ممکن است.
RAVIX_PLUGIN_WEB_INSTALL = env_bool("RAVIX_PLUGIN_WEB_INSTALL", False)
RAVIX_PLUGINS_ALLOW_UNSIGNED = env_bool("RAVIX_PLUGINS_ALLOW_UNSIGNED", False)  # بستهٔ بدون امضای معتبر (فقط آپلود محلی؛ مخزن همیشه امضا می‌خواهد)
RAVIX_TRUSTED_PUBLIC_KEYS = env_list("RAVIX_TRUSTED_PUBLIC_KEYS")  # «برچسب:دامنه:کلید_عمومی_base64» (دامنه: plugin، core یا plugin+core)؛ بدون دامنه = هر دو (قدیمی، با هشدار preflight). کلیدهای بیشتر در پنل (مدیرکل)
# پیش‌فرض‌ها از apps/core/official.py می‌آیند و با متغیر محیطی عوض می‌شوند؛ مقدار خالیِ صریح (RAVIX_OFFICIAL_REGISTRY_URL=) آن را خاموش می‌کند.
RAVIX_OFFICIAL_REGISTRY_URL = env("RAVIX_OFFICIAL_REGISTRY_URL", official.OFFICIAL_REGISTRY_URL).strip().rstrip("/")  # خالی: مخزن همین نمونه (اگر روشن باشد)
RAVIX_REVOKED_KEY_IDS = [k.lower() for k in env_list("RAVIX_REVOKED_KEY_IDS")]  # شناسهٔ کلیدهای ابطال‌شده (افزون بر official.REVOKED_KEY_IDS)
RAVIX_PLUGINS_ENABLED = env_bool("RAVIX_PLUGINS_ENABLED", True)  # false: سیستم افزونه کاملاً خاموش
# حالت امن (بازیابی): کد هیچ افزونه‌ای بارگذاری یا اجرا نمی‌شود؛ پنل و مدیریت افزونه‌ها کار می‌کند.
RAVIX_PLUGINS_SAFE_MODE = env_bool("RAVIX_PLUGINS_SAFE_MODE", False)
# غیرفعال‌سازی خودکار: اگر یک افزونه در این بازه (ثانیه) به این تعداد خطا بدهد.
RAVIX_PLUGIN_ERROR_LIMIT = int(env("RAVIX_PLUGIN_ERROR_LIMIT", "5"))
RAVIX_PLUGIN_ERROR_WINDOW = int(env("RAVIX_PLUGIN_ERROR_WINDOW", "600"))

# پوشهٔ نصب قالب‌های بارگذاری‌شده از پنل (ZIP)؛ جدا از themes/ (که می‌تواند برای سرویس فقط‌خواندنی باشد).
# فقط وقتی RAVIX_THEME_WEB_INSTALL=true باشد و این پوشه برای کاربر سرویس قابل‌نوشتن باشد کار می‌کند (docs/themes.md).
RAVIX_THEME_INSTALL_DIR = Path(env("RAVIX_THEME_INSTALL_DIR", str(BASE_DIR / "themes_installed")))
RAVIX_THEME_WEB_INSTALL = env_bool("RAVIX_THEME_WEB_INSTALL", False)  # پیش‌فرض خاموش؛ روشن‌کردن آگاهانه است
RAVIX_THEME_DIRS = [BASE_DIR / "themes"] + [Path(p) for p in env_list("RAVIX_EXTRA_THEME_DIRS")] + [RAVIX_THEME_INSTALL_DIR]
# نشانی اصلی سایت (مثلاً https://example.com) برای canonical، sitemap و Open Graph؛ اگر خالی باشد از Host درخواست ساخته می‌شود.
SITE_URL = env("RAVIX_SITE_URL", "").rstrip("/")
RAVIX_FEED_ITEMS = int(env("RAVIX_FEED_ITEMS", "20"))  # تعداد مورد در هر فید RSS/Atom (۱ تا ۱۰۰)
RAVIX_PAGE_CACHE = env("RAVIX_PAGE_CACHE", "off").lower()  # on|off server default; the panel (Site settings ▸ Speed) can override it
RAVIX_PAGE_CACHE_TTL = int(env("RAVIX_PAGE_CACHE_TTL", "900"))
PUBLIC_PAGE_SIZE = int(env("RAVIX_PUBLIC_PAGE_SIZE", "9"))
# مدت کش مرورگر/CDN برای صفحات عمومی بازدیدکنندگان ناشناس (ثانیه). ۰ یعنی همیشه با ETag بازبینی شود.
PUBLIC_CACHE_SECONDS = int(env("RAVIX_PUBLIC_CACHE_SECONDS", "0"))
THEME_SLUG_PATTERN = r"[a-z0-9][a-z0-9_-]{0,48}"


def _theme_static_dirs(theme_dirs):
    """فایل‌های استاتیک هر قالب را با پیشوند themes/<slug>/ به خط لوله استاتیک جنگو اضافه می‌کند."""
    found = []
    for base in theme_dirs:
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            static_dir = child / "static"
            if (
                re.fullmatch(THEME_SLUG_PATTERN, child.name)
                and child.is_dir()
                and not child.is_symlink()
                and static_dir.is_dir()
                and not static_dir.is_symlink()
            ):
                found.append((f"themes/{child.name}", static_dir))
    return found


# قالب‌های نصب‌شده از پنل (RAVIX_THEME_INSTALL_DIR) عمداً اینجا نیستند: استاتیکشان را apps.themes.assets از /theme-assets/ سرو می‌کند
# و به collectstatic/manifest نیازی ندارد.
STATICFILES_DIRS = [BASE_DIR / "static", *_theme_static_dirs([d for d in RAVIX_THEME_DIRS if Path(d) != Path(RAVIX_THEME_INSTALL_DIR)])]
STATIC_ROOT = BASE_DIR / "staticfiles"
# مدت کش مرورگر برای فایل‌های استاتیک (ثانیه؛ فقط در حالت DEBUG=False اثر دارد). نشانی فایل‌های هر قالب با ?v=<نسخه قالب>
# ساخته می‌شود؛ پس با افزایش نسخه در theme.json کش مرورگر بازدیدکنندگان خودکار نو می‌شود.
WHITENOISE_MAX_AGE = int(env("RAVIX_STATIC_MAX_AGE", "3600"))
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
TEST_RUNNER = "config.testrunner.RavixTestRunner"  # آزمون‌ها با MEDIA_ROOT موقت اجرا می‌شوند

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

# حداکثر حجم آپلود لوگو (بایت) - اعتبارسنجی در فرم هم انجام می‌شود
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}

# ---------------------------------------------------------------------------
# کوکی‌ها و نشست
# ---------------------------------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
RAVIX_CSP_REPORT_ONLY = env_bool("RAVIX_CSP_REPORT_ONLY", False)  # true: فقط گزارش (برای عیب‌یابی)؛ پیش‌فرض اعمال کامل
# پراکسی‌های مورداعتماد (IP/CIDR، با کاما) برای خواندن X-Forwarded-For / X-Real-IP. سوکت یونیکس همیشه مورداعتماد است؛ برای Nginx روی TCP
# مثلاً RAVIX_TRUSTED_PROXIES=127.0.0.1. منطق: apps/core/clientip.py
RAVIX_TRUSTED_PROXIES = env_list("RAVIX_TRUSTED_PROXIES")
RAVIX_ADMIN_PATH = env("RAVIX_ADMIN_PATH", "django-admin").strip("/")  # مسیر django-admin را عوض کنید یا در Nginx محدود کنید

# ---------------------------------------------------------------------------
# ثبت‌نام
# ---------------------------------------------------------------------------
# اگر False باشد ثبت‌نام عمومی غیرفعال می‌شود (قابل تغییر از تنظیمات سایت نیز هست).
REGISTRATION_DEFAULT_OPEN = env_bool("RAVIX_REGISTRATION_OPEN", False)  # a fresh install starts with public sign-up CLOSED; an administrator opens it

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "[{levelname}] {asctime} {name}: {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", "INFO")},
}
CSRF_FAILURE_VIEW = "apps.core.views.csrf_failure"

# در اجرای تست‌ها از هشر سریع استفاده می‌شود (فقط برای تست؛ هرگز در اجرای واقعی).
import sys  # noqa: E402

if "test" in sys.argv:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# نصب‌کنندهٔ وب (apps/installer): تا ساخته‌شدن مدیرکل همه‌چیز را به /install/ می‌برد. در اجرای تست‌ها خاموش است.
RAVIX_INSTALLER_ENABLED = env_bool("RAVIX_INSTALLER_ENABLED", True) and "test" not in sys.argv
RAVIX_INSTALL_TOKEN = env("RAVIX_INSTALL_TOKEN", "")  # اگر خالی باشد توکن تصادفی ساخته و در لاگ/فایل گذاشته می‌شود
RAVIX_INSTALL_TOKEN_FILE = Path(env("RAVIX_INSTALL_TOKEN_FILE", str(BASE_DIR / "install_token")))

# ---------------------------------------------------------------------------
# چندسایتی و فروشگاه (مرحله ششم)
# ---------------------------------------------------------------------------
# false (پیش‌فرض): فقط «سایت پیش‌فرض» و دسترسی بر پایهٔ نقش سراسری. true: تشخیص سایت با دامنه + عضویت الزامی برای پنل.
RAVIX_MULTISITE_ENABLED = env_bool("RAVIX_MULTISITE_ENABLED", False)
SHOP_CURRENCY_LABEL = env("RAVIX_SHOP_CURRENCY_LABEL", "تومان")
SHOP_CURRENCY_CODE = env("RAVIX_SHOP_CURRENCY_CODE", "IRT").upper()  # IRT تومان | IRR ریال؛ درگاه باید از آن پشتیبانی کند
# کلید(های) Fernet برای رمزنگاری اسرار درگاه‌ها (جداشده با ویرگول؛ اولی رمز می‌کند، بقیه فقط برای چرخش). تولید: python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
RAVIX_SECRETS_KEYS = env_list("RAVIX_SECRETS_KEY")
RAVIX_SECRETS_ALLOW_DERIVED_KEY = env_bool("RAVIX_SECRETS_ALLOW_DERIVED_KEY", False)  # فقط دمو: مشتق از SECRET_KEY (ناامن‌تر برای چرخش)
SHOP_GATEWAY_ALLOW_PRIVATE_HOSTS = env_bool("RAVIX_SHOP_GATEWAY_ALLOW_PRIVATE_HOSTS", False)  # فقط توسعه: اجازهٔ اتصال به IP خصوصی
SHOP_PAYMENT_GATEWAY = env("RAVIX_SHOP_PAYMENT_GATEWAY", "sandbox")  # sandbox | (درگاه واقعی پس از افزودن آداپتور)
SHOP_ALLOW_SANDBOX_IN_PRODUCTION = env_bool("RAVIX_SHOP_ALLOW_SANDBOX_IN_PRODUCTION", False)
SHOP_PENDING_ORDER_MINUTES = int(env("RAVIX_SHOP_PENDING_ORDER_MINUTES", "30"))  # مهلت پرداخت؛ پس از آن سفارش لغو و موجودی برمی‌گردد
SHOP_PAGE_SIZE = int(env("RAVIX_SHOP_PAGE_SIZE", "12"))
SHOP_ALLOW_GUEST_CHECKOUT = env_bool("RAVIX_SHOP_ALLOW_GUEST_CHECKOUT", True)  # false: خرید فقط برای کاربر واردشده
SHOP_PRIVATE_ROOT = Path(env("RAVIX_SHOP_PRIVATE_ROOT", str(BASE_DIR / "shop_private")))  # فایل‌های دیجیتال؛ خارج از media
SHOP_DIGITAL_MAX_BYTES = int(env("RAVIX_SHOP_DIGITAL_MAX_BYTES", str(200 * 1024 * 1024)))
SHOP_DIGITAL_DOWNLOAD_LIMIT = int(env("RAVIX_SHOP_DIGITAL_DOWNLOAD_LIMIT", "5"))  # حداکثر دانلود هر قلم سفارش

# --- مخزن افزونه‌ها (apps.pluginhub) ---
RAVIX_REGISTRY_ENABLED = env_bool("RAVIX_REGISTRY_ENABLED", True)  # false: API عمومی و انتشار خاموش (پنل Directory خالی می‌ماند)
RAVIX_REGISTRY_NAME = env("RAVIX_REGISTRY_NAME", "Ravix Plugin Registry")
RAVIX_REGISTRY_STORAGE = Path(env("RAVIX_REGISTRY_STORAGE", str(BASE_DIR / "registry_packages")))  # خارج از media و static
RAVIX_REGISTRY_MAX_PACKAGE_BYTES = int(env("RAVIX_REGISTRY_MAX_PACKAGE_BYTES", str(5 * 1024 * 1024)))
RAVIX_REGISTRY_ALLOW_INSECURE_DOWNLOAD = env_bool("RAVIX_REGISTRY_ALLOW_INSECURE_DOWNLOAD", False)  # فقط توسعهٔ محلی
RAVIX_REGISTRY_SIGNING_KEY_FILE = env("RAVIX_REGISTRY_SIGNING_KEY_FILE", "")  # مسیر فایل seed کلید امضا (مجوز 600، خارج از مخزن کد)
RAVIX_REGISTRY_SIGNING_KEY = env("RAVIX_REGISTRY_SIGNING_KEY", "")  # جایگزین فایل (seed در base64)؛ فقط از env/secret manager


# ---------------------------------------------------------------------------
# به‌روزرسانی یکپارچه (apps.updater) — docs/upgrade.md
# ---------------------------------------------------------------------------
# پیوندهای رسمی پروژه (نمایش در پنل، نصب‌کننده و User-Agent درخواست‌های به‌روزرسانی)؛ با متغیر محیطی قابل تغییرند.
RAVIX_WEBSITE_URL = env("RAVIX_WEBSITE_URL", official.OFFICIAL_WEBSITE_URL).strip().rstrip("/")
RAVIX_DOCS_URL = env("RAVIX_DOCS_URL", official.OFFICIAL_DOCS_URL).strip().rstrip("/")
RAVIX_REPO_URL = env("RAVIX_REPO_URL", official.OFFICIAL_REPO_URL).strip().rstrip("/")
RAVIX_UPDATE_URL = env("RAVIX_UPDATE_URL", official.OFFICIAL_UPDATE_URL).strip().rstrip("/")  # منبع رسمی (فقط HTTPS): <URL>/updates/v1/index.json
RAVIX_UPDATE_DIR = env("RAVIX_UPDATE_DIR", "")  # منبع پوشه‌ای (آفلاین/آزمایشی): index.json + بسته‌ها؛ فقط تنظیم سرور
RAVIX_UPDATE_WEB_APPLY = env_bool("RAVIX_UPDATE_WEB_APPLY", False)  # false: اعمال ارتقا فقط با دستور مدیریتی (check/نمایش همیشه آزاد است)
RAVIX_UPDATE_APP_ROOT = Path(env("RAVIX_UPDATE_APP_ROOT", str(BASE_DIR)))  # ریشهٔ کدی که ارتقا می‌یابد
RAVIX_UPDATE_WORK_DIR = Path(env("RAVIX_UPDATE_WORK_DIR", str(BASE_DIR / "update_work")))  # دانلود/استخراج موقت (باید روی همان فایل‌سیستم کد باشد)
RAVIX_UPDATE_BACKUP_DIR = Path(env("RAVIX_UPDATE_BACKUP_DIR", str(BASE_DIR / "update_backups")))  # پشتیبان‌های پیش از ارتقا (مجوز ۰۷۰۰)
RAVIX_UPDATE_BACKUPS_KEEP = int(env("RAVIX_UPDATE_BACKUPS_KEEP", "3"))
RAVIX_UPDATE_MAX_PACKAGE_BYTES = int(env("RAVIX_UPDATE_MAX_PACKAGE_BYTES", str(80 * 1024 * 1024)))
RAVIX_UPDATE_LOCK_STALE_MINUTES = int(env("RAVIX_UPDATE_LOCK_STALE_MINUTES", "60"))
RAVIX_UPDATE_REQUIRE_SIGNED_INDEX = env_bool("RAVIX_UPDATE_REQUIRE_SIGNED_INDEX", False)  # فهرست هم باید امضا (index.json.sig) داشته باشد
