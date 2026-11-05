"""Settings for shared hosting (cPanel "Setup Python App": Passenger or LiteSpeed LSAPI), without root.

Differences from ``production`` (the VPS settings, which stay untouched):

* Private files (env file, SQLite, caches, logs, backups, plugin data) live in a data folder outside the code folder and outside
  public_html (``hosting.data_dir()``); nothing here ever writes to disk at import time except in *setup mode*.
* Setup mode: no ``ravix.env`` yet, so the web installer is the only thing the site shows (and it is locked behind a one-time install key).
  The installer writes the env file and the next process starts in normal mode. If the install lock exists but the env file is gone,
  the site fails closed instead of reopening the installer.
* Media is written under public_html so the web server serves it directly (with a safe .htaccess); static files come from WhiteNoise.
* MySQL/MariaDB (PyMySQL) or SQLite (with a warning); PostgreSQL still works. File cache by default, Redis when ``REDIS_URL`` is set.
* DEBUG is always off.
"""
import os
import secrets
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from apps.core import hosting
from apps.core.clilang import cli_gettext

hosting.load_env_into_environ()

DATA_DIR = hosting.data_dir()
SETUP_MODE = not os.environ.get("DJANGO_SECRET_KEY", "").strip() and not os.environ.get("RAVIX_SECRET_KEY_FILE", "").strip()
if SETUP_MODE and hosting.installed_lock_exists():
    raise ImproperlyConfigured(cli_gettext("Ravix is installed but its settings file is missing (%(path)s). Restore it from your backup; the installer stays locked.") % {"path": hosting.env_file()})

# Where each kind of private data goes (an explicit environment variable always wins).
for _name, _sub in (
    ("RAVIX_PLUGIN_INSTALL_DIR", "plugins_installed"), ("RAVIX_PLUGIN_BACKUP_DIR", "plugin_backups"), ("RAVIX_PLUGIN_DATA_DIR", "plugin_data"),
    ("RAVIX_THEME_INSTALL_DIR", "themes_installed"), ("RAVIX_SHOP_PRIVATE_ROOT", "shop_private"), ("RAVIX_REGISTRY_STORAGE", "registry_packages"),
    ("RAVIX_UPDATE_WORK_DIR", "update_work"), ("RAVIX_UPDATE_BACKUP_DIR", "update_backups"), ("RAVIX_CACHE_DIR", "cache"),
    ("RAVIX_INSTALL_TOKEN_FILE", "install-key.txt"),
):
    os.environ.setdefault(_name, str(DATA_DIR / _sub))
os.environ.setdefault("RAVIX_UPDATE_WEB_APPLY", "true")  # a host has no shell for most owners: update from the panel
os.environ.setdefault("RAVIX_PAGE_CACHE", "on")
os.environ.setdefault("RAVIX_PLUGIN_WEB_INSTALL", "false")
os.environ.setdefault("RAVIX_SECRETS_ALLOW_DERIVED_KEY", "true")  # the installer creates RAVIX_SECRETS_KEY; this is only the safety net

from .base import *  # noqa: E402,F401,F403
from .base import BASE_DIR, CACHES, CSRF_TRUSTED_ORIGINS, DATABASES, SHOP_ENABLED, env, env_bool, env_int, env_list  # noqa: E402

DEBUG = False
HOSTING = True
RAVIX_HOSTING = True
RAVIX_DATA_DIR = DATA_DIR
RAVIX_ENV_FILE = hosting.env_file()
# safe update: ravix_cron starts the executor (every 5 minutes at most), the restart is tmp/restart.txt, the queue lives in the private data folder
RAVIX_UPDATE_EXECUTOR = RAVIX_UPDATE_EXECUTOR or "cron"  # noqa: F405
RAVIX_UPDATE_RESTART = RAVIX_UPDATE_RESTART or "touch"  # noqa: F405
RAVIX_UPDATE_JOBS_DIR = Path(env("RAVIX_UPDATE_JOBS_DIR", str(DATA_DIR / "jobs")))
RAVIX_INSTALL_KEY_MODE = True  # the installer asks for the one-time install key before showing anything

# ---------------------------------------------------------------- URL prefix (site installed in a sub-folder such as example.com/site/)
_script = env("RAVIX_SCRIPT_NAME", "").strip()
if _script:
    FORCE_SCRIPT_NAME = "/" + _script.strip("/")
else:
    FORCE_SCRIPT_NAME = None

# ---------------------------------------------------------------- setup mode
if SETUP_MODE:
    SECRET_KEY = secrets.token_urlsafe(50)  # per process on purpose: setup state lives in file sessions, never in signed cookies
    ALLOWED_HOSTS = ["*"]
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}  # nothing is created on disk before the installer runs
    SESSION_ENGINE = "django.contrib.sessions.backends.file"
    SESSION_FILE_PATH = str(DATA_DIR / "setup-sessions")
    MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"
    SITE_URL = ""
    try:
        hosting.ensure_dir(DATA_DIR)
        hosting.ensure_dir(Path(SESSION_FILE_PATH))
    except OSError:
        pass  # the installer's host check explains this
else:
    SECRET_KEY = env("DJANGO_SECRET_KEY", "")
    if not SECRET_KEY and env("RAVIX_SECRET_KEY_FILE"):
        try:
            SECRET_KEY = Path(env("RAVIX_SECRET_KEY_FILE")).read_text(encoding="utf-8").strip()
        except OSError:
            SECRET_KEY = ""
    if len(SECRET_KEY) < 32:
        raise ImproperlyConfigured(cli_gettext("DJANGO_SECRET_KEY must be set and at least 32 characters long."))
    if not env("DJANGO_ALLOWED_HOSTS"):
        raise ImproperlyConfigured(cli_gettext("DJANGO_ALLOWED_HOSTS must be set in production."))
    if DATABASES["default"]["ENGINE"].endswith("sqlite3"):
        _db_name = Path(str(DATABASES["default"]["NAME"]))
        if not _db_name.is_absolute() or str(_db_name).startswith(str(BASE_DIR)):
            # never next to the code: on a host the code folder may be reachable from the web
            DATABASES["default"]["NAME"] = DATA_DIR / "db" / "ravix.sqlite3"

# ---------------------------------------------------------------- HTTPS, cookies, headers (the same protections as the VPS settings, without nginx)
_https_site = SITE_URL.startswith("https://")
if not CSRF_TRUSTED_ORIGINS and SITE_URL:  # the site's own address (and its www twin) may post forms; nothing else is trusted
    from urllib.parse import urlsplit as _urlsplit

    _site = _urlsplit(SITE_URL)
    _origins = {f"{_site.scheme}://{_site.netloc}"}
    if _site.hostname and "." in _site.hostname and not _site.hostname.startswith("www.") and not _site.hostname.replace(".", "").isdigit():
        _origins.add(f"{_site.scheme}://www.{_site.netloc}")
    CSRF_TRUSTED_ORIGINS = sorted(_origins)
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", _https_site and not SETUP_MODE)
SESSION_COOKIE_SECURE = env_bool("DJANGO_SESSION_COOKIE_SECURE", _https_site)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
LANGUAGE_COOKIE_SECURE = SESSION_COOKIE_SECURE
SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", "31536000" if _https_site else "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD", False)
# passenger_wsgi.py turns the server's own HTTPS indicators (HTTPS=on, REQUEST_SCHEME) into wsgi.url_scheme. A forwarded-protocol header is
# trusted only when the owner says a proxy (for example Cloudflare) sits in front: otherwise any visitor could forge it.
if env_bool("RAVIX_BEHIND_PROXY", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SILENCED_SYSTEM_CHECKS = [c for c, on in (("security.W005", SECURE_HSTS_INCLUDE_SUBDOMAINS), ("security.W021", SECURE_HSTS_PRELOAD)) if not on]
if DATABASES["default"]["ENGINE"].endswith("mysql"):
    # mysql.W003: unique CharField longer than 255 works on MariaDB >= 10.5 / MySQL 8 (the only versions Django 5.2 accepts);
    # models.W036: conditional unique constraints are created by the shop/sites migrations as generated-column indexes.
    SILENCED_SYSTEM_CHECKS += ["mysql.W003", "models.W036"]

# ---------------------------------------------------------------- email
EMAIL_BACKEND = env("DJANGO_EMAIL_BACKEND", "apps.core.mailbackend.QueuedEmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)

# ---------------------------------------------------------------- static and media
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "apps.core.staticstorage.RavixStaticFilesStorage"},
}
WHITENOISE_MAX_AGE = env_int("RAVIX_STATIC_MAX_AGE", 31536000)
_public_html = hosting.public_html()
_media_default = (_public_html / _script.strip("/") / "media") if _public_html else (DATA_DIR / "media")
MEDIA_ROOT = Path(env("RAVIX_MEDIA_ROOT", str(_media_default)))
MEDIA_URL = env("RAVIX_MEDIA_URL", "media/")

# ---------------------------------------------------------------- cache (a file cache shared by all processes; Redis when available)
_redis = env("REDIS_URL")
if _redis:
    CACHES["default"] = {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": _redis}
else:
    CACHES["default"] = {"BACKEND": "apps.core.cache.SafeFileBasedCache", "LOCATION": env("RAVIX_CACHE_DIR", str(DATA_DIR / "cache"))}

# ---------------------------------------------------------------- limits: small processes on a shared account
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
SESSION_COOKIE_AGE = env_int("RAVIX_SESSION_AGE", 60 * 60 * 24 * 14)
RAVIX_PAGE_CACHE = env("RAVIX_PAGE_CACHE", "on").lower()
RAVIX_PAGE_CACHE_TTL = env_int("RAVIX_PAGE_CACHE_TTL", 900)
RAVIX_KEEP_ALIVE = env_bool("RAVIX_KEEP_ALIVE", True)  # ravix_cron fetches the home page so the application stays warm
RAVIX_BACKUP_KEEP = env_int("RAVIX_BACKUP_KEEP", 7)
RAVIX_BACKUP_MAX_MEDIA_MB = env_int("RAVIX_BACKUP_MAX_MEDIA_MB", 300)  # larger media folders are left out of the daily backup (the database is always backed up)
RAVIX_BACKUP_DIR = Path(env("RAVIX_BACKUP_DIR", str(DATA_DIR / "backups")))

# ---------------------------------------------------------------- logging: a rotating file in the data folder and stderr (Passenger's log)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"std": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "std"}},
    "root": {"handlers": ["console"], "level": env("RAVIX_LOG_LEVEL", "INFO")},
    "loggers": {"django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False}},
}
if not SETUP_MODE and os.access(DATA_DIR, os.W_OK) and "test" not in sys.argv:
    try:
        hosting.ensure_dir(DATA_DIR / "logs")
        LOGGING["handlers"]["file"] = {"class": "logging.handlers.RotatingFileHandler", "formatter": "std", "filename": str(DATA_DIR / "logs" / "ravix.log"),
                                       "maxBytes": 1_000_000, "backupCount": 3, "encoding": "utf-8"}
        LOGGING["root"]["handlers"].append("file")
    except OSError:
        pass

# The web installer writes these; RAVIX_INSTALLER_ENABLED stays on until the lock file exists.
RAVIX_INSTALLER_ENABLED = env_bool("RAVIX_INSTALLER_ENABLED", True) and "test" not in sys.argv and not hosting.installed_lock_exists()
