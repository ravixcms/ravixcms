"""پاک‌سازی متن گزارش‌ها: مقدارهای سرّی (SECRET_KEY، رمز دیتابیس، کلیدهای رمزنگاری/امضا، متغیرهای محیطی حساس) و کلید خصوصی PEM حذف می‌شوند."""
from django.utils.translation import gettext
import os
import re

from django.conf import settings

PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S)
SENSITIVE_ENV = re.compile(r"(KEY|SECRET|PASSWORD|PASSWD|TOKEN|CREDENTIAL|DATABASE_URL)", re.I)
URL_PASSWORD = re.compile(r"(\w+://[^:/\s@]+:)[^@\s]+(@)")


def _secret_values() -> list[str]:
    vals = {str(getattr(settings, "SECRET_KEY", "") or "")}
    vals.update(str(k) for k in getattr(settings, "RAVIX_SECRETS_KEYS", []) or [])
    vals.add(str(getattr(settings, "RAVIX_REGISTRY_SIGNING_KEY", "") or ""))
    for db in getattr(settings, "DATABASES", {}).values():
        vals.add(str(db.get("PASSWORD") or ""))
    for name, value in os.environ.items():
        if SENSITIVE_ENV.search(name):
            vals.add(value)
    return sorted((v for v in vals if len(v) >= 6), key=len, reverse=True)


def sanitize(text, limit: int = 4000) -> str:
    text = str(text or "")
    text = PEM.sub(gettext("[private key removed]"), text)
    text = URL_PASSWORD.sub(r"\1***\2", text)
    for secret in _secret_values():
        text = text.replace(secret, "***")
    if len(text) > limit:
        text = (gettext("%(text)s\n… (truncated)") % {"text": text[:limit]})
    return text


_FINAL_ERROR = re.compile(r"^\s*(?:[A-Za-z_][\w.]*\.)?[A-Za-z_]\w*(?:Error|Exception|Exit|Interrupt|Warning|Failed)\b.*$|^\s*[A-Za-z_][\w.]*: \S.*$")


def final_error_line(text) -> str:
    """The last line shaped like ``SomeError: …`` (the end of a traceback); else the last non-empty line."""
    lines = [ln for ln in str(text or "").splitlines() if ln.strip()]
    for ln in reversed(lines):
        if _FINAL_ERROR.match(ln) and not ln.lstrip().startswith(("File ", "Traceback")):
            return ln.strip()
    return lines[-1].strip() if lines else ""


def compact(text, limit: int = 1500) -> str:
    """Mask secrets, put the text on one line, then cap the length KEEPING THE END.

    The reason for a failure is its final exception line, so when space is short the middle of the traceback goes, never the end.
    """
    text = sanitize(text, 10**9)
    final = " ".join(final_error_line(text).split())
    body = " ".join(text.split())
    if len(body) <= limit:
        return body
    final = final if len(final) <= limit // 2 else final[: limit // 2 - 1] + "…"
    before = body[: body.rfind(final)].rstrip() if final in body else body
    marker = " … "
    room = max(limit - len(final) - 2 * len(marker), 0)
    head_n = room * 2 // 5
    tail_n = room - head_n
    return body[:head_n] + marker + (before[-tail_n:] if tail_n else "") + marker.rstrip() + " " + final
