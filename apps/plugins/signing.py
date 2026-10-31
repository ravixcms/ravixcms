"""
امضای دیجیتال بستهٔ افزونه (Ed25519).

امضا «اصالت و تمامیت» را تأیید می‌کند: بسته را همان دارندهٔ کلید مورد اعتماد ساخته و پس از آن تغییر نکرده است.
**امضا به معنی بی‌خطر بودن کد نیست**؛ افزونه همچنان کد پایتون است و فقط پس از بازبینی/اعتماد صریح مدیر اجرا می‌شود.

پیام امضاشده (قالب ثابت و نسخه‌دار، برای جلوگیری از جابه‌جایی امضا بین بسته‌ها)::

    ravin-plugin-v1\\n<slug>\\n<version>\\n<sha256 فایل zip>\\n<اثرانگشت محتوا>

کلید خصوصی هرگز در کد/مخزن نیست: از فایل با مجوز محدود یا متغیر محیطی خوانده می‌شود. اینجا فقط کلید عمومی نگهداری می‌شود.
"""
from django.utils.translation import gettext
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
        raise SigningError(gettext("The base64 value is invalid."))
    if expected_len is not None and len(raw) != expected_len:
        raise SigningError((gettext("The key length must be %(expected_len)s bytes.") % {"expected_len": expected_len}))
    return raw


def normalize_public_key(value: str) -> str:
    """کلید عمومی base64 (۳۲ بایت) را اعتبارسنجی و استاندارد می‌کند."""
    raw = _b64decode(value, 32)
    try:
        Ed25519PublicKey.from_public_bytes(raw)
    except ValueError:
        raise SigningError(gettext("The Ed25519 public key is not valid."))
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
            raise SigningError((gettext("The private key file (%(p)s) must have permission 600.") % {"p": p}))
        return p.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SigningError((gettext("Reading the key file failed: %(exc)s") % {"exc": exc}))


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
# دامنهٔ اعتماد هر کلید: «plugin» = بستهٔ افزونه (و مخزن افزونه/قالب)، «core» = به‌روزرسانی هسته/قالب و فهرست آن.
# کلیدی که فقط برای یک دامنه مورد اعتماد است، برای دامنهٔ دیگر هرگز پذیرفته نمی‌شود.
SCOPE_PLUGIN = "plugin"
SCOPE_CORE = "core"
SCOPES = (SCOPE_PLUGIN, SCOPE_CORE)
ALL_SCOPES = frozenset(SCOPES)


def parse_scopes(value, default=ALL_SCOPES) -> frozenset:
    """«plugin+core» / «plugin,core» / فهرست را به مجموعهٔ دامنه‌ها تبدیل می‌کند؛ نام ناشناخته SigningError است؛ مقدار خالی = default."""
    if isinstance(value, str):
        value = [v for v in value.replace("+", ",").split(",")]
    items = {str(v).strip().lower() for v in (value or []) if str(v).strip()}
    if not items:
        return frozenset(default)
    bad = items - ALL_SCOPES
    if bad:
        raise SigningError((gettext("Unknown trust scope: %(scope)s (use plugin or core).") % {"scope": ", ".join(sorted(bad))}))
    return frozenset(items)


def parse_key_entry(item: str, default_label: str = "") -> tuple[str, str, frozenset, bool]:
    """یک ورودی RAVIX_TRUSTED_PUBLIC_KEYS را می‌خواند. قالب‌ها:
    ``کلید`` ، ``برچسب:کلید`` (قدیمی؛ هر دو دامنه) و ``برچسب:دامنه‌ها:کلید`` (دامنه‌ها با «+»، مثلاً ``publisher:plugin:KEY``).
    خروجی: (برچسب، کلید_نرمال‌شده، دامنه‌ها، صریح_بودن_دامنه)."""
    parts = [p.strip() for p in (item or "").split(":")]
    if len(parts) == 1:
        label, scopes, raw, explicit = default_label, ALL_SCOPES, parts[0], False
    elif len(parts) == 2:
        label, scopes, raw, explicit = parts[0], ALL_SCOPES, parts[1], False
    elif len(parts) == 3:
        label, raw, explicit = parts[0], parts[2], True
        scopes = parse_scopes(parts[1], default=())
        if not scopes:
            raise SigningError(gettext("The trust scope is empty (use plugin, core or plugin+core)."))
    else:
        raise SigningError(gettext("The trusted key entry format is invalid."))
    return (label or default_label), normalize_public_key(raw), frozenset(scopes), explicit


@dataclass(frozen=True)
class TrustedKeyInfo:
    key_id: str
    public_key: str
    label: str
    source: str  # builtin | env | db
    scopes: frozenset = ALL_SCOPES

    @property
    def scopes_text(self) -> str:
        return "+".join(s for s in SCOPES if s in self.scopes)


def trusted_keys(scope: str | None = None) -> dict[str, TrustedKeyInfo]:
    """کلیدهای مورد اعتماد: کلیدهای رسمی داخل کد (apps/core/official.py) + RAVIX_TRUSTED_PUBLIC_KEYS + کلیدهای فعال ثبت‌شده توسط مدیرکل؛ منهای کلیدهای ابطال‌شده.
    با scope=«plugin»/«core» فقط کلیدهایی که برای آن دامنه مورد اعتمادند؛ بدون scope همهٔ کلیدها (برای نمایش در پنل)."""
    from apps.core import official

    out: dict[str, TrustedKeyInfo] = {}
    for source, items in (("builtin", official.OFFICIAL_PUBLIC_KEYS), ("env", getattr(settings, "RAVIX_TRUSTED_PUBLIC_KEYS", []))):
        for item in items:
            try:
                label, val, scopes, _ = parse_key_entry(item, source)
            except SigningError:
                continue  # ورودی خراب نادیده گرفته می‌شود؛ preflight آن را گزارش می‌کند
            kid = key_id(val)
            out.setdefault(kid, TrustedKeyInfo(kid, val, label or source, source, scopes))
    from .models import TrustedKey

    for k in TrustedKey.objects.filter(is_active=True):
        out.setdefault(k.key_id, TrustedKeyInfo(k.key_id, k.public_key, k.label, "db", k.scope_set))
    for kid in revoked_key_ids():
        out.pop(kid, None)  # ابطال بر همهٔ منبع‌ها (کد، env، پنل) غالب است
    if scope is not None:
        out = {kid: k for kid, k in out.items() if scope in k.scopes}
    return out


def revoked_key_ids() -> set[str]:
    from apps.core import official

    return {str(k).strip().lower() for k in (*official.REVOKED_KEY_IDS, *getattr(settings, "RAVIX_REVOKED_KEY_IDS", [])) if str(k).strip()}


@dataclass
class Verification:
    state: str  # verified | unsigned | invalid
    key_id: str = ""
    label: str = ""
    reason: str = ""

    @property
    def ok(self):
        return self.state == "verified"


def verify_message(msg: bytes, signature_b64: str, claimed_key_id: str = "", scope: str = SCOPE_PLUGIN) -> Verification:
    """امضای Ed25519 روی msg را فقط با کلیدهای مورد اعتماد برای همان دامنه (scope) می‌سنجد."""
    if not signature_b64:
        return Verification("unsigned", reason=gettext("The package has no signature."))
    every = trusted_keys()
    if not every:
        return Verification("invalid", reason=gettext("No trusted key is configured."))
    keys = {kid: k for kid, k in every.items() if scope in k.scopes}
    try:
        sig = _b64decode(signature_b64, 64)
    except SigningError:
        return Verification("invalid", reason=gettext("The signature format is invalid."))
    if not keys or (claimed_key_id and claimed_key_id in every and claimed_key_id not in keys):
        return Verification("invalid", key_id=claimed_key_id, reason=gettext("This key is not trusted for this kind of package (plugin packages and core updates use separate trust scopes)."))
    candidates = [keys[claimed_key_id]] if claimed_key_id in keys else ([] if claimed_key_id else list(keys.values()))
    if claimed_key_id and not candidates:
        return Verification("invalid", key_id=claimed_key_id, reason=gettext("The signing key is not in the list of trusted keys (or it has been disabled)."))
    for k in candidates:
        try:
            Ed25519PublicKey.from_public_bytes(_b64decode(k.public_key, 32)).verify(sig, msg)
            return Verification("verified", key_id=k.key_id, label=k.label)
        except InvalidSignature:
            continue
    return Verification("invalid", key_id=claimed_key_id, reason=gettext("The signature does not match the package contents (the package was modified or signed with a different key)."))


def verify(slug: str, version: str, sha256: str, fingerprint: str, signature_b64: str, claimed_key_id: str = "") -> Verification:
    """بستهٔ افزونه؛ فقط کلیدهای دامنهٔ «plugin»."""
    return verify_message(message(slug, version, sha256, fingerprint), signature_b64, claimed_key_id, SCOPE_PLUGIN)
