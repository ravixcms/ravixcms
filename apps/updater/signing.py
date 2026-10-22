"""
امضای بستهٔ به‌روزرسانی (Ed25519) با همان کلیدهای مورد اعتماد افزونه‌ها (RAVIN_TRUSTED_PUBLIC_KEYS + کلیدهای فعال ثبت‌شده توسط مدیرکل).

پیام امضاشده شامل «نوع جزء، شناسه، نسخه و SHA-256 فایل» است و پیشوندش با پیام افزونه‌ها فرق دارد؛ پس امضای یک بستهٔ افزونه
هرگز برای هسته/قالب معتبر نیست و فهرست (index) نمی‌تواند بستهٔ دیگری را جای بسته‌ای امضاشده بنشاند.
**امضا اصالت و تمامیت را می‌رساند، نه بی‌خطر بودن کد.** کلید خصوصی هرگز اینجا ذخیره نمی‌شود.
"""
import base64
import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from apps.plugins import signing as plugin_signing
from apps.plugins.signing import SigningError, Verification, key_id, trusted_keys  # noqa: F401

PREFIX = "ravin-update-v1"
INDEX_PREFIX = "ravin-index-v1"


def message(kind: str, slug: str, version: str, sha256: str) -> bytes:
    return "\n".join([PREFIX, kind, slug or "-", version, sha256]).encode()


def index_message(sha256: str) -> bytes:
    return "\n".join([INDEX_PREFIX, sha256]).encode()


def sign(seed_b64: str, kind: str, slug: str, version: str, sha256: str) -> tuple[str, str]:
    key = plugin_signing.load_private_key(seed_b64)
    pub = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    return base64.b64encode(key.sign(message(kind, slug, version, sha256))).decode(), key_id(pub)


def sign_index(seed_b64: str, data: bytes) -> tuple[str, str]:
    key = plugin_signing.load_private_key(seed_b64)
    pub = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    return base64.b64encode(key.sign(index_message(hashlib.sha256(data).hexdigest()))).decode(), key_id(pub)


def _verify(msg: bytes, signature_b64: str, claimed_key_id: str = "") -> Verification:
    if not signature_b64:
        return Verification("unsigned", reason="بسته امضا ندارد.")
    keys = trusted_keys()
    if not keys:
        return Verification("invalid", reason="هیچ کلید مورد اعتمادی تنظیم نشده است.")
    try:
        sig = plugin_signing._b64decode(signature_b64, 64)
    except SigningError:
        return Verification("invalid", reason="قالب امضا نامعتبر است.")
    if claimed_key_id and claimed_key_id not in keys:
        return Verification("invalid", key_id=claimed_key_id, reason="کلید امضاکننده در فهرست کلیدهای مورد اعتماد نیست (یا غیرفعال شده).")
    candidates = [keys[claimed_key_id]] if claimed_key_id else list(keys.values())
    for k in candidates:
        try:
            Ed25519PublicKey.from_public_bytes(plugin_signing._b64decode(k.public_key, 32)).verify(sig, msg)
            return Verification("verified", key_id=k.key_id, label=k.label)
        except InvalidSignature:
            continue
    return Verification("invalid", key_id=claimed_key_id, reason="امضا با محتوای بسته مطابقت ندارد (بسته تغییر کرده یا امضا از کلید دیگری است).")


def verify(kind: str, slug: str, version: str, sha256: str, signature_b64: str, claimed_key_id: str = "") -> Verification:
    return _verify(message(kind, slug, version, sha256), signature_b64, claimed_key_id)


def verify_index(data: bytes, signature_b64: str, claimed_key_id: str = "") -> Verification:
    return _verify(index_message(hashlib.sha256(data).hexdigest()), signature_b64, claimed_key_id)
