"""
رندر امن سند صفحه‌ساز به HTML.

خروجی فقط از عناصر مجازِ ``schema.SPECS`` ساخته می‌شود: همه‌ی متن‌ها escape می‌شوند، هیچ ویژگی ``on*``، ``<script>``،
``style`` درون‌خطی یا HTML آزاد تولید نمی‌شود و مقدارهای CSS فقط از فیلدهای اعتبارسنجی‌شده (عدد/انتخاب/رنگ هگز/شناسه‌ی رسانه)
می‌آیند. سند پیش از رندر دوباره اعتبارسنجی می‌شود (دفاع در عمق؛ حتی اگر دیتابیس دستکاری شده باشد).
"""
import logging
from urllib.parse import quote

from django.utils.html import escape
from django.utils.safestring import mark_safe

from apps.media_library.models import Media

from .schema import SPECS, DocumentInvalid, media_ids, validate_document

logger = logging.getLogger(__name__)

# CSS ساختاری مشترک بین سایت و بوم ویرایشگر (بدون media query تا بوم بتواند دستگاه را شبیه‌سازی کند).
BASE_CSS = """
.bx-page,.bx-page *,.bx-page *::before,.bx-page *::after{box-sizing:border-box}
.bx-sec,.bx-col,.bx-el{border:0 solid currentColor}
.bx-sec{padding-block:48px;background-size:cover;background-position:center;background-repeat:no-repeat}
.bx-wrap{width:100%;max-width:1100px;margin-inline:auto;padding-inline:16px}
.bx-sec-full>.bx-wrap{max-width:none}
.bx-row{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:24px;align-items:start}
.bx-row+.bx-row{margin-top:24px}
.bx-col{min-width:0;grid-column:span var(--bx-span,12)}
.bx-el{margin-bottom:16px;overflow-wrap:anywhere}
.bx-heading .bx-core{margin:0;line-height:1.5}
.bx-text .bx-core{margin:0 0 1em;line-height:1.9}
.bx-text .bx-core:last-child{margin-bottom:0}
.bx-image .bx-core{display:inline-block;max-width:100%;height:auto;vertical-align:middle}
.bx-image a{display:inline-block;max-width:100%}
.bx-btn{display:inline-block;padding:.6em 1.5em;font:inherit;font-weight:700;line-height:1.5;text-align:center;text-decoration:none;
border:2px solid var(--bx-c,var(--rv-primary,#1d4ed8));border-radius:8px;background:var(--bx-c,var(--rv-primary,#1d4ed8));
color:var(--bx-t,var(--rv-on-primary,#fff))}
.bx-btn-outline{background:transparent;color:var(--bx-c,var(--rv-primary,#1d4ed8))}
""".strip()

RESPONSIVE_CSS = """
@media (max-width:1023px){.bx-page .bx-stack-tablet>.bx-col{grid-column:1/-1}}
@media (max-width:767px){.bx-page .bx-stack-tablet>.bx-col,.bx-page .bx-stack-mobile>.bx-col{grid-column:1/-1}}
""".strip()


def _css_url(url: str) -> str:
    return quote(url, safe="/:%?=&-_.~")


def _declarations(type_: str, props: dict, media_urls: dict, target: str) -> str:
    parts = []
    for f in SPECS[type_]:
        if f.css is None or f.target != target:
            continue
        value = props.get(f.name)
        if value is None or value == "":
            continue
        if f.kind == "media":
            url = media_urls.get(value)
            if not url:
                continue
            value = f.template.format(_css_url(url))
        else:
            value = f"{value}{f.unit}"
            if f.template:
                value = f.template.format(value)
        parts.append(f"{f.css}:{value}")
    return ";".join(parts)


class _Builder:
    def __init__(self, media):
        self.media = media  # {id: Media}
        self.media_urls = {pk: m.file.url for pk, m in media.items() if m.file}
        self.rules: list[str] = []

    def rule(self, nid: str, type_: str, props: dict):
        for target, suffix in (("outer", ""), ("core", " .bx-core")):
            decl = _declarations(type_, props, self.media_urls, target)
            if decl:
                self.rules.append(f".bx-page .bx-n-{nid}{suffix}{{{decl}}}")

    # -------------------------------------------------------------- عناصر
    def element(self, el: dict) -> str:
        t, p, nid = el["type"], el["props"], el["id"]
        if t == "image":
            m = self.media.get(p["media"]) if p["media"] else None
            if m is None or m.kind != Media.Kind.IMAGE or not m.file:
                return ""  # رسانه حذف شده/انتخاب نشده؛ عنصر نمایش داده نمی‌شود (سند دست‌نخورده می‌ماند)
            core = self._image(p, m)
        elif t == "heading":
            level = int(p["level"])
            core = f'<h{level} class="bx-core">{escape(p["text"])}</h{level}>'
        elif t == "text":
            core = self._paragraphs(p["text"])
        elif t == "button":
            core = self._button(p)
        else:  # pragma: no cover - اعتبارسنجی نوع‌های دیگر را رد کرده است
            return ""
        self.rule(nid, t, p)
        return f'<div class="bx-el bx-{t} bx-n-{nid}">{core}</div>'

    @staticmethod
    def _paragraphs(text: str) -> str:
        paras = [chunk for chunk in text.split("\n\n") if chunk.strip()]
        return "".join(f'<p class="bx-core">{escape(chunk.strip()).replace(chr(10), "<br>")}</p>' for chunk in paras)

    @staticmethod
    def _image(p: dict, m: Media) -> str:
        alt = p["alt"] or m.alt_text or ""
        size = f' width="{m.width}" height="{m.height}"' if m.width and m.height else ""
        img = f'<img class="bx-core" src="{escape(m.file.url)}" alt="{escape(alt)}"{size} loading="lazy" decoding="async">'
        if p["link_url"]:
            new = ' target="_blank" rel="noopener noreferrer"' if p["link_new_tab"] else ""
            return f'<a href="{escape(p["link_url"])}"{new}>{img}</a>'
        return img

    @staticmethod
    def _button(p: dict) -> str:
        new = ' target="_blank" rel="noopener noreferrer"' if p["new_tab"] else ""
        return (f'<a class="bx-core bx-btn bx-btn-{p["variant"]}" href="{escape(p["url"])}"{new}>'
                f'{escape(p["label"])}</a>')

    # -------------------------------------------------------------- ساختار
    def column(self, col: dict) -> str:
        self.rule(col["id"], "column", col["props"])
        inner = "".join(self.element(e) for e in col["elements"])
        return f'<div class="bx-col bx-n-{col["id"]}">{inner}</div>'

    def row(self, row: dict) -> str:
        self.rule(row["id"], "row", row["props"])
        stack = row["props"]["stack_at"]
        cls = "bx-row" + (f" bx-stack-{stack}" if stack != "never" else "")
        inner = "".join(self.column(c) for c in row["columns"])
        return f'<div class="{cls} bx-n-{row["id"]}">{inner}</div>'

    def section(self, sec: dict) -> str:
        self.rule(sec["id"], "section", sec["props"])
        cls = "bx-sec bx-sec-full" if sec["props"]["width"] == "full" else "bx-sec"
        inner = "".join(self.row(r) for r in sec["rows"])
        return f'<section class="{cls} bx-n-{sec["id"]}"><div class="bx-wrap">{inner}</div></section>'


def render_document(doc: dict) -> str:
    """HTML امن (با تگ ``<style>`` همراه) برای یک سند. سند نامعتبر ← رشته‌ی خالی (و ثبت هشدار)، نه خطای ۵۰۰."""
    try:
        doc = validate_document(doc)
    except DocumentInvalid as exc:
        logger.warning("سند صفحه‌ساز هنگام رندر نامعتبر بود و نمایش داده نشد: %s", exc)
        return mark_safe("")
    ids = media_ids(doc)
    media = {m.pk: m for m in Media.objects.filter(pk__in=ids)} if ids else {}
    builder = _Builder(media)
    body = "".join(builder.section(s) for s in doc["sections"])
    css = "\n".join([BASE_CSS, RESPONSIVE_CSS, *builder.rules])
    return mark_safe(f'<div class="bx-page"><style>{css}</style>{body}</div>')
