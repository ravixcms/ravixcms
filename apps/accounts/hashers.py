"""Argon2id password hasher with Ravix defaults.

Django's own Argon2 hasher asks for 100 MiB per hash, which is too much for a shared-hosting process limit when several people sign in at once.
These defaults (64 MiB, 3 passes, 2 lanes) follow the OWASP Password Storage Cheat Sheet and take roughly 0.1 s on a small server.
Change them with RAVIX_ARGON2_MEMORY_KIB / RAVIX_ARGON2_TIME_COST / RAVIX_ARGON2_PARALLELISM; every stored hash is re-made with the new values
at the owner's next sign-in (``must_update``), exactly like the move from PBKDF2.
"""
import importlib.util
import os

from django.contrib.auth.hashers import Argon2PasswordHasher


def argon2_available() -> bool:
    return importlib.util.find_spec("argon2") is not None


def _int(name: str, default: int, low: int, high: int) -> int:
    try:
        return min(max(int(os.environ.get(name, default)), low), high)
    except (TypeError, ValueError):
        return default


class RavixArgon2PasswordHasher(Argon2PasswordHasher):
    time_cost = _int("RAVIX_ARGON2_TIME_COST", 3, 1, 10)
    memory_cost = _int("RAVIX_ARGON2_MEMORY_KIB", 65536, 8192, 1048576)
    parallelism = _int("RAVIX_ARGON2_PARALLELISM", 2, 1, 16)
