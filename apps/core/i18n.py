"""Small i18n helpers shared by the panel code."""
from django.utils.translation import gettext


def tr(text):
    """Translate a dynamic string (plugin/theme manifest text) through the active catalogs.

    Unknown strings are returned unchanged, so third-party text without a translation is shown as written.
    ``gettext("")`` would return the catalog header, hence the empty-string guard.
    """
    if not text or not isinstance(text, str):
        return text
    return gettext(text)
