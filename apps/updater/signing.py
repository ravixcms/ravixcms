"""
امضای بستهٔ به‌روزرسانی (Ed25519) فقط با کلیدهایی که برای دامنهٔ «core» مورد اعتمادند (کلیدهای رسمی داخل کد، RAVIX_TRUSTED_PUBLIC_KEYS و کلیدهای پنل با دامنهٔ core). کلید افزونه‌ها (دامنهٔ plugin) هسته را امضا نمی‌کند.

پیام امضاشده شامل «نوع جزء، شناسه، نسخه و SHA-256 فایل» است و پیشوندش با پیام افزونه‌ها فرق دارد؛ پس امضای یک بستهٔ افزونه
هرگز برای هسته/قالب معتبر نیست و فهرست (index) نمی‌تواند بستهٔ دیگری را جای بسته‌ای امضاشده بنشاند.
**امضا اصالت و تمامیت را می‌رساند، نه بی‌خطر بودن کد.** کلید خصوصی هرگز اینجا ذخیره نمی‌شود.
"""
from django.utils.translation import gettext
import base64
import hashlib

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
    """فقط کلیدهای دامنهٔ «core»؛ کلید ناشر افزونه‌ها (دامنهٔ plugin) هسته را نمی‌تواند امضا کند."""
    return plugin_signing.verify_message(msg, signature_b64, claimed_key_id, plugin_signing.SCOPE_CORE)


def verify(kind: str, slug: str, version: str, sha256: str, signature_b64: str, claimed_key_id: str = "") -> Verification:
    return _verify(message(kind, slug, version, sha256), signature_b64, claimed_key_id)


def verify_index(data: bytes, signature_b64: str, claimed_key_id: str = "") -> Verification:
    return _verify(index_message(hashlib.sha256(data).hexdigest()), signature_b64, claimed_key_id)
