"""رمزنگاری تنظیمات محرمانهٔ درگاه (Fernet) و پاک‌سازی داده‌های گزارش. کلید هرگز در دیتابیس یا کد نیست."""
import base64
import json
import re

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings


class SecretsError(Exception):
    pass


def _keys() -> list[bytes]:
    explicit = [k for k in getattr(settings, "RAVIX_SECRETS_KEYS", []) if k]
    if explicit:
        return [k.encode() for k in explicit]
    if settings.DEBUG or getattr(settings, "RAVIX_SECRETS_ALLOW_DERIVED_KEY", False):
        # فقط برای توسعه: از SECRET_KEY مشتق می‌شود (با چرخش SECRET_KEY دادهٔ رمزشده خوانا نمی‌ماند)
        raw = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"ravin-gateway-secrets").derive(settings.SECRET_KEY.encode())
        return [base64.urlsafe_b64encode(raw)]
    raise SecretsError("کلید رمزنگاری اسرار (RAVIX_SECRETS_KEY) تنظیم نشده است.")


def _fernet() -> MultiFernet:
    try:
        return MultiFernet([Fernet(k) for k in _keys()])  # کلید اول رمز می‌کند؛ همه برای رمزگشایی (چرخش کلید)
    except (ValueError, TypeError):
        raise SecretsError("RAVIX_SECRETS_KEY معتبر نیست (باید کلید Fernet باشد).")


def key_configured() -> bool:
    return bool([k for k in getattr(settings, "RAVIX_SECRETS_KEYS", []) if k])


def encrypt_json(data: dict) -> str:
    if not data:
        return ""
    return _fernet().encrypt(json.dumps(data, ensure_ascii=False).encode()).decode()


def decrypt_json(token: str) -> dict:
    if not token:
        return {}
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken:
        raise SecretsError("رمزگشایی تنظیمات ممکن نیست؛ کلید RAVIX_SECRETS_KEY تغییر کرده است.")


# ------------------------------------------------------------------ پاک‌سازی گزارش‌ها
SENSITIVE_KEY = re.compile(r"(key|secret|token|password|passwd|authorization|auth|merchant|signature|sign|card|pan|cvv|pin|otp|cookie|credential)", re.I)
MAX_VALUE = 200
MAX_ITEMS = 30


def redact(obj, secrets=(), _depth=0):
    """نسخهٔ امن برای گزارش: کلیدهای حساس ← ***، مقدارهای سرّی شناخته‌شده از متن حذف، طول و عمق محدود."""
    if _depth > 3:
        return "…"
    if isinstance(obj, dict):
        out = {}
        for i, (k, v) in enumerate(obj.items()):
            if i >= MAX_ITEMS:
                out["…"] = "truncated"
                break
            out[str(k)[:60]] = "***" if SENSITIVE_KEY.search(str(k)) else redact(v, secrets, _depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(v, secrets, _depth + 1) for v in list(obj)[:MAX_ITEMS]]
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    text = scrub(str(obj), secrets)
    return text[:MAX_VALUE]


def scrub(text: str, secrets=()) -> str:
    """مقدارهای سرّی (کلیدهای پیکربندی) را از هر متنی حذف می‌کند، حتی اگر ارائه‌دهنده آن‌ها را echo کرده باشد."""
    for s in secrets:
        if s and len(str(s)) >= 4:
            text = text.replace(str(s), "***")
    return text


def secret_values(config: dict, fields) -> list[str]:
    return [str(config[f.key]) for f in fields if f.secret and config.get(f.key)]
