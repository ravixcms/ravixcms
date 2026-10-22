"""
اسکیمای تنظیمات افزونه‌ها: افزونه در manifest فیلدهایش را اعلام می‌کند؛ هسته فرم را می‌سازد و **ورودی را اعتبارسنجی** می‌کند.
افزونه هرگز مقدار تنظیمات را خام از کاربر نمی‌گیرد.
"""
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
                raise ValidationError(f"«{self.label}» باید عدد صحیح باشد.")
            if self.min is not None and number < self.min or self.max is not None and number > self.max:
                raise ValidationError(f"«{self.label}» باید بین {self.min} و {self.max} باشد.")
            return number
        if not isinstance(value, str):
            raise ValidationError(f"«{self.label}» باید متن باشد.")
        value = value.replace("\r\n", "\n").strip()
        if t != "textarea":
            value = value.replace("\n", " ")
        value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
        if len(value) > self.max_len:
            raise ValidationError(f"«{self.label}» حداکثر {self.max_len} نویسه است.")
        if not value:
            if self.required:
                raise ValidationError(f"«{self.label}» الزامی است.")
            return ""
        if t == "choice":
            if value not in {c[0] for c in self.choices}:
                raise ValidationError(f"مقدار «{self.label}» مجاز نیست.")
        elif t == "email":
            try:
                validate_email(value)
            except ValidationError:
                raise ValidationError(f"«{self.label}» ایمیل معتبر نیست.")
        elif t == "url":
            from apps.builder.schema import is_safe_url

            if not is_safe_url(value):
                raise ValidationError(f"«{self.label}» باید نشانی مجاز (مسیر داخلی یا http/https) باشد.")
        return value


def parse_field(raw, index: int, errors: list):
    """یک فیلد manifest را اعتبارسنجی می‌کند؛ خطاها در ``errors`` جمع می‌شوند."""
    where = f"settings[{index}]"
    if not isinstance(raw, dict):
        errors.append(f"{where}: باید شیء باشد.")
        return None
    allowed = {"key", "label", "type", "default", "help", "choices", "min", "max", "max_len", "required"}
    for k in raw:
        if k not in allowed:
            errors.append(f"{where}.{k}: کلید ناشناخته.")
    key, label, type_ = raw.get("key"), raw.get("label"), raw.get("type", "text")
    if not isinstance(key, str) or not KEY_RE.match(key):
        errors.append(f"{where}.key: نامعتبر (حروف کوچک انگلیسی، عدد و _).")
        return None
    if not isinstance(label, str) or not label.strip() or len(label) > 100:
        errors.append(f"{where}.label: الزامی (حداکثر ۱۰۰ نویسه).")
        return None
    if type_ not in TYPES:
        errors.append(f"{where}.type: یکی از {', '.join(TYPES)}.")
        return None
    choices = ()
    if type_ == "choice":
        raw_choices = raw.get("choices")
        if not isinstance(raw_choices, list) or not raw_choices or not all(
                isinstance(c, (list, tuple)) and len(c) == 2 and isinstance(c[0], str) and isinstance(c[1], str) and c[1] for c in raw_choices):
            errors.append(f"{where}.choices: فهرست [مقدار، برچسب] لازم است.")
            return None
        choices = tuple((c[0], c[1]) for c in raw_choices)
    field = SettingField(
        key=key, label=label.strip(), type=type_, default=raw.get("default", False if type_ == "bool" else (0 if type_ == "int" else "")),
        help=str(raw.get("help", ""))[:300], choices=choices, min=raw.get("min", 0 if type_ == "int" else None),
        max=raw.get("max", 1_000_000 if type_ == "int" else None), max_len=raw.get("max_len", 200), required=bool(raw.get("required", False)),
    )
    if not isinstance(field.max_len, int) or isinstance(field.max_len, bool) or not 1 <= field.max_len <= 5000:
        errors.append(f"{where}.max_len: بین ۱ تا ۵۰۰۰.")
        return None
    if type_ == "int" and (not isinstance(field.min, int) or not isinstance(field.max, int) or isinstance(field.min, bool) or isinstance(field.max, bool)):
        errors.append(f"{where}: min/max باید عدد صحیح باشند.")
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
