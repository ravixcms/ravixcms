"""تنظیمات توسعه: DEBUG روشن و SQLite به‌صورت پیش‌فرض."""
from .base import *  # noqa: F401,F403
from .base import env, env_bool

DEBUG = True
SECRET_KEY = env("DJANGO_SECRET_KEY") or "dev-only-insecure-key-do-not-use-in-production"
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]", "testserver"]

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
RAVIN_PLUGIN_WEB_INSTALL = env_bool("RAVIN_PLUGIN_WEB_INSTALL", True)  # در توسعه پیش‌فرض روشن
