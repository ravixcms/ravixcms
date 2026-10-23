"""Language-aware number and date formatting.

The Jalali calendar and Persian digits are used only while the active UI language is Persian (``fa``).
Every other language uses the Gregorian calendar, Latin digits and Django's own locale formats
(month names, date order and separators come from Django's bundled locale data).
"""
import datetime

from django.utils import formats, timezone, translation

_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
PERSIAN_THOUSANDS_SEPARATOR = "٬"


def is_persian() -> bool:
    return (translation.get_language() or "").split("-")[0].lower() == "fa"


def digits(value) -> str:
    """Latin digits → Persian digits, but only while the UI language is Persian."""
    text = str(value)
    return text.translate(_PERSIAN_DIGITS) if is_persian() else text


def localize_datetime(value):
    """Aware datetimes are converted to the active time zone; naive values and plain dates pass through."""
    if isinstance(value, datetime.datetime) and timezone.is_aware(value):
        return timezone.localtime(value)
    return value


def gregorian_date(value, fmt: str | None = None) -> str:
    value = localize_datetime(value)
    if fmt:
        return value.strftime(fmt)
    return formats.date_format(value, "SHORT_DATE_FORMAT")


def gregorian_datetime(value, fmt: str | None = None) -> str:
    value = localize_datetime(value)
    if fmt:
        return value.strftime(fmt)
    if isinstance(value, datetime.datetime):
        return formats.date_format(value, "SHORT_DATETIME_FORMAT")
    return formats.date_format(value, "SHORT_DATE_FORMAT")


def long_date(value) -> str:
    """Human-readable date with the month name, e.g. "23 October 2026" (Gregorian, locale-aware)."""
    return formats.date_format(localize_datetime(value), "DATE_FORMAT")


def grouped_int(value) -> str:
    """Integer with thousands separators: Persian ("۱٬۲۳۴") for fa, the locale's grouping otherwise."""
    number = int(value)
    if is_persian():
        return f"{number:,}".replace(",", PERSIAN_THOUSANDS_SEPARATOR).translate(_PERSIAN_DIGITS)
    separator = formats.get_format("THOUSAND_SEPARATOR", use_l10n=True) or ","  # some locales (ar, zh) define none
    return f"{number:,}".replace(",", separator)


# Open Graph locale (language_TERRITORY) for each supported UI language.
OG_LOCALES = {"fa": "fa_IR", "en": "en_US", "ar": "ar_AR", "tr": "tr_TR", "es": "es_ES", "fr": "fr_FR", "de": "de_DE",
              "ru": "ru_RU", "pt-br": "pt_BR", "zh-hans": "zh_CN"}


def og_locale() -> str:
    return OG_LOCALES.get((translation.get_language() or "").lower(), "en_US")


# ---------------------------------------------------------------------------------------------------------------------
# Fonts per script
# ---------------------------------------------------------------------------------------------------------------------
_LATIN_STACK = "'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
_CJK_STACK = ("'Inter', system-ui, -apple-system, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Noto Sans CJK SC', "
              "'Noto Sans SC', 'Source Han Sans SC', sans-serif")
_CJK_FALLBACK = "'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Noto Sans CJK SC', 'Noto Sans SC', 'Source Han Sans SC'"
ARABIC_SCRIPT_LANGUAGES = ("fa", "ar")


def base_language() -> str:
    return (translation.get_language() or "").split("-")[0].lower()


def uses_arabic_script() -> bool:
    return base_language() in ARABIC_SCRIPT_LANGUAGES


def adapt_font_stack(stack: str) -> str:
    """The font stack the site admin picked is designed for Persian/Arabic text. For Latin, Cyrillic and Chinese
    languages it is swapped for a stack that covers that script (a "system font" choice stays a system stack)."""
    language = base_language()
    if language in ARABIC_SCRIPT_LANGUAGES or not stack:
        return stack
    if stack.lstrip().startswith("system-ui"):
        return f"{stack}, {_CJK_FALLBACK}" if language == "zh" else stack
    return _CJK_STACK if language == "zh" else _LATIN_STACK
