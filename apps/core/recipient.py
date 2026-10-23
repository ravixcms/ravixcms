"""Language of text that is written for somebody who is not the person running the code.

Emails, notifications and stored messages that are sent or written by a timer, a management command, a gateway callback, a hook or
a request made by somebody else must be in the **recipient's** language, never in the language of the terminal (``RAVIX_CLI_LANG``)
or of whoever happened to trigger the code. The order is always:

1. the recipient's profile language (``User.language``) — or, for a guest order, the language stored with the order;
2. an explicit fallback the caller knows is right (e.g. the interface language a visitor is using right now);
3. the site's default language (``SiteSettings.language``).

Only terminal output stays in the CLI language (``apps/core/clilang.py``).
"""
from __future__ import annotations

from contextlib import contextmanager

from django.db import transaction
from django.utils import translation

from .regional import normalize_language


def site_default_language() -> str:
    from .models import SiteSettings
    from .regional import regional_for

    try:
        with transaction.atomic():  # a savepoint: a missing column (mid-migration) must not poison the surrounding transaction
            return regional_for(SiteSettings.load())[0]
    except Exception:  # noqa: BLE001 — never let a missing table break a notification
        return normalize_language(translation.get_language()) or "en"


def user_language(user) -> str:
    """The profile language of ``user`` ("" when none is set)."""
    return normalize_language(getattr(user, "language", "")) if user is not None else ""


def language_for_user(user, fallback: str = "") -> str:
    return user_language(user) or normalize_language(fallback) or site_default_language()


def language_for_email(email: str, fallback: str = "") -> str:
    """Language for a message to ``email``: the profile language of the account that owns the address, else ``fallback``, else the
    site default."""
    from django.contrib.auth import get_user_model

    email = (email or "").strip()
    user = get_user_model().objects.filter(email__iexact=email).order_by("pk").first() if email else None
    return language_for_user(user, fallback)


def language_for_order(order) -> str:
    """Language of an order: stored with it when it was placed (guests included), else the customer's profile, else the site default."""
    return normalize_language(getattr(order, "language", "")) or language_for_user(getattr(order, "user", None))


def current_language() -> str:
    """The interface language active right now (inside a web request: the visitor's), "" outside of one."""
    return normalize_language(translation.get_language())


@contextmanager
def use_language(code: str = ""):
    """Run the block with Django's gettext, date formats and templates in ``code`` (default: the site language)."""
    with translation.override(normalize_language(code) or site_default_language()):
        yield
