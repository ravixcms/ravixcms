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
