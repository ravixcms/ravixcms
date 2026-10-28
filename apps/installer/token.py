"""توکن نصب: جلوگیری از اینکه «اولین بازدیدکنندهٔ اینترنت» روی سرور تازه، مدیرکل شود.

منبع توکن (به‌ترتیب): متغیر ``RAVIX_INSTALL_TOKEN`` ← فایل ``RAVIX_INSTALL_TOKEN_FILE`` (مجوز 600) ← ساخت تصادفی و ثبت در همان فایل
و چاپ در لاگ (هشدار). در حالت DEBUG (توسعه) لازم نیست. پس از نصب فایل حذف می‌شود.
"""
from apps.core.clilang import cli_gettext
from django.utils.translation import gettext
import hmac
import logging
import os
import secrets
from pathlib import Path

from django.conf import settings

logger = logging.getLogger("ravix.installer")


def required() -> bool:
    return not settings.DEBUG


def _file() -> Path:
    return Path(settings.RAVIX_INSTALL_TOKEN_FILE)


def expected_token() -> str:
    if getattr(settings, "RAVIX_HOSTING", False):  # shared hosting: the install code of the web installer; it is never written to a log
        from . import hostinstall

        return hostinstall.ensure_key()
    env_value = (getattr(settings, "RAVIX_INSTALL_TOKEN", "") or "").strip()
    if env_value:
        return env_value
    path = _file()
    try:
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    except OSError:
        pass
    value = secrets.token_urlsafe(24)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(value + "\n")
        where = (gettext("Saved to %(path)s; or run: python manage.py ravix_install_token") % {"path": path})
    except OSError:
        where = gettext("Could not be saved (the folder is not writable); set RAVIX_INSTALL_TOKEN in the environment")
    logger.warning(cli_gettext("Ravix install token (for the /install/ page): %s — %s"), value, where)
    return value


def check_token(candidate: str) -> bool:
    if not required():
        return True
    return hmac.compare_digest((candidate or "").strip().encode(), expected_token().encode())


def discard_token() -> None:
    try:
        _file().unlink()
    except OSError:
        pass
