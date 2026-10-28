"""Shared URL safety check used by the page builder, Ravix Studio and plugins."""
import re
from urllib.parse import urlsplit

_URL_CHARS_RE = re.compile(r"^[^\s\x00-\x1f\x7f\\<>\"'`]+$")


def is_safe_url(value: str) -> bool:
    """فقط مسیر داخلی، http/https، mailto، tel و لنگر (#). javascript:/data: و نشانی‌های نسبت‌به‌پروتکل (//) رد می‌شوند."""
    if not value or len(value) > 500 or not _URL_CHARS_RE.match(value):
        return False
    if value.startswith("//"):
        return False
    if value.startswith(("/", "#")):
        return True
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    if parts.scheme in ("http", "https"):
        return bool(parts.netloc) and "@" not in parts.netloc
    if parts.scheme in ("mailto", "tel"):
        return bool(parts.path)
    return False
