"""
Server-side rendering of a Studio document to HTML and CSS.

The output is built only from the validated document: every text is escaped, links were checked when the document was saved, CSS comes from
``css.py`` (typed values only). A page is rendered once, when it is published, and stored; visitors get the stored text and no JavaScript
besides an optional ~1 KB script for lite video players.
"""
import hashlib
import logging
from dataclasses import dataclass, field

from django.utils.html import escape

from apps.core.markup import render_markdown
from apps.media_library.models import Media

from . import media_images, video
from .css import DEVICES, Sheet, assert_safe_css
from .fields import icon_data, parse_video, sanitize_html
from .registry import get_widget
from .schema import COLUMN_STYLE_INDEX, SECTION_STYLE_INDEX, DocumentInvalid, media_ids, validate_document

logger = logging.getLogger(__name__)

# Bump when the HTML/CSS a widget produces changes: `manage.py studio_rebuild` (and migrate) then re-render every published page.
RENDERER_VERSION = 1

BASE_CSS = (
    ".st-root,.st-root *,.st-root *::before,.st-root *::after{box-sizing:border-box}"
    ".st-sprite{position:absolute;width:0;height:0;overflow:hidden}"
    ".st-sec{position:relative}"
    ".st-wrap{width:100%;max-width:1140px;margin-inline:auto;padding-inline:16px}"
    ".st-full>.st-wrap{max-width:none}"
    ".st-cols{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:24px;align-items:start}"
    ".st-ai-center{align-items:center}.st-ai-end{align-items:end}.st-ai-stretch{align-items:stretch}"
    ".st-col{grid-column:span 12;min-width:0}"
    ".st-va-start{align-self:start}.st-va-center{align-self:center}.st-va-end{align-self:end}.st-va-stretch{align-self:stretch}"
    ":where(.st-col)>:where(.st-w){margin-block-end:16px}:where(.st-col)>:where(.st-w):last-child{margin-block-end:0}"
    ".st-w{max-width:100%;overflow-wrap:anywhere}"
    ".st-ic{width:1em;height:1em;flex:none;vertical-align:-.125em}"
)


@dataclass
class Rendered:
    html: str = ""
    css: str = ""
    widgets: list = field(default_factory=list)
    media: list = field(default_factory=list)
    scripts: list = field(default_factory=list)
    frame_hosts: list = field(default_factory=list)

    @property
    def css_hash(self) -> str:
        return hashlib.sha256(self.css.encode()).hexdigest()[:20]


class RenderCtx:
    def __init__(self, media: dict, *, preview: bool, make_files: bool):
        self.media_map = media
        self.preview = preview
        self.make_files = make_files
        self.used: list = []
        self.icons: dict = {}
        self.scripts: set = set()
        self.frames: set = set()
        self.media_used: set = set()
        self._image_count = 0

    # ----- helpers for widgets
    def media(self, pk):
        return self.media_map.get(pk) if pk else None

    def markdown(self, text: str) -> str:
        return render_markdown(text)

    def link_attrs(self, url: str, new_tab: bool) -> str:
        if not url:
            return ""
        extra = ' target="_blank" rel="noopener noreferrer"' if new_tab else ""
        return f' href="{escape(url)}"{extra}'

    def icon(self, name: str, label: str = "") -> str:
        inner = icon_data().get(name)
        if inner is None:
            return ""
        self.icons[name] = inner
        aria = f' role="img" aria-label="{escape(label)}"' if label else ' aria-hidden="true" focusable="false"'
        return f'<svg class="st-ic"{aria}><use href="#st-i-{name}"/></svg>'

    def image(self, m, *, alt: str, sizes: str, node_id: str = "", eager=None, cls: str = "st-core") -> str:
        self.media_used.add(m.pk)
        self._image_count += 1
        if eager is None:
            eager = self._image_count == 1  # the first image of the page is the likely LCP element: not lazy, high priority
        rend = media_images.renditions(m, make=self.make_files) if m.width else []
        attrs = [f'src="{escape(m.file.url)}"', f'alt="{escape(alt)}"']
        if cls:
            attrs.insert(0, f'class="{cls}"')
        if m.width and m.height:
            attrs += [f'width="{m.width}"', f'height="{m.height}"']
        if rend:
            srcset = ", ".join(f"{escape(u)} {w}w" for u, w in rend) + f", {escape(m.file.url)} {m.width}w"
            attrs += [f'srcset="{srcset}"', f'sizes="{escape(sizes)}"']
        attrs += ['loading="eager" fetchpriority="high"' if eager else 'loading="lazy"', 'decoding="async"']
        return f"<img {' '.join(attrs)}>"

    def lite_video(self, source: str, *, title: str, ratio: str, poster_media=None) -> str:
        parsed = parse_video(source)
        if parsed is None:
            return ""
        provider, vid = parsed
        self.scripts.add("video")
        self.frames.add(video.EMBED_HOSTS[provider])
        cover = ""
        if poster_media is not None and poster_media.kind == "image" and poster_media.file:
            self.media_used.add(poster_media.pk)
            size = f' width="{poster_media.width}" height="{poster_media.height}"' if poster_media.width and poster_media.height else ""
            cover = f'<img src="{escape(poster_media.file.url)}" alt=""{size} loading="lazy" decoding="async">'
        else:
            found = video.poster(provider, vid, fetch=self.make_files)
            if found:
                url, w, h = found
                cover = f'<img src="{escape(url)}" alt="" width="{w}" height="{h}" loading="lazy" decoding="async">'
        play = self.icon("play")
        watch = video.WATCH_URL[provider].format(id=vid)
        return (f'<div class="st-core st-video st-r-{ratio.replace("/", "-")}" data-st-video="{provider}:{vid}">'
                f'<a class="st-video-btn" href="{escape(watch)}" target="_blank" rel="noopener noreferrer" aria-label="{escape(title)}">'
                f'{cover}<span class="st-video-play">{play}</span></a></div>')


class PluginCtx:
    """What a plugin widget's ``render(props, ctx)`` may use."""

    def __init__(self, ctx: RenderCtx):
        self._ctx = ctx

    def escape(self, value) -> str:
        return escape(str(value))

    def icon(self, name: str, label: str = "") -> str:
        return self._ctx.icon(name, label)

    def markdown(self, text: str) -> str:
        return self._ctx.markdown(str(text))

    def media_url(self, pk):
        m = self._ctx.media(pk)
        return m.file.url if m is not None and m.file else ""


def plugin_render_adapter(fn):
    def adapter(ctx, node, props):
        try:
            return sanitize_html(str(fn(props, PluginCtx(ctx)) or ""))
        except Exception:  # noqa: BLE001 — a broken plugin widget renders nothing instead of breaking the page
            logger.exception("Ravix Studio plugin widget %s failed to render", node.get("type"))
            return ""

    return adapter


# ----------------------------------------------------------------------------------------------------------------- tree
def _widget_class(wtype: str) -> str:
    return "st-w-" + wtype.replace(".", "-").replace("_", "-")


class _Renderer:
    def __init__(self, ctx: RenderCtx, sheet: Sheet, media_urls: dict):
        self.ctx = ctx
        self.sheet = sheet
        self.media_urls = media_urls
        self.preview = ctx.preview

    def _attrs(self, kind: str, node: dict, extra_cls: str = "") -> str:
        cls = f'{extra_cls} st-n-{node["id"]}'.strip()
        data = f' data-st-id="{node["id"]}" data-st-kind="{kind}"' if self.preview else ""
        return f'class="{cls}"{data}'

    def _style(self, node, index, extra=None):
        self.sheet.node(node["id"], node.get("style"), index, self.media_urls, extra=extra)

    def widget(self, w: dict) -> str:
        spec = get_widget(w["type"])
        if spec is None:
            return ""
        if w["type"] not in self.ctx.used:
            self.ctx.used.append(w["type"])
        data = f' data-st-type="{w["type"]}"' if self.preview else ""
        attrs = self._attrs("widget", w, f"st-w {_widget_class(w['type'])}") + data
        self._style(w, spec.style_index)
        if spec.container:
            self._cols_rules(w["id"], w["props"], inner=True)
            inner = self.columns(w["columns"], list_id=w["id"])
            return f'<div {attrs}><div class="st-cols{self._ai(w["props"])}"{self._list(w["id"], "columns") }>{inner}</div></div>'
        inner = spec.render(self.ctx, w, w["props"]) if spec.render else ""
        if not inner and not self.preview:
            return ""
        if not inner:
            inner = '<div class="st-ph">' + escape(str(spec.title)) + "</div>"
        return f"<div {attrs}>{inner}</div>"

    @staticmethod
    def _ai(props) -> str:
        v = props.get("valign", "start")
        return f" st-ai-{v}" if v in ("center", "end", "stretch") else ""

    def _list(self, owner: str, kind: str) -> str:
        return f' data-st-list="{kind}" data-st-owner="{owner}"' if self.preview else ""

    def _cols_rules(self, node_id: str, props: dict, *, inner: bool):
        decl = []
        if props.get("gap", 24) != 24:
            decl.append(f"gap:{int(props['gap'])}px")
        if decl:
            target = f".st-n-{node_id}>.st-cols" if inner else f".st-n-{node_id}>.st-wrap>.st-cols"
            self.sheet.raw("desktop", f"{target}{{{';'.join(decl)}}}")

    def column(self, col: dict, owner: str) -> str:
        extras = {}
        for device in DEVICES:
            span = (col.get("style") or {}).get(device, {}).get("span")
            if span:
                extras[device] = f"grid-column:span {int(span)}"
        self._style(col, COLUMN_STYLE_INDEX, extra=extras)
        va = col["props"].get("valign", "")
        cls = "st-col" + (f" st-va-{va}" if va in ("start", "center", "end", "stretch") else "")
        inner = "".join(self.widget(w) for w in col["widgets"])
        if self.preview and not inner:
            inner = '<div class="st-empty" data-st-empty="1"></div>'
        return f'<div {self._attrs("column", col, cls)}{self._list(col["id"], "widgets")}>{inner}</div>'

    def columns(self, columns: list, list_id: str) -> str:
        return "".join(self.column(c, list_id) for c in columns)

    def section(self, sec: dict) -> str:
        props = sec["props"]
        self._style(sec, SECTION_STYLE_INDEX)
        wrap_decl = []
        if props["content_width"] == "boxed" and props["max_width"] != 1140:
            wrap_decl.append(f"max-width:{int(props['max_width'])}px")
        if wrap_decl:
            self.sheet.raw("desktop", f".st-n-{sec['id']}>.st-wrap{{{';'.join(wrap_decl)}}}")
        self._cols_rules(sec["id"], props, inner=False)
        cls = "st-sec" + (" st-full" if props["content_width"] == "full" else "")
        anchor = f' id="{escape(props["anchor"])}"' if props["anchor"] else ""
        cols = self.columns(sec["columns"], sec["id"])
        return (f'<section {self._attrs("section", sec, cls)}{anchor}><div class="st-wrap">'
                f'<div class="st-cols{self._ai(props)}"{self._list(sec["id"], "columns")}>{cols}</div></div></section>')


PREVIEW_CSS = (
    ".st-preview [data-st-id]{cursor:default}.st-empty{min-height:56px;border:1px dashed rgba(127,127,127,.6);border-radius:6px;background:rgba(127,127,127,.06)}"
    ".st-ph{padding:14px;border:1px dashed rgba(127,127,127,.6);border-radius:6px;text-align:center;opacity:.7;font-size:14px}"
)


def render_document(doc: dict, *, preview: bool = False, make_files: bool = False, validate: bool = True, can_html: bool = True) -> Rendered:
    """Render a document. An invalid document renders as an empty result (and a warning), never as a 500."""
    if validate:
        try:
            doc = validate_document(doc, can_html=can_html, existing_html=None)
        except DocumentInvalid as exc:
            logger.warning("A Ravix Studio document was invalid at render time and was not displayed: %s", exc)
            return Rendered()
    ids = media_ids(doc)
    media = Media.objects.in_bulk(ids) if ids else {}
    media_urls = {pk: m.file.url for pk, m in media.items() if m.file}
    ctx = RenderCtx(media, preview=preview, make_files=make_files)
    sheet = Sheet(preview=preview)
    r = _Renderer(ctx, sheet, media_urls)
    body = "".join(r.section(s) for s in doc["sections"])
    sprite = ""
    if ctx.icons:
        symbols = "".join(f'<symbol id="st-i-{name}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{inner}</symbol>'
                          for name, inner in sorted(ctx.icons.items()))
        sprite = f'<svg class="st-sprite" aria-hidden="true" focusable="false">{symbols}</svg>'
    generated = sheet.text()
    try:
        assert_safe_css(generated)
    except ValueError:
        logger.exception("Ravix Studio generated unsafe CSS; the per-element rules were dropped")
        generated = ""
    css = BASE_CSS + "".join(get_widget(t).css for t in ctx.used if get_widget(t) is not None) + (PREVIEW_CSS if preview else "") + generated
    root_cls = "st-root st-preview" if preview else "st-root"
    root_attrs = ' data-st-list="sections" data-st-owner="root"' if preview else ""
    return Rendered(
        html=f'<div class="{root_cls}"{root_attrs}>{body}{sprite}</div>',
        css=css, widgets=list(ctx.used), media=sorted(ctx.media_used | ids), scripts=sorted(ctx.scripts), frame_hosts=sorted(ctx.frames),
    )
