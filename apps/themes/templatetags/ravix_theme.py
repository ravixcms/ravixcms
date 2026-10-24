"""
کتابخانه تگ مخصوص قالب‌های سایت: ``{% load ravix_theme %}``.

این تنها کتابخانه‌ای است که قالب‌ها اجازه بارگذاری آن را دارند. تگ‌های ``theme_head`` و ``theme_toolbar`` برای
هر قالب الزامی هستند تا سئوی پایه، فونت‌ها، رنگ‌های سفارشی و نوار پیش‌نمایش همه‌جا یکسان کار کنند.
"""
from django.utils.translation import gettext
import json
import re

from django import template
from django.templatetags.static import static
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from apps.core import localefmt
from apps.core.templatetags import ravix_ui as _ui
from apps.core.templatetags.ravix_ui import fa_digits as _fa_digits

register = template.Library()

_HEX_RE = re.compile(r"^#[0-9a-f]{6}$")
_STATIC_PATH_RE = re.compile(r"^[A-Za-z0-9._\-/]+$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,48}$")
_VERSION_RE = re.compile(r"^[0-9A-Za-z.+\-]{1,45}$")
_JSON_ESCAPES = {ord(">"): "\\u003E", ord("<"): "\\u003C", ord("&"): "\\u0026", ord(" "): "\\u2028", ord(" "): "\\u2029"}


@register.filter(name="fa_digits")
def fa_digits(value):
    """Persian digits while the UI language is Persian; other languages keep Latin digits (alias: ``local_digits``)."""
    return _fa_digits(value)


register.filter("local_digits", fa_digits)
register.filter("local_date", _ui.jalali)
register.filter("local_datetime", _ui.jalali_datetime)
register.filter("money", _ui.money)


@register.simple_tag(takes_context=True)
def theme_static(context, path):
    """نشانی فایل استاتیک همین قالب: ``{% theme_static 'css/theme.css' %}``."""
    theme = context.get("theme") or {}
    slug = theme.get("slug", "") if isinstance(theme, dict) else ""
    version = theme.get("version", "") if isinstance(theme, dict) else ""
    path = str(path)
    if not _SLUG_RE.match(slug or "") or not _STATIC_PATH_RE.match(path) or ".." in path or path.startswith("/"):
        return ""
    url = static(f"themes/{slug}/{path}")
    # ?v=<نسخه قالب>: با افزایش نسخه در theme.json کش مرورگر خودکار نو می‌شود (فایل‌ها نام ثابت دارند).
    return f"{url}?v={version}" if _VERSION_RE.match(version or "") else url


def _css_vars(vars_: dict) -> str:
    """بدنه ``:root{...}``؛ فقط مقادیر اعتبارسنجی‌شده وارد می‌شوند (رنگ hex، فونت از فهرست سفید، اندازه عددی)."""
    out = []
    for key, css_name in (
        ("primary", "--rv-primary"),
        ("on_primary", "--rv-on-primary"),
        ("primary_soft", "--rv-primary-soft"),
        ("background", "--rv-bg"),
        ("surface", "--rv-surface"),
        ("border", "--rv-border"),
        ("text", "--rv-text"),
        ("muted", "--rv-muted"),
    ):
        value = vars_.get(key, "")
        if _HEX_RE.match(value):
            out.append(f"{css_name}:{value}")
    stack = localefmt.adapt_font_stack(vars_.get("font_stack", ""))
    if stack and re.fullmatch(r"[A-Za-z0-9 ,'\-]+", stack):
        out.append(f"--rv-font:{stack}")
    size = vars_.get("font_size_px")
    if isinstance(size, int) and 12 <= size <= 24:
        out.append(f"--rv-font-size:{size}px")
    return ":root{" + ";".join(out) + "}"


def _json_ld(data) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).translate(_JSON_ESCAPES)


@register.simple_tag(takes_context=True)
def language_switcher(context, css_class="rv-lang"):
    """Language selector for the site footer: ``{% language_switcher %}``. Posts to the language view (cookie + profile) and
    returns to the same page in the chosen language. Works without JavaScript."""
    from django.conf import settings
    from django.urls import reverse
    from django.utils import translation

    request = context.get("request")
    current = (translation.get_language() or "").lower()
    css_class = css_class if re.fullmatch(r"[A-Za-z0-9_\- ]{1,40}", str(css_class)) else "rv-lang"
    options = format_html_join(
        "", '<option value="{}" lang="{}"{}>{}</option>',
        ((code, code, mark_safe(" selected") if code == current else "", name) for code, name in settings.LANGUAGES))
    return format_html(
        '<form method="post" action="{}" class="{}"><input type="hidden" name="next" value="{}">'
        '<label class="rv-sr-only" for="rv-lang-select">{}</label>'
        '<select id="rv-lang-select" name="language">{}</select> <button type="submit">{}</button></form>',
        reverse("set_language"), css_class, request.get_full_path() if request is not None else "/", gettext("Language"), options,
        gettext("Change"))


@register.simple_tag
def feature_icon(name, size=28):
    """Inline SVG for a "features" item: ``{% feature_icon item.icon %}``. Draws nothing for an empty or unknown name. The shapes come
    from Ravix (not from user input), take their colour from ``currentColor`` and have class ``rv-icon``."""
    from apps.core.templatetags.ravix_ui import ICONS

    from ..constants import FEATURE_ICONS

    if name not in FEATURE_ICONS or name not in ICONS:
        return ""
    try:
        size = max(12, min(96, int(size)))
    except (TypeError, ValueError):
        size = 28
    return format_html(
        '<svg class="rv-icon" width="{0}" height="{0}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">{1}</svg>',
        size, mark_safe(ICONS[name]),
    )


@register.simple_tag(takes_context=True)
def theme_head(context):
    """عنوان صفحه، متاتگ‌های سئو، Open Graph، JSON-LD، فونت و رنگ‌های سفارشی. باید اولین مورد داخل <head> باشد."""
    seo = context.get("seo") or {}
    site = context.get("site") or {}
    vars_ = context.get("theme_vars") or {}
    parts = [
        mark_safe('<meta charset="utf-8">'),
        mark_safe('<meta name="viewport" content="width=device-width, initial-scale=1">'),
        format_html("<title>{}</title>", seo.get("title", "")),
    ]
    if seo.get("description"):
        parts.append(format_html('<meta name="description" content="{}">', seo["description"]))
    if seo.get("robots"):
        parts.append(format_html('<meta name="robots" content="{}">', seo["robots"]))
    if seo.get("canonical"):
        parts.append(format_html('<link rel="canonical" href="{}">', seo["canonical"]))
    for lang, href in seo.get("alternates") or ():  # hreflang: the same page in every UI language
        parts.append(format_html('<link rel="alternate" hreflang="{}" href="{}">', lang, href))
    for feed in context.get("feeds") or ():  # autodiscovery فید RSS/Atom
        parts.append(format_html('<link rel="alternate" type="{}" title="{}" href="{}">', feed.type, feed.title, feed.url))
    for rel in ("prev", "next"):
        if seo.get(rel):
            parts.append(format_html('<link rel="{}" href="{}">', rel, seo[rel]))
    og = [
        ("og:type", seo.get("og_type", "website")),
        ("og:site_name", site.get("name", "")),
        ("og:title", seo.get("title", "")),
        ("og:description", seo.get("description", "")),
        ("og:url", seo.get("canonical", "")),
        ("og:locale", localefmt.og_locale()),
        ("og:image", seo.get("og_image", "")),
    ]
    for prop, value in og:
        if value:
            parts.append(format_html('<meta property="{}" content="{}">', prop, value))
    if seo.get("og_type") == "article":
        for prop, key in (("article:published_time", "published_time"), ("article:modified_time", "modified_time")):
            if seo.get(key):
                parts.append(format_html('<meta property="{}" content="{}">', prop, seo[key]))
    twitter = [
        ("twitter:card", "summary_large_image" if seo.get("og_image") else "summary"),
        ("twitter:title", seo.get("title", "")),
        ("twitter:description", seo.get("description", "")),
        ("twitter:image", seo.get("og_image", "")),
    ]
    for name, value in twitter:
        if value:
            parts.append(format_html('<meta name="{}" content="{}">', name, value))
    favicon = site.get("favicon_url")
    if favicon:
        parts.append(format_html('<link rel="icon" href="{}">', favicon))
    else:
        parts.append(mark_safe('<link rel="icon" href="data:,">'))  # از درخواست بی‌فایده /favicon.ico جلوگیری می‌کند
    preload = vars_.get("font_preload_url")
    if preload and not localefmt.uses_arabic_script():  # the Persian/Arabic font is not used for this language
        preload = static("fonts/inter-latin-wght-normal.woff2") if localefmt.base_language() != "zh" else ""
    if preload:
        parts.append(format_html('<link rel="preload" href="{}" as="font" type="font/woff2" crossorigin>', preload))
    parts.append(format_html('<link rel="stylesheet" href="{}">', static("public/fonts.css")))
    parts.append(mark_safe('<style id="rv-theme-vars">' + _css_vars(vars_) + "</style>"))
    for data in seo.get("jsonld") or []:
        parts.append(mark_safe('<script type="application/ld+json">' + _json_ld(data) + "</script>"))
    return mark_safe("\n".join(parts))


_TOOLBAR_CSS = (
    "#rv-toolbar{position:relative;z-index:10000;font:14px/1.6 system-ui,Tahoma,sans-serif;background:#151a2d;color:#fff;"
    "padding:.4rem 1rem;display:flex;gap:1rem;align-items:center;flex-wrap:wrap}"
    "#rv-toolbar a{color:#fff;text-decoration:underline}#rv-toolbar a:hover{color:#c7d2fe}"
    "#rv-toolbar.rv-preview{background:#b45309}"
)


@register.simple_tag(takes_context=True)
def theme_toolbar(context):
    """نوار مدیریت برای کاربران وارد‌شده + هشدار حالت پیش‌نمایش. بلافاصله بعد از <body> قرار می‌گیرد."""
    viewer = context.get("viewer")
    if not viewer or context.get("embedded"):
        return ""
    is_preview = bool(context.get("is_preview"))
    links = []
    if viewer.get("edit_url"):
        links.append(format_html(gettext("<a href=\"{}\">Edit this item</a>"), viewer["edit_url"]))
    if viewer.get("customize_url"):
        links.append(format_html('<a href="{}">{}</a>', viewer["customize_url"], gettext("Back to the theme editor") if is_preview else gettext("Customize appearance")))
    links.append(format_html(gettext("<a href=\"{}\">Admin panel</a>"), viewer.get("panel_url", "/panel/")))
    message = (
        mark_safe(gettext("<strong>Preview mode:</strong> these changes are not published yet and visitors do not see them."))
        if is_preview
        else format_html(gettext("Signed in: {}"), viewer.get("name", ""))
    )
    return format_html(
        gettext("<style>{}</style><div id=\"rv-toolbar\" class=\"{}\" role=\"region\" aria-label=\"Admin bar\"><span>{}</span>{}</div>"),
        mark_safe(_TOOLBAR_CSS),
        "rv-preview" if is_preview else "",
        message,
        mark_safe(" ".join(links)),
    )
