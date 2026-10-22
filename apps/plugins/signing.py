"""
امضای دیجیتال بستهٔ افزونه (Ed25519).

امضا «اصالت و تمامیت» را تأیید می‌کند: بسته را همان دارندهٔ کلید مورد اعتماد ساخته و پس از آن تغییر نکرده است.
**امضا به معنی بی‌خطر بودن کد نیست**؛ افزونه همچنان کد پایتون است و فقط پس از بازبینی/اعتماد صریح مدیر اجرا می‌شود.

پیام امضاشده (قالب ثابت و نسخه‌دار، برای جلوگیری از جابه‌جایی امضا بین بسته‌ها)::

    ravin-plugin-v1\\n<slug>\\n<version>\\n<sha256 فایل zip>\\n<اثرانگشت محتوا>

کلید خصوصی هرگز در کد/مخزن نیست: از فایل با مجوز محدود یا متغیر محیطی خوانده می‌شود. اینجا فقط کلید عمومی نگهداری می‌شود.
"""
import base64
import binascii
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from django.conf import settings

MESSAGE_PREFIX = "ravin-plugin-v1"


class SigningError(Exception):
    pass


def message(slug: str, version: str, sha256: str, fingerprint: str) -> bytes:
    return "\n".join([MESSAGE_PREFIX, slug, version, sha256, fingerprint]).encode("utf-8")


def key_id(public_b64: str) -> str:
    """شناسهٔ کوتاه کلید = ۱۶ نویسهٔ اول SHA-256 بایت‌های کلید عمومی."""
    return hashlib.sha256(_b64decode(public_b64, 32)).hexdigest()[:16]


def _b64decode(value: str, expected_len: int | None = None) -> bytes:
    try:
        raw = base64.b64decode((value or "").strip(), validate=True)
    except (binascii.Error, ValueError):
        raise SigningError("مقدار base64 نامعتبر است.")
    if expected_len is not None and len(raw) != expected_len:
        raise SigningError(f"طول کلید باید {expected_len} بایت باشد.")
    return raw


def normalize_public_key(value: str) -> str:
    """کلید عمومی base64 (۳۲ بایت) را اعتبارسنجی و استاندارد می‌کند."""
    raw = _b64decode(value, 32)
    try:
        Ed25519PublicKey.from_public_bytes(raw)
    except ValueError:
        raise SigningError("کلید عمومی Ed25519 معتبر نیست.")
    return base64.b64encode(raw).decode()


# ------------------------------------------------------------------ تولید و امضا (سمت ناشر/مخزن)
def generate_keypair() -> tuple[str, str]:
    """(کلید_خصوصی_base64_seed, کلید_عمومی_base64). کلید خصوصی را فقط در فایل محرمانه نگه دارید."""
    priv = Ed25519PrivateKey.generate()
    seed = priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(seed).decode(), base64.b64encode(pub).decode()


def load_private_key(seed_b64: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(_b64decode(seed_b64, 32))


def read_key_file(path) -> str:
    """seed را از فایل می‌خواند و اگر فایل برای گروه/دیگران قابل‌خواندن باشد رد می‌کند."""
    p = Path(path)
    try:
        mode = p.stat().st_mode
        if os.name == "posix" and mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise SigningError(f"مجوز فایل کلید خصوصی ({p}) باید 600 باشد.")
        return p.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SigningError(f"خواندن فایل کلید ناموفق بود: {exc}")


def sign(seed_b64: str, slug: str, version: str, sha256: str, fingerprint: str) -> tuple[str, str]:
    """(امضا_base64, key_id)"""
    priv = load_private_key(seed_b64)
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    sig = priv.sign(message(slug, version, sha256, fingerprint))
    return base64.b64encode(sig).decode(), key_id(base64.b64encode(pub).decode())


def registry_signing_seed() -> str | None:
    """کلید امضای مخزن از فایل (RAVIX_REGISTRY_SIGNING_KEY_FILE) یا متغیر محیطی (RAVIX_REGISTRY_SIGNING_KEY)؛ در غیر این صورت None."""
    path = getattr(settings, "RAVIX_REGISTRY_SIGNING_KEY_FILE", "")
    if path:
        return read_key_file(path)
    return getattr(settings, "RAVIX_REGISTRY_SIGNING_KEY", "") or None


# ------------------------------------------------------------------ اعتبارسنجی (سمت سایت)
@dataclass(frozen=True)
class TrustedKeyInfo:
    key_id: str
    public_key: str
    label: str
    source: str  # env | db


def trusted_keys() -> dict[str, TrustedKeyInfo]:
    """کلیدهای مورد اعتماد: تنظیم سرور (RAVIX_TRUSTED_PUBLIC_KEYS) + کلیدهای فعال ثبت‌شده توسط مدیرکل."""
    out: dict[str, TrustedKeyInfo] = {}
    for item in getattr(settings, "RAVIX_TRUSTED_PUBLIC_KEYS", []):
        label, _, val = item.partition(":")
        if not val:
            label, val = "env", item
        try:
            val = normalize_public_key(val)
        except SigningError:
            continue  # ورودی خراب نادیده گرفته می‌شود؛ preflight آن را گزارش می‌کند
        kid = key_id(val)
        out[kid] = TrustedKeyInfo(kid, val, label.strip() or "env", "env")
    from .models import TrustedKey

    for k in TrustedKey.objects.filter(is_active=True):
        out.setdefault(k.key_id, TrustedKeyInfo(k.key_id, k.public_key, k.label, "db"))
    return out


@dataclass
class Verification:
    state: str  # verified | unsigned | invalid
    key_id: str = ""
    label: str = ""
    reason: str = ""

    @property
    def ok(self):
        return self.state == "verified"


def verify(slug: str, version: str, sha256: str, fingerprint: str, signature_b64: str, claimed_key_id: str = "") -> Verification:
    if not signature_b64:
        return Verification("unsigned", reason="بسته امضا ندارد.")
    keys = trusted_keys()
    if not keys:
        return Verification("invalid", reason="هیچ کلید مورد اعتمادی تنظیم نشده است.")
    try:
        sig = _b64decode(signature_b64, 64)
    except SigningError:
        return Verification("invalid", reason="قالب امضا نامعتبر است.")
    candidates = [keys[claimed_key_id]] if claimed_key_id in keys else ([] if claimed_key_id else list(keys.values()))
    if claimed_key_id and not candidates:
        return Verification("invalid", key_id=claimed_key_id, reason="کلید امضاکننده در فهرست کلیدهای مورد اعتماد نیست (یا غیرفعال شده).")
    msg = message(slug, version, sha256, fingerprint)
    for k in candidates:
        try:
            Ed25519PublicKey.from_public_bytes(_b64decode(k.public_key, 32)).verify(sig, msg)
            return Verification("verified", key_id=k.key_id, label=k.label)
        except InvalidSignature:
            continue
    return Verification("invalid", key_id=claimed_key_id, reason="امضا با محتوای بسته مطابقت ندارد (بسته تغییر کرده یا امضا از کلید دیگری است).")
