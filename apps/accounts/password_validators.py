"""Password rules: the length comes from the account settings, and the lists are local (nothing is sent anywhere).

* Django's list of the 20,000 most used passwords, plus ``data/common_passwords.txt`` (Persian words typed in Latin or Persian letters,
  keyboard walks, ravix and the usual admin passwords), compared after Persian/Arabic digits and letters are folded to one form.
* A password that is only a common word with digits or symbols around it ("salam1234", "Ravix@2026") is refused too.
* The site name, the domain and "ravix" cannot be the core of a password either.
Every rule can be switched off in the account settings except the length floor of 8.
"""
import re
from functools import lru_cache
from pathlib import Path

from django.contrib.auth.password_validation import CommonPasswordValidator
from django.core.exceptions import ValidationError
from django.utils.translation import gettext, ngettext

DATA = Path(__file__).resolve().parent / "data" / "common_passwords.txt"
MAX_LENGTH = 128
SLACK = 10  # up to this many extra characters (digits, years, symbols) around a listed word do not make it a different password
DEFAULTS = {"password_min_length": 10, "password_check_common": True, "password_check_personal": True}

_DIGITS = {ord(c): str(i) for i, c in enumerate("\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9")} | {ord(c): str(i) for i, c in enumerate("\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669")}
_LETTERS = {ord("\u064a"): "\u06cc", ord("\u0649"): "\u06cc", ord("\u0643"): "\u06a9", ord("\u0629"): "\u0647", ord("\u06c0"): "\u0647", ord("\u0623"): "\u0627", ord("\u0625"): "\u0627", ord("\u0671"): "\u0627"}
_STRIP = re.compile(r"[\u064b-\u065f\u0670\u200c\u200d\u200e\u200f\s]")  # harakat, ZWNJ/ZWJ, direction marks, spaces
_EDGE = re.compile(r"^[\W\d_]+|[\W\d_]+$")  # digits and symbols around the core word


def fold(text: str) -> str:
    """One comparable form: lower case, ASCII digits, a single Persian yeh/kaf, no harakat, ZWNJ or spaces."""
    return _STRIP.sub("", (text or "").translate(_DIGITS).translate(_LETTERS)).casefold()


@lru_cache(maxsize=1)
def local_list() -> frozenset:
    try:
        lines = DATA.read_text(encoding="utf-8").splitlines()
    except OSError:
        return frozenset()
    return frozenset(fold(line) for line in lines if line.strip() and not line.startswith("#"))


def _core(folded: str) -> str:
    return _EDGE.sub("", folded)


def policy_values() -> dict:
    """The settings from the account policy row; defaults when the table is missing (before the first migration) or empty."""
    try:
        from .models import AccountPolicy

        row = AccountPolicy.objects.filter(pk=1).values(*DEFAULTS).first()
    except Exception:  # noqa: BLE001 - installer, first boot, a broken database must not make every password fail
        row = None
    return {**DEFAULTS, **(row or {})}


def site_words() -> set:
    """Words that make a weak password on this site: the site name, its domain and the product name."""
    words = {"ravix", "ravin", "\u0631\u0627\u0648\u06cc\u06a9\u0633", "\u0631\u0627\u0648\u06cc\u0646"}
    try:
        from apps.core.models import SiteSettings

        name = (SiteSettings.objects.filter(pk=1).values_list("site_name", flat=True).first() or "").strip()
    except Exception:  # noqa: BLE001
        name = ""
    for part in re.split(r"[\s\-_.|/]+", name):
        part = fold(part)
        if len(part) >= 4:
            words.add(part)
    folded = fold(name)
    if len(folded) >= 4:
        words.add(folded)
    return {fold(w) for w in words}


class LengthPolicyValidator:
    """Minimum length from the account settings (never below 8) and a ceiling that keeps requests sane."""

    def validate(self, password, user=None):
        minimum = max(8, int(policy_values()["password_min_length"]))
        if len(password) < minimum:
            raise ValidationError(ngettext("This password is too short. It must contain at least %(min_length)d character.",
                                           "This password is too short. It must contain at least %(min_length)d characters.", minimum),
                                  code="password_too_short", params={"min_length": minimum})
        if len(password) > MAX_LENGTH:
            raise ValidationError(gettext("This password is too long (at most %(max)d characters).") % {"max": MAX_LENGTH}, code="password_too_long")

    def get_help_text(self):
        minimum = max(8, int(policy_values()["password_min_length"]))
        return ngettext("Your password must contain at least %(min_length)d character.", "Your password must contain at least %(min_length)d characters.", minimum) % {"min_length": minimum}


class CommonPasswordPolicyValidator:
    """Refuses well-known passwords (Django's list, the local list) and a common word dressed in digits or symbols."""

    def __init__(self):
        self.django = CommonPasswordValidator()

    def validate(self, password, user=None):
        if not policy_values()["password_check_common"]:
            return
        folded = fold(password)
        bad = local_list()
        core = _core(folded)
        hit = folded in bad or (core and core in bad and len(folded) <= len(core) + SLACK)
        if not hit:
            try:
                self.django.validate(password, user)
            except ValidationError:
                hit = True
        if hit:
            raise ValidationError(gettext("This password is too common. Choose something only you would think of."), code="password_too_common")

    def get_help_text(self):
        return gettext("Your password cannot be a commonly used password.")


class SiteContextValidator:
    """Refuses a password built on the site name, the domain, the product name, or the person's own name/username/email."""

    def validate(self, password, user=None):
        if not policy_values()["password_check_personal"]:
            return
        folded = fold(password)
        core = _core(folded)
        words = site_words()
        if user is not None:
            for attr in ("username", "first_name", "last_name"):
                value = fold(getattr(user, attr, "") or "")
                if len(value) >= 4:
                    words.add(value)
            email = fold(getattr(user, "email", "") or "")
            if email:
                local = email.split("@")[0]
                if len(local) >= 4:
                    words.add(local)
        if any(w and (core == w or (w in folded and len(folded) <= len(w) + SLACK)) for w in words):
            raise ValidationError(gettext("This password is based on the site name or your own details. Choose something only you would think of."), code="password_personal")

    def get_help_text(self):
        return gettext("Your password cannot be built on the site name or your own details.")
