"""اعتبارسنجی مقادیر سفارشی‌سازی (رنگ، نشانی‌ها). همه خروجی‌ها قبل از ورود به CSS/HTML کاملاً محدود می‌شوند."""
from django.utils.translation import gettext
import re
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_CONTROL_OR_SPACE_RE = re.compile(r"[\x00-\x20\x7f]")


def normalize_color(value: str) -> str:
    value = (value or "").strip()
    if not HEX_COLOR_RE.match(value):
        raise ValidationError(gettext("The color must be in the form #RRGGBB (e.g. #4f46e5)."))
    return value.lower()


def normalize_link(value: str, *, allow_empty: bool = True) -> str:
    """نشانی امن برای پیوند: مسیر نسبی (/...)، http(s)، mailto و tel. بقیه (javascript:, data: و ...) رد می‌شوند."""
    value = (value or "").strip()
    if not value:
        if allow_empty:
            return ""
        raise ValidationError(gettext("Enter a URL."))
    if len(value) > 500:
        raise ValidationError(gettext("The URL is too long."))
    if _CONTROL_OR_SPACE_RE.search(value):
        raise ValidationError(gettext("The URL must not contain spaces or control characters."))
    if value.startswith("//") or value.startswith("/\\") or "\\" in value:
        raise ValidationError(gettext("The URL is invalid."))
    if value.startswith("/") or value.startswith("#"):
        return value
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise ValidationError(gettext("The URL is invalid.")) from exc
    scheme = parts.scheme.lower()
    if scheme in {"http", "https"}:
        if not parts.netloc or not parts.hostname:
            raise ValidationError(gettext("The URL is invalid."))
        return value
    if scheme == "mailto" and parts.path and "@" in parts.path:
        return value
    if scheme == "tel" and re.fullmatch(r"\+?[0-9\-]{3,20}", parts.path or ""):
        return value
    raise ValidationError(gettext("Only http, https, mailto, tel URLs or internal paths (/...) are allowed."))
