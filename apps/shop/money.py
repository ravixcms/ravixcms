"""Money for Ravix Shop: which currencies exist, how an amount is stored, how it is shown and typed in each language.

Storage (never changes): an integer in the **smallest unit of the order's currency**.

====  ===========================  ==============================
code  unit stored                  note
====  ===========================  ==============================
IRR   rial (whole rials)           the recommended base unit for Iran; the shop may *display* it as toman (÷ 10)
IRT   toman (whole tomans)         what shops used before Ravix Shop had a setting; kept as it is
USD   cent                         1999 = $19.99
EUR   euro cent                    1999 = €19.99
====  ===========================  ==============================

Showing toman never changes what is stored or sent to a gateway: a rial shop shown in toman still charges rials (gateways get the
currency of the order: ``IRR`` stays ``IRR``). ``tests/test_money.py`` pins the ×10 relation so that a mix-up is caught.
"""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.utils import formats, translation
from django.utils.translation import gettext, gettext_lazy

from apps.core import localefmt

NBSP = " "


@dataclass(frozen=True)
class Currency:
    code: str
    exponent: int  # decimal places between the stored unit and the unit people read (cent → dollar: 2)
    name: object  # lazy, translated


CURRENCIES = {
    "IRR": Currency("IRR", 0, gettext_lazy("Rial")),
    "IRT": Currency("IRT", 0, gettext_lazy("Toman")),
    "USD": Currency("USD", 2, gettext_lazy("US dollar")),
    "EUR": Currency("EUR", 2, gettext_lazy("Euro")),
}
CHOICES = [(c.code, c.name) for c in CURRENCIES.values()]
TOMAN_PER_RIAL = Decimal("0.1")

# Symbol currencies: where the symbol goes in each language (CLDR-like). {n} is the formatted number.
SYMBOL_PATTERNS = {
    "en": {"USD": "${n}", "EUR": "€{n}"},
    "de": {"USD": "{n}" + NBSP + "$", "EUR": "{n}" + NBSP + "€"},
    "fr": {"USD": "{n}" + NBSP + "$US", "EUR": "{n}" + NBSP + "€"},
    "es": {"USD": "{n}" + NBSP + "US$", "EUR": "{n}" + NBSP + "€"},
    "pt-br": {"USD": "US$" + NBSP + "{n}", "EUR": "€" + NBSP + "{n}"},
    "ru": {"USD": "{n}" + NBSP + "$", "EUR": "{n}" + NBSP + "€"},
    "tr": {"USD": "${n}", "EUR": "€{n}"},
    "zh-hans": {"USD": "US${n}", "EUR": "€{n}"},
    "ar": {"USD": "{n}" + NBSP + "US$", "EUR": "{n}" + NBSP + "€"},
}


def exponent(code: str) -> int:
    return CURRENCIES[code].exponent


def is_valid(code: str) -> bool:
    return code in CURRENCIES


def to_major(amount: int, code: str) -> Decimal:
    """Stored integer → the amount people read (cent → dollar). Rial and toman have no fraction."""
    return Decimal(int(amount)).scaleb(-exponent(code))


def display(amount: int, code: str, show_toman: bool = False) -> tuple[Decimal, str]:
    """``(number, currency to name)`` as the customer reads it. Only a rial shop can show toman (1 toman = 10 rials)."""
    if code == "IRR" and show_toman:
        return Decimal(int(amount)) * TOMAN_PER_RIAL, "IRT"
    return to_major(amount, code), code


def _number(value: Decimal, places: int) -> str:
    """Grouped number in the active language (Persian digits and separators for fa)."""
    value = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    whole, _, frac = f"{value:f}".partition(".")
    if localefmt.is_persian():
        grouped = localefmt.grouped_int(int(whole))
        return localefmt.digits(grouped + (chr(0x066B) + frac if places else ""))
    separator = formats.get_format("THOUSAND_SEPARATOR", use_l10n=True) or ","
    decimal = formats.get_format("DECIMAL_SEPARATOR", use_l10n=True) or "."
    grouped = f"{int(whole):,}".replace(",", separator)
    return grouped + (decimal + frac if places else "")


def format_amount(amount: int, code: str, *, show_toman: bool = False) -> str:
    """The number only (no currency name), in the active language."""
    value, shown = display(amount, code, show_toman)
    places = exponent(shown) if shown != "IRT" or code != "IRR" else (0 if int(amount) % 10 == 0 else 1)
    return _number(value, places)


def format_money(amount: int, code: str, *, show_toman: bool = False, language: str | None = None) -> str:
    """``5,000 Toman`` / ``$19.99`` / ``19,99 €`` / ``۵٬۰۰۰ تومان`` — correct for the language (default: the active one)."""
    if language:
        with translation.override(language):
            return format_money(amount, code, show_toman=show_toman)
    value, shown = display(amount, code, show_toman)
    number = format_amount(amount, code, show_toman=show_toman)
    pattern = SYMBOL_PATTERNS.get((translation.get_language() or "en").lower().split("_")[0].replace("_", "-"), {}).get(shown)
    if pattern is None and shown in ("USD", "EUR"):
        lang = (translation.get_language() or "en").lower()
        pattern = SYMBOL_PATTERNS.get(lang, SYMBOL_PATTERNS.get(lang.split("-")[0], SYMBOL_PATTERNS["en"]))[shown]
    if pattern is None:
        return f"{number} {CURRENCIES[shown].name}"
    return pattern.replace("{n}", number)


def jsonld_offer(amount: int, code: str) -> tuple[str, str]:
    """``(price, ISO 4217 code)`` for schema.org / feeds. ``IRT`` is not an ISO code, so a toman shop is published in rials (×10)."""
    if code == "IRT":
        return str(int(amount) * 10), "IRR"
    return f"{to_major(amount, code):f}", code


def parse_amount(value, code: str, *, show_toman: bool = False) -> int:
    """What an operator typed (in the unit the form shows) → the stored integer. ``ValueError`` for nonsense or a fraction of the unit."""
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(gettext("Enter a valid amount.")) from None
    if not number.is_finite():
        raise ValueError(gettext("Enter a valid amount."))
    if code == "IRR" and show_toman:
        number = number * 10
    stored = number.scaleb(exponent(code))
    if stored != stored.to_integral_value():
        raise ValueError(gettext("This currency does not allow that many decimal places."))
    return int(stored)


def input_value(amount: int, code: str, *, show_toman: bool = False) -> Decimal:
    """Stored integer → the number an operator edits in a form (no grouping)."""
    value, shown = display(amount, code, show_toman)
    return value.quantize(Decimal(1).scaleb(-exponent(shown))) if shown != "IRT" or code != "IRR" else (value.quantize(Decimal(1)) if int(amount) % 10 == 0 else value)
