"""Legacy: the schema-1 document shape, kept because the old migrations import it. New work lives in apps.studio."""
from django.utils.translation import gettext, gettext_lazy
import re
import secrets
from dataclasses import dataclass

from django.utils.functional import Promise
from urllib.parse import urlsplit

SCHEMA_VERSION = 1

LIMITS = {
    "sections": 40,
    "rows_per_section": 20,
    "columns_per_row": 6,
    "elements_per_column": 30,
    "total_nodes": 1200,
    "max_json_bytes": 400_000,
}

ELEMENT_TYPES = ("heading", "text", "image", "button")
MAX_ERRORS = 25

_ID_RE = re.compile(r"^[a-z0-9]{4,16}$")
_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f  ]")
_URL_CHARS_RE = re.compile(r"^[^\s\x00-\x1f\x7f\\<>\"'`]+$")


@dataclass(frozen=True)
class Field:
    name: str
    kind: str  # text | textarea | color | int | choice | bool | media | url
    label: str
    default: object = ""
    group: str = "style"  # content | layout | style
    min: int | None = None
    max: int | None = None
    choices: tuple = ()  # ((value, label), ...)
    max_len: int = 200
    required: bool = False
    css: str | None = None  # نام ویژگی CSS (یا custom property)؛ None یعنی فقط داده/ساختار است
    unit: str = ""
    template: str | None = None  # مثلاً 'url("{}")'
    target: str = "outer"  # outer | core (عنصر داخلی مثل h2 / a / img)
    help: str = ""

    def __getattribute__(self, name):
        # Defaults such as the placeholder heading text are lazy translations; consumers need a plain str.
        value = object.__getattribute__(self, name)
        if name == "default" and isinstance(value, Promise):
            return str(value)
        return value

    def to_client(self) -> dict:
        data = {"name": self.name, "kind": self.kind, "label": str(self.label), "default": self.default, "group": self.group,
                "css": self.css, "unit": self.unit, "template": self.template, "target": self.target, "help": str(self.help),
                "required": self.required}
        if self.kind == "int":
            data.update(min=self.min, max=self.max)
        if self.kind in ("text", "textarea", "url"):
            data["max_len"] = self.max_len
        if self.kind == "choice":
            data["choices"] = [[c[0], str(c[1])] for c in self.choices]
        return data


ALIGN = (("start", gettext_lazy("Start side")), ("center", gettext_lazy("Center")), ("end", gettext_lazy("End side")))
ALIGN_TEXT = ALIGN + (("justify", gettext_lazy("Justified")),)
BORDER_STYLES = (("solid", gettext_lazy("Continuous")), ("dashed", gettext_lazy("Dashed")), ("dotted", gettext_lazy("Dotted")))


def _border(radius_target="outer", radius_max=60):
    return [
        Field("border_width", "int", gettext_lazy("Border width (px)"), 0, "style", 0, 12, css="border-width", unit="px"),
        Field("border_style", "choice", gettext_lazy("Border style"), "solid", "style", choices=BORDER_STYLES, css="border-style"),
        Field("border_color", "color", gettext_lazy("Border color"), "", "style", css="border-color"),
        Field("radius", "int", gettext_lazy("Corner radius (px)"), 0, "style", 0, radius_max, css="border-radius", unit="px", target=radius_target),
    ]


def _box(padding_default=0, radius_target="outer", with_padding=True):
    fields = [Field("bg_color", "color", gettext_lazy("Background color"), "", "style", css="background-color")]
    if with_padding:
        fields.append(Field("padding", "int", gettext_lazy("Inner spacing (px)"), padding_default, "layout", 0, 120, css="padding", unit="px"))
    fields += _border(radius_target)
    fields.append(Field("margin_bottom", "int", gettext_lazy("Spacing to the next element (px)"), 16, "layout", 0, 160, css="margin-bottom", unit="px"))
    return fields


SPECS: dict[str, list[Field]] = {
    "section": [
        Field("width", "choice", gettext_lazy("Content width"), "boxed", "layout", choices=(("boxed", gettext_lazy("Contained (centered)")), ("full", gettext_lazy("Full width")))),
        Field("padding_y", "int", gettext_lazy("Top and bottom spacing (px)"), 48, "layout", 0, 240, css="padding-block", unit="px"),
        Field("bg_color", "color", gettext_lazy("Background color"), "", "style", css="background-color"),
        Field("bg_media", "media", gettext_lazy("Background image"), None, "style", css="background-image", template='url("{}")'),
        Field("text_color", "color", gettext_lazy("Section text color"), "", "style", css="color"),
        *_border(),
    ],
    "row": [
        Field("gap", "int", gettext_lazy("Gap between columns (px)"), 24, "layout", 0, 80, css="gap", unit="px"),
        Field("valign", "choice", gettext_lazy("Vertical alignment of columns"), "start",
              "layout", choices=(("start", gettext_lazy("Top")), ("center", gettext_lazy("Center")), ("end", gettext_lazy("Bottom")), ("stretch", gettext_lazy("Stretch"))), css="align-items"),
        Field("stack_at", "choice", gettext_lazy("Stack columns"), "mobile", "layout",
              choices=(("mobile", gettext_lazy("On mobile only")), ("tablet", gettext_lazy("On tablet and mobile")), ("never", gettext_lazy("Never")))),
    ],
    "column": [
        Field("span", "int", gettext_lazy("Column width (out of 12)"), 12, "layout", 1, 12, css="--bx-span"),
        *_box(0)[:-1],  # بدون «فاصله تا عنصر بعد»
    ],
    "heading": [
        Field("text", "text", gettext_lazy("Heading text"), gettext_lazy("Your heading"), "content", max_len=300, required=True),
        Field("level", "choice", gettext_lazy("Heading level"), "2", "content", choices=tuple((str(n), f"H{n}") for n in range(2, 7)),
              help=gettext_lazy("H1 is only for the page’s own title.")),
        Field("font_size", "int", gettext_lazy("Text size (px)"), 32, "style", 12, 96, css="font-size", unit="px", target="core"),
        Field("color", "color", gettext_lazy("Text color"), "", "style", css="color", target="core"),
        Field("align", "choice", gettext_lazy("Alignment"), "start", "style", choices=ALIGN, css="text-align"),
        *_box(),
    ],
    "text": [
        Field("text", "textarea", gettext_lazy("Text"), gettext_lazy("Write your text here."), "content", max_len=5000, required=True),
        Field("font_size", "int", gettext_lazy("Text size (px)"), 17, "style", 10, 48, css="font-size", unit="px", target="core"),
        Field("color", "color", gettext_lazy("Text color"), "", "style", css="color", target="core"),
        Field("align", "choice", gettext_lazy("Alignment"), "start", "style", choices=ALIGN_TEXT, css="text-align"),
        *_box(),
    ],
    "image": [
        Field("media", "media", gettext_lazy("Image"), None, "content", required=True),
        Field("alt", "text", gettext_lazy("Alternative text (Alt)"), "", "content", max_len=200,
              help=gettext_lazy("If empty, the media library’s alt text is used.")),
        Field("link_url", "url", gettext_lazy("Image link (optional)"), "", "content"),
        Field("link_new_tab", "bool", gettext_lazy("Open the link in a new tab"), False, "content"),
        Field("width_pct", "int", gettext_lazy("Image width (percent)"), 100, "layout", 10, 100, css="width", unit="%", target="core"),
        Field("align", "choice", gettext_lazy("Alignment"), "start", "style", choices=ALIGN, css="text-align"),
        *_box(radius_target="core", with_padding=False),
    ],
    "button": [
        Field("label", "text", gettext_lazy("Button text"), gettext_lazy("Button"), "content", max_len=80, required=True),
        Field("url", "url", gettext_lazy("Link URL"), "/", "content", required=True),
        Field("new_tab", "bool", gettext_lazy("Open in a new tab"), False, "content"),
        Field("variant", "choice", gettext_lazy("Style"), "solid", "style", choices=(("solid", gettext_lazy("Solid")), ("outline", gettext_lazy("Outline")))),
        Field("bg_color", "color", gettext_lazy("Button color"), "", "style", css="--bx-c", target="core",
              help=gettext_lazy("If empty, the site theme’s primary color is used.")),
        Field("text_color", "color", gettext_lazy("Button text color"), "", "style", css="--bx-t", target="core"),
        Field("font_size", "int", gettext_lazy("Text size (px)"), 16, "style", 12, 32, css="font-size", unit="px", target="core"),
        Field("radius", "int", gettext_lazy("Corner radius (px)"), 8, "style", 0, 50, css="border-radius", unit="px", target="core"),
        Field("align", "choice", gettext_lazy("Alignment"), "start", "layout", choices=ALIGN, css="text-align"),
        Field("margin_bottom", "int", gettext_lazy("Spacing to the next element (px)"), 16, "layout", 0, 160, css="margin-bottom", unit="px"),
    ],
}

META_FIELDS = [Field("show_title", "bool", gettext_lazy("Show the page title above the content"), True, "content")]

_FIELD_INDEX = {t: {f.name: f for f in fields} for t, fields in SPECS.items()}
_META_INDEX = {f.name: f for f in META_FIELDS}


def client_specs() -> dict:
    """جدول مشخصات برای مرورگر (ساخت پنل تنظیمات و اعمال سبک در بوم)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "limits": LIMITS,
        "meta": [f.to_client() for f in META_FIELDS],
        "types": {t: [f.to_client() for f in fields] for t, fields in SPECS.items()},
    }


def defaults(type_: str) -> dict:
    return {f.name: f.default for f in SPECS[type_]}


def new_id() -> str:
    return "n" + secrets.token_hex(4)


def empty_document() -> dict:
    return {"schema_version": SCHEMA_VERSION, "meta": {f.name: f.default for f in META_FIELDS}, "sections": []}


# ------------------------------------------------------------------ اعتبارسنجی
class DocumentInvalid(Exception):
    """سند نامعتبر است. ``errors``: فهرست (مسیر، پیام)."""

    def __init__(self, errors):
        self.errors = list(errors)[:MAX_ERRORS]
        super().__init__("; ".join(f"{p}: {m}" for p, m in self.errors))

    def as_json(self):
        return [{"path": p, "message": m} for p, m in self.errors]


class _Collector:
    def __init__(self, strict=False):
        self.errors = []
        self.strict = strict  # حالت سخت‌گیرانه (ارسال/انتشار): فیلدهای الزامی نباید خالی باشند

    def add(self, path, message):
        if len(self.errors) < MAX_ERRORS:
            self.errors.append((path, message))

    @property
    def full(self):
        return len(self.errors) >= MAX_ERRORS


from apps.core.safeurl import is_safe_url  # noqa: E402,F401  (kept for older imports)


def _clean_text(value: str, multiline: bool) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = _CONTROL_RE.sub("", value)
    if not multiline:
        value = value.replace("\n", " ").replace("\t", " ")
    return value.strip()


def _validate_value(field: Field, value, path: str, errs: _Collector):
    """مقدار پاک‌سازی‌شده را برمی‌گرداند؛ در صورت خطا خطا ثبت و پیش‌فرض را برمی‌گرداند."""
    kind = field.kind
    label = field.label
    if kind in ("text", "textarea"):
        if not isinstance(value, str):
            errs.add(path, (gettext("“%(label)s” must be text.") % {"label": label}))
            return field.default
        value = _clean_text(value, kind == "textarea")
        if len(value) > field.max_len:
            errs.add(path, (gettext("“%(label)s” can be at most %(max_len)s characters.") % {"label": label, "max_len": field.max_len}))
            return field.default
        if field.required and errs.strict and not value:
            errs.add(path, (gettext("“%(label)s” must not be empty.") % {"label": label}))
        return value
    if kind == "color":
        if value in ("", None):
            return ""
        if not isinstance(value, str) or not _COLOR_RE.match(value):
            errs.add(path, (gettext("“%(label)s” must be a hexadecimal color such as #1d4ed8.") % {"label": label}))
            return ""
        return value.lower()
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            errs.add(path, (gettext("“%(label)s” must be an integer.") % {"label": label}))
            return field.default
        if not field.min <= value <= field.max:
            errs.add(path, (gettext("“%(label)s” must be between %(min)s and %(max)s.") % {"label": label, "min": field.min, "max": field.max}))
            return field.default
        return value
    if kind == "choice":
        allowed = {c[0] for c in field.choices}
        if not isinstance(value, str) or value not in allowed:
            errs.add(path, (gettext("The value of “%(label)s” is not allowed.") % {"label": label}))
            return field.default
        return value
    if kind == "bool":
        if not isinstance(value, bool):
            errs.add(path, (gettext("“%(label)s” must be true/false.") % {"label": label}))
            return field.default
        return value
    if kind == "media":
        if value is None:
            if field.required and errs.strict:
                errs.add(path, (gettext("Choose “%(label)s”.") % {"label": label}))
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > 2**53:
            errs.add(path, (gettext("The ID of “%(label)s” is invalid.") % {"label": label}))
            return None
        return value
    if kind == "url":
        if not isinstance(value, str):
            errs.add(path, (gettext("“%(label)s” must be text.") % {"label": label}))
            return field.default
        value = value.strip()
        if not value:
            if field.required and errs.strict:
                errs.add(path, (gettext("“%(label)s” must not be empty.") % {"label": label}))
            return ""
        if not is_safe_url(value):
            errs.add(path, (gettext("The URL of “%(label)s” is not allowed (only internal paths, http(s), mailto and tel).") % {"label": label}))
            return field.default
        return value
    raise AssertionError(kind)  # pragma: no cover


def _validate_props(type_: str, raw, path: str, errs: _Collector) -> dict:
    if not isinstance(raw, dict):
        errs.add(path, gettext("Properties must be an object."))
        return defaults(type_)
    index = _FIELD_INDEX[type_]
    for key in raw:
        if key not in index:
            errs.add(f"{path}.{key}", gettext("This property is not allowed."))
    out = {}
    for name, field in index.items():
        out[name] = _validate_value(field, raw[name], f"{path}.{name}", errs) if name in raw else field.default
    return out


def _check_keys(node, allowed: set, path: str, errs: _Collector) -> bool:
    if not isinstance(node, dict):
        errs.add(path, gettext("Must be an object."))
        return False
    for key in node:
        if key not in allowed:
            errs.add(f"{path}.{key}", gettext("Unknown key."))
    return True


def _node_id(node: dict, path: str, seen: set, errs: _Collector) -> str:
    nid = node.get("id")
    if not isinstance(nid, str) or not _ID_RE.match(nid):
        errs.add(f"{path}.id", gettext("The node ID is invalid."))
        return new_id()
    if nid in seen:
        errs.add(f"{path}.id", gettext("The node ID is duplicated."))
    seen.add(nid)
    return nid


def _children(node: dict, key: str, limit: int, path: str, errs: _Collector, label: str):
    items = node.get(key, [])
    if not isinstance(items, list):
        errs.add(f"{path}.{key}", gettext("Must be a list."))
        return []
    if len(items) > limit:
        errs.add(f"{path}.{key}", (gettext("At most %(limit)s %(label)s are allowed.") % {"limit": limit, "label": label}))
        return []
    return items


def migrate_document(raw):
    """سند ذخیره‌شده با نسخه‌ی قدیمی‌تر را به نسخه‌ی فعلی ارتقا می‌دهد. نسخه‌ی جدیدتر یا نامعلوم خطاست."""
    if not isinstance(raw, dict):
        raise DocumentInvalid([("$", gettext("The document must be an object."))])
    version = raw.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise DocumentInvalid([("$.schema_version", gettext("The document schema version is invalid."))])
    if version > SCHEMA_VERSION or version < 1:
        raise DocumentInvalid([("$.schema_version", (gettext("Schema version %(version)s is not supported.") % {"version": version}))])
    # نسخه‌های بعدی: اینجا زنجیره‌ی مهاجرت (v1→v2→…) اضافه می‌شود.
    return raw


def validate_document(raw, *, strict: bool = False) -> dict:
    """سند خام (JSON پارس‌شده از مرورگر یا دیتابیس) را اعتبارسنجی و به شکل استاندارد درمی‌آورد. خطا: DocumentInvalid.

    ``strict=False`` برای ذخیره‌ی پیش‌نویس (فیلد الزامی نیمه‌کاره مجاز است)؛ ``strict=True`` هنگام ارسال برای تأیید/انتشار.
    """
    raw = migrate_document(raw)
    errs = _Collector(strict)
    _check_keys(raw, {"schema_version", "meta", "sections"}, "$", errs)

    meta_raw = raw.get("meta", {})
    meta = {f.name: f.default for f in META_FIELDS}
    if not isinstance(meta_raw, dict):
        errs.add("$.meta", gettext("Must be an object."))
    else:
        for key, value in meta_raw.items():
            if key not in _META_INDEX:
                errs.add(f"$.meta.{key}", gettext("This property is not allowed."))
            else:
                meta[key] = _validate_value(_META_INDEX[key], value, f"$.meta.{key}", errs)

    seen: set = set()
    count = 0
    sections_out = []
    for si, sec in enumerate(_children(raw, "sections", LIMITS["sections"], "$", errs, gettext("Section"))):
        if errs.full:
            break
        sp = f"$.sections[{si}]"
        if not _check_keys(sec, {"id", "props", "rows"}, sp, errs):
            continue
        count += 1
        out_sec = {"id": _node_id(sec, sp, seen, errs), "props": _validate_props("section", sec.get("props", {}), f"{sp}.props", errs), "rows": []}
        for ri, row in enumerate(_children(sec, "rows", LIMITS["rows_per_section"], sp, errs, gettext("Row"))):
            rp = f"{sp}.rows[{ri}]"
            if not _check_keys(row, {"id", "props", "columns"}, rp, errs):
                continue
            count += 1
            out_row = {"id": _node_id(row, rp, seen, errs), "props": _validate_props("row", row.get("props", {}), f"{rp}.props", errs), "columns": []}
            span_total = 0
            for ci, col in enumerate(_children(row, "columns", LIMITS["columns_per_row"], rp, errs, gettext("Column"))):
                cp = f"{rp}.columns[{ci}]"
                if not _check_keys(col, {"id", "props", "elements"}, cp, errs):
                    continue
                count += 1
                col_props = _validate_props("column", col.get("props", {}), f"{cp}.props", errs)
                span_total += col_props["span"]
                out_col = {"id": _node_id(col, cp, seen, errs), "props": col_props, "elements": []}
                for ei, el in enumerate(_children(col, "elements", LIMITS["elements_per_column"], cp, errs, gettext("Element"))):
                    ep = f"{cp}.elements[{ei}]"
                    if not _check_keys(el, {"id", "type", "props"}, ep, errs):
                        continue
                    etype = el.get("type")
                    if etype not in ELEMENT_TYPES:
                        errs.add(f"{ep}.type", gettext("The element type is not allowed."))
                        continue
                    count += 1
                    out_col["elements"].append({
                        "id": _node_id(el, ep, seen, errs), "type": etype,
                        "props": _validate_props(etype, el.get("props", {}), f"{ep}.props", errs),
                    })
                out_row["columns"].append(out_col)
            if span_total > 12:
                errs.add(rp, gettext("The total width of the columns in a row must not exceed 12."))
            out_sec["rows"].append(out_row)
        sections_out.append(out_sec)

    if count > LIMITS["total_nodes"]:
        errs.add("$", (gettext("The total number of page components must not exceed %(LIMITS)s.") % {"LIMITS": LIMITS['total_nodes']}))
    if errs.errors:
        raise DocumentInvalid(errs.errors)
    return {"schema_version": SCHEMA_VERSION, "meta": meta, "sections": sections_out}


def media_ids(doc: dict) -> set[int]:
    """شناسه‌ی همه‌ی رسانه‌هایی که سند به آن‌ها ارجاع می‌دهد (سند باید اعتبارسنجی‌شده باشد)."""
    found = set()
    for sec in doc["sections"]:
        if sec["props"].get("bg_media"):
            found.add(sec["props"]["bg_media"])
        for row in sec["rows"]:
            for col in row["columns"]:
                for el in col["elements"]:
                    if el["type"] == "image" and el["props"].get("media"):
                        found.add(el["props"]["media"])
    return found
