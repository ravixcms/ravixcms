"""Language of the operator-facing text: management commands, start-up/settings errors, preflight and log lines.

These messages are written in English (the msgid) and marked with :func:`cli_gettext`. They are shown in another language only
when the operator asks for it: ``RAVIX_CLI_LANG`` (environment variable / ``.env``) wins, otherwise Django's ``LANGUAGE_CODE``.
``fa`` ships with Ravix; for any other language drop a compiled ``locale/<lang>/LC_MESSAGES/cli.mo`` next to it (see
docs/translation.md). A language without a catalog simply shows the English text.

This is deliberately independent of Django's per-request language: a log line must not change language with the visitor, and the
start-up errors are raised before Django's translation machinery exists. It uses Python's own :mod:`gettext` with the ``cli`` domain.
"""
from __future__ import annotations

import gettext as _gettext
import os
from functools import lru_cache
from pathlib import Path

DOMAIN = "cli"
LOCALE_DIR = Path(__file__).resolve().parents[2] / "locale"


def _read_env(name: str) -> str:
    """The variable from the environment, or (before settings exist) from the project's .env file."""
    value = os.environ.get(name, "")
    if value:
        return value.strip()
    env_file = LOCALE_DIR.parent / ".env"
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, sep, rest = line.partition("=")
            if sep and key.strip() == name:
                return rest.strip().strip("\"'")
    except OSError:
        pass
    return ""


def cli_language() -> str:
    """``RAVIX_CLI_LANG``, else ``LANGUAGE_CODE`` (the Django settings; before they exist the environment / .env), else English.
    Returned like "pt_BR"."""
    code = ""
    try:
        from django.conf import settings

        if settings.configured:  # never triggers the lazy import (we may be running inside the settings module itself)
            code = getattr(settings, "RAVIX_CLI_LANG", "") or settings.LANGUAGE_CODE
    except Exception:  # noqa: BLE001
        code = ""
    if not code:  # settings do not exist yet (start-up errors)
        code = _read_env("RAVIX_CLI_LANG") or _read_env("LANGUAGE_CODE")
    return (code or "en").strip().replace("-", "_")


@lru_cache(maxsize=16)
def _catalog(language: str):
    candidates = [language]
    if "_" in language:
        candidates.append(language.split("_")[0])
    for code in candidates:
        for variant in (code, code.lower()):
            path = LOCALE_DIR / variant / "LC_MESSAGES" / f"{DOMAIN}.mo"
            if path.is_file():
                with open(path, "rb") as handle:
                    return _gettext.GNUTranslations(handle)
    return _gettext.NullTranslations()


def cli_gettext(message: str) -> str:
    """``message`` (English) in the operator's language; unchanged when there is no translation."""
    return _catalog(cli_language()).gettext(message)


def reset_cache() -> None:
    """Forget the loaded catalogs (tests change the language)."""
    _catalog.cache_clear()
