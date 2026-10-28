"""Small symmetric box for secrets kept in the database (the SMTP password). Fernet keys come from RAVIX_SECRETS_KEY (first one encrypts, the rest
only decrypt, for rotation); without one a key is derived from SECRET_KEY (still not readable from a database dump alone)."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings


def _fernets() -> MultiFernet:
    keys = [k.strip() for k in getattr(settings, "RAVIX_SECRETS_KEYS", []) or [] if k and k.strip()]
    ferns = []
    for key in keys:
        try:
            ferns.append(Fernet(key.encode()))
        except (ValueError, TypeError):
            continue
    if not ferns:
        derived = base64.urlsafe_b64encode(hashlib.sha256(b"ravix-secretbox:" + settings.SECRET_KEY.encode()).digest())
        ferns.append(Fernet(derived))
    return MultiFernet(ferns)


def has_dedicated_key() -> bool:
    return any(k.strip() for k in getattr(settings, "RAVIX_SECRETS_KEYS", []) or [])


def encrypt(value: str) -> str:
    return _fernets().encrypt(value.encode("utf-8")).decode("ascii") if value else ""


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernets().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""
