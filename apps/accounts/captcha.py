"""Self-hosted proof-of-work check for the public account forms.

The server hands out a signed challenge (random nonce, difficulty, purpose, issue time). The visitor's browser looks for a number ``s`` so that
``sha256("<challenge>:<s>")`` starts with at least ``bits`` zero bits, and sends it back with the form. Checking costs the server one hash;
finding it costs the client about ``2**bits`` hashes, which makes bulk sign-ups and password guessing expensive without a third party,
cookies or any tracking. Every challenge works once (the nonce is remembered until it expires) and only for the form it was issued for.
"""
import hashlib
import secrets

from django.conf import settings
from django.core import signing

from . import throttle

SALT = "ravix.accounts.proof-of-work"
MAX_BITS = 26  # nobody is asked for more than this, whatever the settings say


def enabled() -> bool:
    return bool(settings.RAVIX_CAPTCHA_ENABLED)


def new_challenge(purpose: str, bits: int | None = None) -> dict:
    bits = min(MAX_BITS, max(1, bits or settings.RAVIX_CAPTCHA_BITS))
    token = signing.dumps({"n": secrets.token_hex(16), "b": bits, "p": purpose}, salt=SALT, compress=False)
    return {"token": token, "bits": bits}


def leading_zero_bits(digest: bytes) -> int:
    return len(digest) * 8 - int.from_bytes(digest, "big").bit_length()


def verify(purpose: str, token: str, solution: str, min_bits: int = 1) -> str:
    """``"ok"`` or the reason it failed: ``missing`` | ``expired`` | ``invalid`` | ``weak`` | ``replay``. A good answer is spent: it never works twice."""
    token, solution = (token or "").strip(), (solution or "").strip()
    if not token or not solution:
        return "missing"
    try:
        data = signing.loads(token, salt=SALT, max_age=settings.RAVIX_CAPTCHA_MAX_AGE)
        nonce, bits, issued_for = str(data["n"]), int(data["b"]), data["p"]
    except signing.SignatureExpired:
        return "expired"
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return "invalid"
    if issued_for != purpose:
        return "invalid"
    if bits < min_bits:
        return "weak"  # an easy challenge from before the failures started cannot be reused once the bar was raised
    if not solution.isascii() or not solution.isdigit() or len(solution) > 15:
        return "invalid"
    if leading_zero_bits(hashlib.sha256(f"{token}:{solution}".encode()).digest()) < bits:
        return "invalid"
    ttl = settings.RAVIX_CAPTCHA_MAX_AGE + 60
    fresh = throttle._with_cache(lambda c: c.add(f"pow-used:{nonce}", 1, ttl))
    return "ok" if fresh else "replay"


def solve(token: str, bits: int) -> str:
    """The browser's job, in Python (tests and the server-side e2e use it)."""
    n = 0
    while True:
        if leading_zero_bits(hashlib.sha256(f"{token}:{n}".encode()).digest()) >= bits:
            return str(n)
        n += 1
