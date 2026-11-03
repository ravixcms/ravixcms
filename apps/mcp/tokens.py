"""Opaque tokens. Only a SHA-256 of a token is ever stored, and a token never appears in a log line or an error message."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

PREFIX = {"access": "rvx_at_", "refresh": "rvx_rt_", "code": "rvx_ac_", "client": "rvx_c_"}
_VERIFIER = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")


def new(kind: str) -> str:
    return PREFIX[kind] + secrets.token_urlsafe(32)


def digest(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8", "ignore")).hexdigest()


def s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def pkce_ok(verifier: str, challenge: str) -> bool:
    """RFC 7636 S256, compared in constant time."""
    if not _VERIFIER.match(verifier or ""):
        return False
    return hmac.compare_digest(s256(verifier), challenge or "")


def valid_challenge(challenge: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9\-_]{43}", challenge or ""))  # base64url of a 32-byte SHA-256, no padding
