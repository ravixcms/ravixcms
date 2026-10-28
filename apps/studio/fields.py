"""
Field definitions and typed value validation for Ravix Studio.

Nothing free-form ever reaches the stored document or the generated CSS: every value is a number inside a range, a choice from a list,
a hex colour, a media ID, a safe URL or plain text. ``validate_value`` returns the cleaned value; problems are collected in a
``Collector`` instead of raising, so the editor can show all of them at once.
"""
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import nh3
from django.utils.functional import Promise
from django.utils.translation import gettext, gettext_lazy

from apps.core.safeurl import is_safe_url

MAX_ERRORS = 25
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f  ]")
COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
ANCHOR_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
HTML_TAG_RE = re.compile(r"<\s*[/!a-zA-Z?]")

# What the custom HTML widget (administrators only) may contain. Scripts, styles, frames, forms and event attributes are never allowed.
HTML_TAGS = {
    "a", "abbr", "address", "article", "aside", "b", "blockquote", "br", "caption", "cite", "code", "dd", "details", "div", "dl", "dt", "em",
    "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "i", "img", "kbd", "li", "mark", "nav", "ol", "p",
    "pre", "q", "s", "section", "small", "span", "strong", "sub", "summary", "sup", "table", "tbody", "td", "tfoot", "th", "thead", "time",
    "tr", "u", "ul",
}
HTML_ATTRS = {
    "*": {"class", "id", "title", "lang", "dir"},
    "a": {"href", "target"},
    "img": {"src", "alt", "width", "height", "loading"},
    "td": {"colspan", "rowspan"}, "th": {"colspan", "rowspan", "scope"},
    "time": {"datetime"}, "details": {"open"},
}
HTML_SCHEMES = {"http", "https", "mailto", "tel"}


@lru_cache(maxsize=1)
def icon_data() -> dict:
    return json.loads((Path(__file__).parent / "icons.json").read_text(encoding="utf-8"))


def icon_names() -> tuple:
    return tuple(sorted(icon_data()))


def sanitize_html(value: str) -> str:
    """The only HTML a document may hold (custom HTML widget and plugin widgets): cleaned with an allowlist, links get a safe rel."""
    return nh3.clean(value, tags=HTML_TAGS, attributes=HTML_ATTRS, url_schemes=HTML_SCHEMES, link_rel="noopener noreferrer nofollow",
                     strip_comments=True)


def strip_html(value: str) -> str:
    """Markdown text widgets do not accept raw HTML: tags are removed (the text between them stays)."""
    if not HTML_TAG_RE.search(value):
        return value
    return nh3.clean(value, tags=set()).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


@dataclass(frozen=True)
class Field:
    name: str
    kind: str  # text textarea markdown color int choice bool media url icon list video html anchor
    label: object
    default: object = ""
    group: str = "content"  # content | layout | style
    min: int | None = None
    max: int | None = None
    choices: tuple = ()  # ((value, label), ...)
    max_len: int = 200
    required: bool = False
    css: str | None = None  # CSS property for style fields (None: data only)
    unit: str = ""
    target: str = "outer"  # outer | core (the inner element: heading tag, image, link ...)
    help: object = ""
    media_kind: str = "image"  # for kind "media"
    item_fields: tuple = ()  # for kind "list"
    max_items: int = 20

    def __getattribute__(self, name):
        value = object.__getattribute__(self, name)
        if name == "default" and isinstance(value, Promise):
            return str(value)
        return value

    def to_client(self) -> dict:
        data = {"name": self.name, "kind": self.kind, "label": str(self.label), "default": self.default, "group": self.group,
                "help": str(self.help), "required": self.required}
        if self.kind == "int":
            data.update(min=self.min, max=self.max, unit=self.unit)
        if self.kind in ("text", "textarea", "markdown", "url", "html", "video"):
            data["max_len"] = self.max_len
        if self.kind == "choice":
            data["choices"] = [[c[0], str(c[1])] for c in self.choices]
        if self.kind == "media":
            data["media_kind"] = self.media_kind
        if self.kind == "list":
            data["max_items"] = self.max_items
            data["item_fields"] = [f.to_client() for f in self.item_fields]
        return data


class DocumentInvalid(Exception):
    """The document is invalid. ``errors``: list of (path, message)."""

    def __init__(self, errors):
        self.errors = list(errors)[:MAX_ERRORS]
        super().__init__("; ".join(f"{p}: {m}" for p, m in self.errors))

    def as_json(self):
        return [{"path": p, "message": str(m)} for p, m in self.errors]


class Collector:
    def __init__(self, strict=False, can_html=True, existing_html=None):
        self.errors = []
        self.strict = strict  # publishing / submitting: required fields must be filled
        self.can_html = can_html  # may this user write the custom HTML widget?
        self.existing_html = existing_html or {}  # {widget id: html already stored}: unchanged HTML stays valid for others
        self.current_id = ""

    def add(self, path, message):
        if len(self.errors) < MAX_ERRORS:
            self.errors.append((path, message))

    @property
    def full(self):
        return len(self.errors) >= MAX_ERRORS


def clean_text(value: str, multiline: bool) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = _CONTROL_RE.sub("", value)
    if not multiline:
        value = value.replace("\n", " ").replace("\t", " ")
    return value.strip()


def parse_video(value: str):
    """('youtube'|'vimeo'|'aparat', id) from a pasted link, or None. Only fixed, well-known link shapes are accepted."""
    value = (value or "").strip()
    m = re.fullmatch(r"(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|embed/|shorts/|live/)|youtube-nocookie\.com/embed/|youtu\.be/)([A-Za-z0-9_-]{11})(?:[?&#/].*)?", value)
    if m:
        return "youtube", m.group(1)
    m = re.fullmatch(r"(?:https?://)?(?:www\.|player\.)?vimeo\.com/(?:video/|channels/[A-Za-z0-9_-]+/|groups/[A-Za-z0-9_-]+/videos/)?(\d{5,12})(?:[/?#].*)?", value)
    if m:
        return "vimeo", m.group(1)
    m = re.fullmatch(r"(?:https?://)?(?:www\.)?aparat\.com/(?:v/|video/video/embed/videohash/)([A-Za-z0-9]{3,20})(?:[/?#].*)?", value)
    if m:
        return "aparat", m.group(1)
    return None


def _err_text(label, kind_msg):
    return kind_msg % {"label": label}


def validate_value(f: Field, value, path: str, errs: Collector):
    """Returns the cleaned value; on a problem records the error and returns the default."""
    kind, label = f.kind, f.label
    if kind in ("text", "textarea", "markdown", "anchor"):
        if not isinstance(value, str):
            errs.add(path, gettext("“%(label)s” must be text.") % {"label": label})
            return f.default
        value = clean_text(value, kind in ("textarea", "markdown"))
        if kind == "markdown":
            value = strip_html(value)
        if len(value) > f.max_len:
            errs.add(path, gettext("“%(label)s” can be at most %(max_len)s characters.") % {"label": label, "max_len": f.max_len})
            return f.default
        if kind == "anchor" and value and not ANCHOR_RE.match(value):
            errs.add(path, gettext("“%(label)s” may only contain lowercase letters, digits and hyphens, and must start with a letter.") % {"label": label})
            return ""
        if f.required and errs.strict and not value:
            errs.add(path, gettext("“%(label)s” must not be empty.") % {"label": label})
        return value
    if kind == "html":
        if not isinstance(value, str):
            errs.add(path, gettext("“%(label)s” must be text.") % {"label": label})
            return f.default
        if len(value) > f.max_len:
            errs.add(path, gettext("“%(label)s” can be at most %(max_len)s characters.") % {"label": label, "max_len": f.max_len})
            return f.default
        cleaned = sanitize_html(_CONTROL_RE.sub("", value)).strip()
        if not errs.can_html and cleaned != errs.existing_html.get(errs.current_id, ""):
            errs.add(path, gettext("Only administrators can add or change custom HTML."))
            return errs.existing_html.get(errs.current_id, "")
        return cleaned
    if kind == "color":
        if value in ("", None):
            return ""
        if not isinstance(value, str) or not COLOR_RE.match(value):
            errs.add(path, gettext("“%(label)s” must be a hexadecimal color such as #1d4ed8.") % {"label": label})
            return ""
        return value.lower()
    if kind == "int":
        if value in ("", None):
            return f.default
        if isinstance(value, bool) or not isinstance(value, int):
            errs.add(path, gettext("“%(label)s” must be an integer.") % {"label": label})
            return f.default
        if not f.min <= value <= f.max:
            errs.add(path, gettext("“%(label)s” must be between %(min)s and %(max)s.") % {"label": label, "min": f.min, "max": f.max})
            return f.default
        return value
    if kind == "choice":
        allowed = {c[0] for c in f.choices}
        if not isinstance(value, str) or value not in allowed:
            errs.add(path, gettext("The value of “%(label)s” is not allowed.") % {"label": label})
            return f.default
        return value
    if kind == "icon":
        if value in ("", None):
            return ""
        if not isinstance(value, str) or value not in icon_data():
            errs.add(path, gettext("The value of “%(label)s” is not allowed.") % {"label": label})
            return f.default
        return value
    if kind == "bool":
        if not isinstance(value, bool):
            errs.add(path, gettext("“%(label)s” must be true/false.") % {"label": label})
            return f.default
        return value
    if kind == "media":
        if value is None:
            if f.required and errs.strict:
                errs.add(path, gettext("Choose “%(label)s”.") % {"label": label})
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > 2**53:
            errs.add(path, gettext("The ID of “%(label)s” is invalid.") % {"label": label})
            return None
        return value
    if kind == "url":
        if not isinstance(value, str):
            errs.add(path, gettext("“%(label)s” must be text.") % {"label": label})
            return f.default
        value = value.strip()
        if not value:
            if f.required and errs.strict:
                errs.add(path, gettext("“%(label)s” must not be empty.") % {"label": label})
            return ""
        if not is_safe_url(value):
            errs.add(path, gettext("The URL of “%(label)s” is not allowed (only internal paths, http(s), mailto and tel).") % {"label": label})
            return f.default
        return value
    if kind == "video":
        if not isinstance(value, str):
            errs.add(path, gettext("“%(label)s” must be text.") % {"label": label})
            return f.default
        value = value.strip()
        if not value:
            if f.required and errs.strict:
                errs.add(path, gettext("“%(label)s” must not be empty.") % {"label": label})
            return ""
        if len(value) > f.max_len or parse_video(value) is None:
            errs.add(path, gettext("“%(label)s” must be a YouTube, Vimeo or Aparat video link.") % {"label": label})
            return f.default
        return value
    if kind == "list":
        if not isinstance(value, list):
            errs.add(path, gettext("Must be a list."))
            return []
        if len(value) > f.max_items:
            errs.add(path, gettext("At most %(limit)s %(label)s are allowed.") % {"limit": f.max_items, "label": label})
            return []
        out = []
        for i, item in enumerate(value):
            ip = f"{path}[{i}]"
            if not isinstance(item, dict):
                errs.add(ip, gettext("Must be an object."))
                continue
            index = {sub.name: sub for sub in f.item_fields}
            for key in item:
                if key not in index:
                    errs.add(f"{ip}.{key}", gettext("This property is not allowed."))
            out.append({name: validate_value(sub, item[name], f"{ip}.{name}", errs) if name in item else sub.default for name, sub in index.items()})
        return out
    raise AssertionError(kind)  # pragma: no cover


def field_defaults(fields) -> dict:
    return {f.name: ([] if f.kind == "list" else f.default) for f in fields}


# ---------------------------------------------------------------------------------------------------- shared option lists
ALIGN = (("start", gettext_lazy("Start side")), ("center", gettext_lazy("Center")), ("end", gettext_lazy("End side")))
ALIGN_TEXT = ALIGN + (("justify", gettext_lazy("Justified")),)
BORDER_STYLES = (("solid", gettext_lazy("Continuous")), ("dashed", gettext_lazy("Dashed")), ("dotted", gettext_lazy("Dotted")))
SHADOWS = (("none", gettext_lazy("None")), ("sm", gettext_lazy("Small")), ("md", gettext_lazy("Medium")), ("lg", gettext_lazy("Large")))
WEIGHTS = tuple((str(n), str(n)) for n in (400, 500, 600, 700, 800, 900))


def I(name, label, default, lo, hi, css=None, unit="px", group="style", target="outer", help=""):  # noqa: E743
    return Field(name, "int", label, default, group, lo, hi, css=css, unit=unit, target=target, help=help)


def C(name, label, css=None, target="outer", default="", group="style", help=""):
    return Field(name, "color", label, default, group, css=css, target=target, help=help)


def spacing_fields(prefix_css, label_prefix, names, hi=400, lo=0, group="layout"):
    """Four logical sides (top/bottom/start/end) of margin or padding."""
    sides = (("top", gettext_lazy("top"), "block-start"), ("bottom", gettext_lazy("bottom"), "block-end"),
             ("start", gettext_lazy("start side"), "inline-start"), ("end", gettext_lazy("end side"), "inline-end"))
    return [I(f"{names}_{key}", f"{label_prefix} ({label})", 0, lo, hi, css=f"{prefix_css}-{css}", group=group) for key, label, css in sides]


# Style fields every node can have (per device: desktop / tablet / mobile).
BOX_STYLE = [
    *spacing_fields("margin", gettext_lazy("Outer spacing"), "margin", hi=400, lo=-100),
    *spacing_fields("padding", gettext_lazy("Inner spacing"), "padding", hi=400),
    C("color", gettext_lazy("Text color"), "color"),
    C("bg_color", gettext_lazy("Background color"), "background-color"),
    C("bg_grad_from", gettext_lazy("Gradient start color")),
    C("bg_grad_to", gettext_lazy("Gradient end color")),
    I("bg_grad_angle", gettext_lazy("Gradient angle (degrees)"), 180, 0, 360, unit="deg"),
    Field("bg_media", "media", gettext_lazy("Background image"), None, "style"),
    Field("bg_size", "choice", gettext_lazy("Background image size"), "cover", "style", choices=(("cover", gettext_lazy("Cover")), ("contain", gettext_lazy("Contain")), ("auto", gettext_lazy("Original size")))),
    Field("bg_pos", "choice", gettext_lazy("Background image position"), "center", "style", choices=(("center", gettext_lazy("Center")), ("top", gettext_lazy("Top")), ("bottom", gettext_lazy("Bottom")))),
    I("border_width", gettext_lazy("Border width (px)"), 0, 0, 20, css="border-width"),
    Field("border_style", "choice", gettext_lazy("Border style"), "solid", "style", choices=BORDER_STYLES, css="border-style"),
    C("border_color", gettext_lazy("Border color"), "border-color"),
    I("radius", gettext_lazy("Corner radius (px)"), 0, 0, 200, css="border-radius"),
    Field("shadow", "choice", gettext_lazy("Shadow"), "none", "style", choices=SHADOWS),
    Field("text_align", "choice", gettext_lazy("Alignment"), "", "layout", choices=(("", gettext_lazy("Inherit")),) + ALIGN_TEXT, css="text-align"),
    I("min_height", gettext_lazy("Minimum height (px)"), 0, 0, 1500, css="min-height", group="layout"),
    Field("hidden", "bool", gettext_lazy("Hide on this screen size"), False, "layout"),
]
