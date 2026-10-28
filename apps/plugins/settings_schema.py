"""
اسکیمای تنظیمات افزونه‌ها: افزونه در manifest فیلدهایش را اعلام می‌کند؛ هسته فرم را می‌سازد و **ورودی را اعتبارسنجی** می‌کند.
افزونه هرگز مقدار تنظیمات را خام از کاربر نمی‌گیرد.
"""
from django.utils.translation import gettext
import re
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
TYPES = ("text", "textarea", "bool", "int", "choice", "email", "url")


@dataclass(frozen=True)
class SettingField:
    key: str
    label: str
    type: str = "text"
    default: object = ""
    help: str = ""
    choices: tuple = ()  # ((value, label), ...)
    min: int | None = None
    max: int | None = None
    max_len: int = 200
    required: bool = False

    def to_json(self) -> dict:
        data = {"key": self.key, "label": self.label, "type": self.type, "default": self.default}
        if self.help:
            data["help"] = self.help
        if self.type == "choice":
            data["choices"] = [list(c) for c in self.choices]
        if self.type == "int":
            data["min"], data["max"] = self.min, self.max
        if self.type in ("text", "textarea", "email", "url"):
            data["max_len"] = self.max_len
        if self.required:
            data["required"] = True
        return data

    def clean(self, value):
        """مقدار پاک‌سازی‌شده را برمی‌گرداند یا ValidationError می‌دهد."""
        t = self.type
        if t == "bool":
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "on", "yes"}
            return bool(value)
        if t == "int":
            try:
                if isinstance(value, bool) or (isinstance(value, float) and value != int(value)):
                    raise ValueError
                number = int(str(value).strip()) if not isinstance(value, int) else value
            except (TypeError, ValueError):
                raise ValidationError((gettext("“%(label)s” must be an integer.") % {"label": self.label}))
            if self.min is not None and number < self.min or self.max is not None and number > self.max:
                raise ValidationError((gettext("“%(label)s” must be between %(min)s and %(max)s.") % {"label": self.label, "min": self.min, "max": self.max}))
            return number
        if not isinstance(value, str):
            raise ValidationError((gettext("“%(label)s” must be text.") % {"label": self.label}))
        value = value.replace("\r\n", "\n").strip()
        if t != "textarea":
            value = value.replace("\n", " ")
        value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
        if len(value) > self.max_len:
            raise ValidationError((gettext("“%(label)s” can be at most %(max_len)s characters.") % {"label": self.label, "max_len": self.max_len}))
        if not value:
            if self.required:
                raise ValidationError((gettext("“%(label)s” is required.") % {"label": self.label}))
            return ""
        if t == "choice":
            if value not in {c[0] for c in self.choices}:
                raise ValidationError((gettext("The value of “%(label)s” is not allowed.") % {"label": self.label}))
        elif t == "email":
            try:
                validate_email(value)
            except ValidationError:
                raise ValidationError((gettext("“%(label)s” is not a valid email.") % {"label": self.label}))
        elif t == "url":
            from apps.core.safeurl import is_safe_url

            if not is_safe_url(value):
                raise ValidationError((gettext("“%(label)s” must be a permitted address (an internal path or http/https).") % {"label": self.label}))
        return value


def parse_field(raw, index: int, errors: list):
    """یک فیلد manifest را اعتبارسنجی می‌کند؛ خطاها در ``errors`` جمع می‌شوند."""
    where = f"settings[{index}]"
    if not isinstance(raw, dict):
        errors.append((gettext("%(where)s: must be an object.") % {"where": where}))
        return None
    allowed = {"key", "label", "type", "default", "help", "choices", "min", "max", "max_len", "required"}
    for k in raw:
        if k not in allowed:
            errors.append((gettext("%(where)s.%(k)s: unknown key.") % {"where": where, "k": k}))
    key, label, type_ = raw.get("key"), raw.get("label"), raw.get("type", "text")
    if not isinstance(key, str) or not KEY_RE.match(key):
        errors.append((gettext("%(where)s.key: invalid (lowercase English letters, digits and _).") % {"where": where}))
        return None
    if not isinstance(label, str) or not label.strip() or len(label) > 100:
        errors.append((gettext("%(where)s.label: required (at most 100 characters).") % {"where": where}))
        return None
    if type_ not in TYPES:
        errors.append((gettext("%(where)s.type: one of %(TYPES)s.") % {"where": where, "TYPES": ', '.join(TYPES)}))
        return None
    choices = ()
    if type_ == "choice":
        raw_choices = raw.get("choices")
        if not isinstance(raw_choices, list) or not raw_choices or not all(
                isinstance(c, (list, tuple)) and len(c) == 2 and isinstance(c[0], str) and isinstance(c[1], str) and c[1] for c in raw_choices):
            errors.append((gettext("%(where)s.choices: a list of [value, label] is required.") % {"where": where}))
            return None
        choices = tuple((c[0], c[1]) for c in raw_choices)
    field = SettingField(
        key=key, label=label.strip(), type=type_, default=raw.get("default", False if type_ == "bool" else (0 if type_ == "int" else "")),
        help=str(raw.get("help", ""))[:300], choices=choices, min=raw.get("min", 0 if type_ == "int" else None),
        max=raw.get("max", 1_000_000 if type_ == "int" else None), max_len=raw.get("max_len", 200), required=bool(raw.get("required", False)),
    )
    if not isinstance(field.max_len, int) or isinstance(field.max_len, bool) or not 1 <= field.max_len <= 5000:
        errors.append((gettext("%(where)s.max_len: between 1 and 5000.") % {"where": where}))
        return None
    if type_ == "int" and (not isinstance(field.min, int) or not isinstance(field.max, int) or isinstance(field.min, bool) or isinstance(field.max, bool)):
        errors.append((gettext("%(where)s: min/max must be integers.") % {"where": where}))
        return None
    try:  # پیش‌فرض باید خودش معتبر باشد (فیلد الزامی با پیش‌فرض خالی مجاز است؛ کاربر باید پر کند)
        if not (field.default == "" and field.required):
            field.clean(field.default)
    except ValidationError as exc:
        errors.append(f"{where}.default: {exc.messages[0]}")
        return None
    return field


def clean_all(schema, raw: dict):
    """(مقادیر پاک‌سازی‌شده، خطاها) برای دادهٔ خام؛ کلیدهای ناشناخته نادیده گرفته می‌شوند."""
    clean, errors = {}, {}
    for f in schema:
        value = raw.get(f.key, f.default)
        try:
            clean[f.key] = f.clean(value)
        except ValidationError as exc:
            errors[f.key] = exc.messages[0]
    return clean, errors
