"""
ساختار سند پیکربندی ظاهر سایت (ذخیره‌شده به‌صورت JSON در ``ThemeConfig``).

دو تابع اصلی:
- ``default_config()``: مقادیر پیش‌فرض.
- ``sanitize(doc)``: هر سند دلخواه را به یک سند کامل و امن تبدیل می‌کند و هرگز خطا نمی‌دهد؛ مقدار نامعتبر
  با پیش‌فرض جایگزین می‌شود. رندر عمومی همیشه از این تابع عبور می‌کند، پس داده خراب در دیتابیس هم سایت را
  نمی‌شکند و هیچ مقدار کنترل‌نشده‌ای وارد CSS یا HTML نمی‌شود.

اعتبارسنجی سخت‌گیرانه (با پیام خطا برای کاربر) در فرم‌های پنل انجام می‌شود.
"""
from django.utils.translation import gettext
import copy
import re

from django.core.exceptions import ValidationError

from .colors import palette_errors
from .constants import (
    DEFAULT_THEME,
    FONT_SIZES,
    FONTS,
    HOME_SECTION_TYPES,
    MAX_MENU_ITEMS,
    MAX_SECTIONS,
    MAX_SOCIAL_LINKS,
    MENU_KINDS,
    MENU_LOCATIONS,
)
from .validators import normalize_color, normalize_link

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,48}$")

DEFAULT_APPEARANCE = {
    "primary": "#4f46e5",
    "background": "#ffffff",
    "text": "#1d2433",
    "font": "vazirmatn",
    "font_size": "md",
    "logo_media": None,
    "favicon_media": None,
}


# Texts the default configuration is created with. They are stored in whatever language was active at that moment, so at render time
# they are shown in the visitor's language (a menu saying "Home" must not stay English on the Persian page, or the reverse).
# Text the owner wrote themselves is never touched: only an exact match of one of these words in some language is translated.
DEFAULT_TEXTS = ("Home", "Posts", "View posts", "Latest posts")
_default_text_index: dict | None = None


def localize_default_text(text: str) -> str:
    global _default_text_index
    if not text or text not in _all_default_forms():
        return text
    return gettext(_default_text_index[text])


def _all_default_forms() -> dict:
    global _default_text_index
    if _default_text_index is None:
        from django.conf import settings
        from django.utils import translation

        index = {}
        for msgid in DEFAULT_TEXTS:
            index.setdefault(msgid, msgid)
            for code, _name in settings.LANGUAGES:
                with translation.override(code):
                    index.setdefault(gettext(msgid), msgid)
        _default_text_index = index
    return _default_text_index


def default_config() -> dict:
    return {
        "theme": DEFAULT_THEME,
        "appearance": dict(DEFAULT_APPEARANCE),
        "header": {"show_tagline": True, "sticky": True, "show_search": True, "cta_label": "", "cta_url": ""},
        "footer": {"text": "", "show_menu": True, "show_contact": True, "social": []},
        "menus": {
            "header": [
                {"label": gettext("Home"), "kind": "home", "target": None, "url": "", "new_tab": False},
                {"label": gettext("Posts"), "kind": "blog", "target": None, "url": "", "new_tab": False},
            ],
            "footer": [],
        },
        "home": {
            "sections": [
                {
                    "type": "hero", "enabled": True, "title": "", "subtitle": "", "body": "",
                    "button_label": gettext("View posts"), "button_url": "/blog/", "category": None, "page": None, "count": 6,
                },
                {
                    "type": "latest_posts", "enabled": True, "title": gettext("Latest posts"), "subtitle": "", "body": "",
                    "button_label": "", "button_url": "", "category": None, "page": None, "count": 6,
                },
            ]
        },
    }


# ---------------------------------------------------------------------- کمکی‌های پاک‌سازی
def _text(value, max_len: int) -> str:
    if not isinstance(value, str):
        return ""
    value = "".join(ch for ch in value if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    return value.strip()[:max_len]


def _bool(value, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _int_or_none(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 0 < value < 2**31:
        return value
    return None


def _link(value) -> str:
    try:
        return normalize_link(value if isinstance(value, str) else "")
    except ValidationError:
        return ""


def _color(value, default: str) -> str:
    try:
        return normalize_color(value if isinstance(value, str) else "")
    except ValidationError:
        return default


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def sanitize_menu_item(raw) -> dict | None:
    raw = _dict(raw)
    kind = raw.get("kind")
    label = _text(raw.get("label"), 60)
    if kind not in MENU_KINDS or not label:
        return None
    item = {"label": label, "kind": kind, "target": None, "url": "", "new_tab": _bool(raw.get("new_tab"), False)}
    if kind in ("page", "category"):
        item["target"] = _int_or_none(raw.get("target"))
        if item["target"] is None:
            return None
    elif kind == "url":
        item["url"] = _link(raw.get("url"))
        if not item["url"]:
            return None
    return item


def sanitize_section(raw) -> dict | None:
    raw = _dict(raw)
    kind = raw.get("type")
    if kind not in HOME_SECTION_TYPES:
        return None
    count = raw.get("count")
    count = count if isinstance(count, int) and not isinstance(count, bool) and 1 <= count <= 24 else 6
    return {
        "type": kind,
        "enabled": _bool(raw.get("enabled"), True),
        "title": _text(raw.get("title"), 120),
        "subtitle": _text(raw.get("subtitle"), 300),
        "body": _text(raw.get("body"), 5000),
        "button_label": _text(raw.get("button_label"), 40),
        "button_url": _link(raw.get("button_url")),
        "category": _int_or_none(raw.get("category")),
        "page": _int_or_none(raw.get("page")),
        "count": count,
    }


def sanitize_social(raw) -> list[dict]:
    result = []
    for entry in raw if isinstance(raw, list) else []:
        entry = _dict(entry)
        label, url = _text(entry.get("label"), 40), _link(entry.get("url"))
        if label and url:
            result.append({"label": label, "url": url})
    return result[:MAX_SOCIAL_LINKS]


def sanitize(doc) -> dict:
    """سند دلخواه ← سند کامل و امن. هرگز استثنا نمی‌اندازد."""
    base = default_config()
    doc = _dict(doc)

    theme = doc.get("theme")
    theme = theme if isinstance(theme, str) and SLUG_RE.match(theme) else base["theme"]

    a = _dict(doc.get("appearance"))
    d = base["appearance"]
    primary = _color(a.get("primary"), d["primary"])
    background = _color(a.get("background"), d["background"])
    text = _color(a.get("text"), d["text"])
    if palette_errors(primary, background, text):  # ترکیب ناخوانا هرگز رندر نمی‌شود
        primary, background, text = d["primary"], d["background"], d["text"]
    appearance = {
        "primary": primary,
        "background": background,
        "text": text,
        "font": a.get("font") if a.get("font") in FONTS else d["font"],
        "font_size": a.get("font_size") if a.get("font_size") in FONT_SIZES else d["font_size"],
        "logo_media": _int_or_none(a.get("logo_media")),
        "favicon_media": _int_or_none(a.get("favicon_media")),
    }

    h = _dict(doc.get("header"))
    bh = base["header"]
    header = {
        "show_tagline": _bool(h.get("show_tagline"), bh["show_tagline"]),
        "sticky": _bool(h.get("sticky"), bh["sticky"]),
        "show_search": _bool(h.get("show_search"), bh["show_search"]),
        "cta_label": _text(h.get("cta_label"), 30),
        "cta_url": _link(h.get("cta_url")),
    }
    if not (header["cta_label"] and header["cta_url"]):
        header["cta_label"] = header["cta_url"] = ""

    f = _dict(doc.get("footer"))
    bf = base["footer"]
    footer = {
        "text": _text(f.get("text"), 300),
        "show_menu": _bool(f.get("show_menu"), bf["show_menu"]),
        "show_contact": _bool(f.get("show_contact"), bf["show_contact"]),
        "social": sanitize_social(f.get("social")),
    }

    raw_menus = _dict(doc.get("menus"))
    menus = {}
    for location in MENU_LOCATIONS:
        if location in raw_menus and isinstance(raw_menus[location], list):
            items = [sanitize_menu_item(i) for i in raw_menus[location]]
            menus[location] = [i for i in items if i][:MAX_MENU_ITEMS]
        else:
            menus[location] = base["menus"][location]

    raw_home = _dict(doc.get("home"))
    if isinstance(raw_home.get("sections"), list):
        sections = [sanitize_section(s) for s in raw_home["sections"]]
        sections = [s for s in sections if s][:MAX_SECTIONS]
    else:
        sections = base["home"]["sections"]

    return {
        "theme": theme,
        "appearance": appearance,
        "header": header,
        "footer": footer,
        "menus": menus,
        "home": {"sections": sections},
    }


def merged(doc, key: str, value) -> dict:
    """نسخه‌ای از doc که بخش key با value جایگزین شده است (بدون تغییر ورودی)."""
    result = copy.deepcopy(sanitize(doc))
    result[key] = value
    return result
