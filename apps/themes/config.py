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
    FEATURE_ICONS,
    HOME_SECTION_TYPES,
    MAX_FEATURE_ITEMS,
    MAX_MENU_ITEMS,
    MAX_SECTION_BUTTONS,
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
                {"label": gettext("Home"), "kind": "home", "target": None, "url": "", "new_tab": False, "translations": {}},
                {"label": gettext("Posts"), "kind": "blog", "target": None, "url": "", "new_tab": False, "translations": {}},
            ],
            "footer": [],
        },
        "home": {
            "sections": [
                {
                    "type": "hero", "enabled": True, "title": "", "subtitle": "", "body": "",
                    "button_label": gettext("View posts"), "button_url": "/blog/", "buttons": [{"label": gettext("View posts"), "url": "/blog/"}],
                    "items": [], "translations": {}, "category": None, "page": None, "count": 6,
                },
                {
                    "type": "latest_posts", "enabled": True, "title": gettext("Latest posts"), "subtitle": "", "body": "",
                    "button_label": "", "button_url": "", "buttons": [], "items": [], "translations": {},
                    "category": None, "page": None, "count": 6,
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
    item["translations"] = sanitize_menu_translations(raw.get("translations"), kind)
    return item


def sanitize_menu_translations(raw, kind: str) -> dict:
    """{language: {"label", "target" | "url"}}: a menu item's own title and destination for one language. ``target`` (a page or
    category id) is used by page/category items and ``url`` by link items; whatever is empty falls back to the item's own value
    (the site's default language), field by field. Structure (kind, order, new tab) stays shared."""
    from apps.core.regional import normalize_language

    result = {}
    for code, entry in _dict(raw).items():
        code = normalize_language(code)
        entry = _dict(entry)
        if not code:
            continue
        cleaned = {"label": _text(entry.get("label"), 60)}
        if kind in ("page", "category"):
            target = _int_or_none(entry.get("target"))
            if target is not None:
                cleaned["target"] = target
        elif kind == "url":
            url = _link(entry.get("url"))
            if url:
                cleaned["url"] = url
        if cleaned["label"] or len(cleaned) > 1:
            result[code] = cleaned
    return dict(sorted(result.items()))


def sanitize_buttons(raw_buttons, first_label="", first_url="") -> list[dict]:
    """[{"label", "url"}] (at most MAX_SECTION_BUTTONS). A button needs both a label and a valid link; without a ``buttons`` list the
    older single button (button_label/button_url) is the first one."""
    entries = []
    if isinstance(raw_buttons, list):
        entries = [_dict(b) for b in raw_buttons]
    elif first_label or first_url:
        entries = [{"label": first_label, "url": first_url}]
    buttons = []
    for entry in entries:
        label, url = _text(entry.get("label"), 40), _link(entry.get("url"))
        if label and url:
            buttons.append({"label": label, "url": url})
    return buttons[:MAX_SECTION_BUTTONS]


def sanitize_items(raw_items) -> list[dict]:
    """Feature items: [{"title", "icon", "text"}]. The icon must be one of FEATURE_ICONS (else empty); an item needs a title or text."""
    items = []
    for entry in raw_items if isinstance(raw_items, list) else []:
        entry = _dict(entry)
        title, text = _text(entry.get("title"), 60), _text(entry.get("text"), 300)
        icon = entry.get("icon") if entry.get("icon") in FEATURE_ICONS else ""
        if title or text:
            items.append({"title": title, "icon": icon, "text": text})
    return items[:MAX_FEATURE_ITEMS]


def sanitize_section_translations(raw) -> dict:
    """{language: {"title", "subtitle", "body", "button_labels": [...], "items": [{"title", "text"}]}}.

    Only text can differ per language: structure (type, links, icons, order, counts) is shared. Entries that match an item or button
    by position; an empty value means "use the text of the site's default language"."""
    from apps.core.regional import normalize_language

    result = {}
    for code, entry in _dict(raw).items():
        code = normalize_language(code)
        entry = _dict(entry)
        if not code:
            continue
        cleaned = {
            "title": _text(entry.get("title"), 120),
            "subtitle": _text(entry.get("subtitle"), 300),
            "body": _text(entry.get("body"), 5000),
            "button_labels": [_text(v, 40) for v in (entry.get("button_labels") if isinstance(entry.get("button_labels"), list) else [])][:MAX_SECTION_BUTTONS],
            "items": [
                {"title": _text(_dict(i).get("title"), 60), "text": _text(_dict(i).get("text"), 300)}
                for i in (entry.get("items") if isinstance(entry.get("items"), list) else [])
            ][:MAX_FEATURE_ITEMS],
        }
        if (cleaned["title"] or cleaned["subtitle"] or cleaned["body"] or any(cleaned["button_labels"])
                or any(i["title"] or i["text"] for i in cleaned["items"])):
            result[code] = cleaned
    return dict(sorted(result.items()))


def sanitize_section(raw) -> dict | None:
    raw = _dict(raw)
    kind = raw.get("type")
    if kind not in HOME_SECTION_TYPES:
        return None
    count = raw.get("count")
    count = count if isinstance(count, int) and not isinstance(count, bool) and 1 <= count <= 24 else 6
    buttons = sanitize_buttons(raw.get("buttons"), _text(raw.get("button_label"), 40), _link(raw.get("button_url")))
    # button_label/button_url stay the first button's values (themes and "view all" links written for one button keep working)
    if buttons:
        first_label, first_url = buttons[0]["label"], buttons[0]["url"]
    else:  # a link without text (a post list's "view all" falls back to a default label)
        first_label, first_url = _text(raw.get("button_label"), 40), _link(raw.get("button_url"))
    return {
        "type": kind,
        "enabled": _bool(raw.get("enabled"), True),
        "title": _text(raw.get("title"), 120),
        "subtitle": _text(raw.get("subtitle"), 300),
        "body": _text(raw.get("body"), 5000),
        "button_label": first_label,
        "button_url": first_url,
        "buttons": buttons,
        "items": sanitize_items(raw.get("items")),
        "translations": sanitize_section_translations(raw.get("translations")),
        "category": _int_or_none(raw.get("category")),
        "page": _int_or_none(raw.get("page")),
        "count": count,
    }


def localized_section(section: dict, language: str, default_language: str) -> dict:
    """The section with the texts of ``language`` applied (a copy). The site's default language, or a language without translations,
    gets the base text; an empty translated value falls back to the base text, field by field."""
    from apps.core.regional import normalize_language

    language, default_language = normalize_language(language), normalize_language(default_language)
    tr = section.get("translations", {}).get(language) if language and language != default_language else None
    if not tr:
        return section
    out = dict(section)
    for key in ("title", "subtitle", "body"):
        if tr.get(key):
            out[key] = tr[key]
    labels = tr.get("button_labels") or []
    buttons = [{**b, "label": (labels[i] if i < len(labels) and labels[i] else b["label"])} for i, b in enumerate(section["buttons"])]
    out["buttons"] = buttons
    if buttons:
        out["button_label"], out["button_url"] = buttons[0]["label"], buttons[0]["url"]
    elif section["button_url"] and labels and labels[0]:  # single-button sections (e.g. "view all" link of a post list)
        out["button_label"] = labels[0]
    translated = tr.get("items") or []
    out["items"] = [
        {**item, "title": (translated[i]["title"] if i < len(translated) and translated[i]["title"] else item["title"]),
         "text": (translated[i]["text"] if i < len(translated) and translated[i]["text"] else item["text"])}
        for i, item in enumerate(section["items"])
    ]
    return out


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
        sections = [sanitize_section(s) for s in base["home"]["sections"]]

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
