"""Tiny standalone HTML page for last-resort error responses (no templates, no database)."""
from html import escape

from django.utils.translation import get_language, get_language_bidi, gettext


def minimal_page(title: str, message: str) -> str:
    lang = escape(get_language() or "en", quote=True)
    direction = "rtl" if get_language_bidi() else "ltr"
    return (
        f'<!doctype html><html lang="{lang}" dir="{direction}"><meta charset="utf-8">'
        f"<title>{escape(title)}</title><p>{escape(message)}</p></html>"
    )


def unavailable_page() -> str:
    return minimal_page(gettext("Site unavailable"), gettext("The site theme is unavailable. Please try again later."))


def section_error_page() -> str:
    return minimal_page(gettext("Error"), gettext("This section is temporarily unavailable."))
