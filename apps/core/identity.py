"""Cleaning of the "site identity for search engines" fields (names and sameAs profiles) entered in the site settings."""
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.utils.translation import gettext

MAX_NAMES = 10
MAX_PROFILES = 20
MAX_NAME_LENGTH = 120
MAX_URL_LENGTH = 500


def _lines(text) -> list[str]:
    if isinstance(text, (list, tuple)):
        text = "\n".join(str(t) for t in text)
    return [" ".join(line.split()) for line in str(text or "").splitlines() if line.strip()]


def clean_names(text) -> list[str]:
    """One alternative name per line: blanks and repeats dropped, each at most MAX_NAME_LENGTH characters, at most MAX_NAMES names."""
    out = []
    for line in _lines(text):
        if len(line) > MAX_NAME_LENGTH:
            raise ValidationError(gettext("Each name can have at most %(n)d characters.") % {"n": MAX_NAME_LENGTH})
        if line not in out:
            out.append(line)
    if len(out) > MAX_NAMES:
        raise ValidationError(gettext("Enter at most %(n)d alternative names.") % {"n": MAX_NAMES})
    return out


def is_https_profile(value: str) -> bool:
    """A plain absolute https address with a host: no spaces or control characters, no user name or password, no fragment-only tricks."""
    if not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH:
        return False
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c in "<>\"'\\" for c in value):
        return False
    try:
        parts = urlsplit(value)
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError for a bad port
    except ValueError:
        return False
    return parts.scheme == "https" and bool(host) and "." in host and parts.username is None and parts.password is None


def clean_profiles(text) -> list[str]:
    """One https address per line (sameAs). Anything else, for example http://, javascript: or a bare name, is rejected with a message."""
    out = []
    for line in _lines(text):
        if not is_https_profile(line):
            raise ValidationError(gettext("“%(value)s” is not a full https:// address.") % {"value": line[:80]})
        if line not in out:
            out.append(line)
    if len(out) > MAX_PROFILES:
        raise ValidationError(gettext("Enter at most %(n)d addresses.") % {"n": MAX_PROFILES})
    return out
