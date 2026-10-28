"""
The built-in widgets of Ravix Studio (stage 1). Each is one ``WidgetDef``: its settings, its extra style settings, its HTML and its CSS.

``render(ctx, node, props)`` returns the inner HTML; the renderer wraps it in ``<div class="st-w st-w-<type> st-n-<id>">``. Every text is
escaped here, URLs were validated when the document was saved, and the result is never post-processed by hand.
"""
from django.utils.html import escape
from django.utils.translation import gettext, gettext_lazy

from .fields import ALIGN, WEIGHTS, C, Field, I, sanitize_html
from .registry import WidgetDef, register_widget

TAGS = tuple((f"h{n}", f"H{n}") for n in range(1, 7))
RATIOS = (("16/9", "16:9"), ("4/3", "4:3"), ("1/1", "1:1"), ("21/9", "21:9"))
_PAD = ("padding_top", "padding_bottom", "padding_start", "padding_end")


def _typography(color=True, size=True, weight=False, line=True, *, size_default=None):
    out = []
    if color:
        out.append(C("color", gettext_lazy("Text color"), "color", target="core"))
    if size:
        out.append(I("font_size", gettext_lazy("Text size (px)"), size_default or 16, 8, 200, css="font-size", target="core"))
    if weight:
        out.append(Field("font_weight", "choice", gettext_lazy("Font weight"), "700", "style", choices=WEIGHTS, css="font-weight", target="core"))
    if line:
        out.append(I("line_height", gettext_lazy("Line height (%)"), 150, 80, 300, css="line-height", unit="%", target="core"))
    return tuple(out)


def _link(ctx, props, url_key="link_url", new_key="link_new_tab"):
    return ctx.link_attrs(props.get(url_key, ""), props.get(new_key, False))


# ------------------------------------------------------------------------------------------------------------ basic
def _heading(ctx, node, p):
    tag = p["tag"] if p["tag"] in ("h1", "h2", "h3", "h4", "h5", "h6") else "h2"
    text = escape(p["text"])
    if p.get("link_url"):
        text = f"<a{_link(ctx, p)}>{text}</a>"
    return f'<{tag} class="st-core" dir="auto">{text}</{tag}>'


def _text(ctx, node, p):
    return f'<div class="st-core st-prose">{ctx.markdown(p["content"])}</div>' if p["content"] else ""


def _button(ctx, node, p):
    size = p["size"] if p["size"] in ("sm", "md", "lg") else "md"
    return f'<a class="st-core st-btn st-btn-{p["variant"]} st-btn-{size}"{ctx.link_attrs(p["url"], p["new_tab"])}>{escape(p["label"])}</a>'


def _icon(ctx, node, p):
    if not p["icon"]:
        return ""
    label = p["label"]
    svg = ctx.icon(p["icon"], label=label)
    inner = f'<span class="st-core st-iconw">{svg}</span>'
    return f'<a{ctx.link_attrs(p["url"], p["new_tab"])}>{inner}</a>' if p["url"] else inner


def _divider(ctx, node, p):
    return f'<hr class="st-core st-divider st-divider-{p["line"]}">'


def _spacer(ctx, node, p):
    return '<div class="st-core st-spacer" aria-hidden="true"></div>'


def _heading_css():
    return ".st-w-heading .st-core{margin:0;line-height:1.35}.st-w-heading a{color:inherit;text-decoration:none}"


register_widget(WidgetDef(
    "heading", gettext_lazy("Heading"), "file-text", "basic",
    fields=(Field("text", "text", gettext_lazy("Heading text"), gettext_lazy("Your heading"), "content", max_len=300, required=True),
            Field("tag", "choice", gettext_lazy("Heading level"), "h2", "content", choices=TAGS, help=gettext_lazy("H1 is only for the page’s own title.")),
            Field("link_url", "url", gettext_lazy("Link (optional)"), "", "content"),
            Field("link_new_tab", "bool", gettext_lazy("Open the link in a new tab"), False, "content")),
    style_fields=_typography(weight=True, size_default=32), render=_heading, css=_heading_css(),
    default_style={"desktop": {"font_size": 32}},
))
register_widget(WidgetDef(
    "text", gettext_lazy("Text"), "file-text", "basic",
    fields=(Field("content", "markdown", gettext_lazy("Text"), gettext_lazy("Write your text here."), "content", max_len=20000, required=True,
                  help=gettext_lazy("Markdown: **bold**, *italic*, lists and [links](https://example.com). Raw HTML is not allowed.")),),
    style_fields=_typography(size_default=17), render=_text,
    css=".st-w-text .st-core>:first-child{margin-block-start:0}.st-w-text .st-core>:last-child{margin-block-end:0}.st-w-text .st-core{line-height:1.9}",
))
register_widget(WidgetDef(
    "button", gettext_lazy("Button"), "arrow-right", "basic",
    fields=(Field("label", "text", gettext_lazy("Button text"), gettext_lazy("Button"), "content", max_len=80, required=True),
            Field("url", "url", gettext_lazy("Link URL"), "/", "content", required=True),
            Field("new_tab", "bool", gettext_lazy("Open in a new tab"), False, "content"),
            Field("variant", "choice", gettext_lazy("Style"), "solid", "style", choices=(("solid", gettext_lazy("Solid")), ("outline", gettext_lazy("Outline")))),
            Field("size", "choice", gettext_lazy("Size"), "md", "style", choices=(("sm", gettext_lazy("Small")), ("md", gettext_lazy("Medium")), ("lg", gettext_lazy("Large"))))),
    style_fields=(C("btn_color", gettext_lazy("Button color"), "--st-c", target="core", help=gettext_lazy("If empty, the site theme’s primary color is used.")),
                  C("btn_text", gettext_lazy("Button text color"), "--st-t", target="core"),
                  I("font_size", gettext_lazy("Text size (px)"), 16, 10, 64, css="font-size", target="core"),
                  I("btn_radius", gettext_lazy("Corner radius (px)"), 8, 0, 100, css="border-radius", target="core")),
    render=_button,
    css=(".st-btn{display:inline-block;padding:.65em 1.5em;font:inherit;font-weight:700;line-height:1.4;text-align:center;text-decoration:none;"
         "border:2px solid var(--st-c,var(--rv-primary,#1d4ed8));border-radius:8px;background:var(--st-c,var(--rv-primary,#1d4ed8));"
         "color:var(--st-t,var(--rv-on-primary,#fff))}.st-btn:hover{opacity:.9}.st-btn-outline{background:transparent;color:var(--st-c,var(--rv-primary,#1d4ed8))}"
         ".st-btn-sm{font-size:.875em;padding:.5em 1.1em}.st-btn-lg{font-size:1.2em;padding:.8em 1.9em}"),
))
register_widget(WidgetDef(
    "icon", gettext_lazy("Icon"), "sparkles", "basic",
    fields=(Field("icon", "icon", gettext_lazy("Icon"), "star", "content", required=True),
            Field("label", "text", gettext_lazy("Accessible label (optional)"), "", "content", max_len=100,
                  help=gettext_lazy("Read by screen readers. Leave empty for a decorative icon.")),
            Field("url", "url", gettext_lazy("Link (optional)"), "", "content"),
            Field("new_tab", "bool", gettext_lazy("Open in a new tab"), False, "content")),
    style_fields=(I("icon_size", gettext_lazy("Icon size (px)"), 48, 12, 300, css="font-size", target="core"),
                  C("color", gettext_lazy("Icon color"), "color")),
    render=_icon, css=".st-iconw{display:inline-block;font-size:48px;line-height:1}.st-iconw .st-ic{display:block}",
))
register_widget(WidgetDef(
    "divider", gettext_lazy("Divider"), "minus", "basic",
    fields=(Field("line", "choice", gettext_lazy("Line style"), "solid", "style", choices=(("solid", gettext_lazy("Continuous")), ("dashed", gettext_lazy("Dashed")), ("dotted", gettext_lazy("Dotted")))),),
    style_fields=(C("line_color", gettext_lazy("Line color"), "border-top-color", target="core"),
                  I("line_width", gettext_lazy("Line thickness (px)"), 1, 1, 20, css="border-top-width", target="core"),
                  I("width", gettext_lazy("Width (percent)"), 100, 5, 100, css="width", unit="%", target="core")),
    render=_divider, hidden_style=("border_width", "border_style", "border_color"),
    css=".st-divider{border:0 solid rgba(127,127,127,.4);border-top-width:1px;margin:0 auto;height:0}.st-divider-dashed{border-top-style:dashed}.st-divider-dotted{border-top-style:dotted}.st-divider-solid{border-top-style:solid}",
))
register_widget(WidgetDef(
    "spacer", gettext_lazy("Spacer"), "layers", "basic",
    style_fields=(I("height", gettext_lazy("Height (px)"), 40, 0, 600, css="height", target="core"),),
    render=_spacer, css=".st-spacer{height:40px}", hidden_style=("bg_color", "bg_grad_from", "bg_grad_to", "bg_media", "border_width", "radius", "shadow", "color"),
))


# ------------------------------------------------------------------------------------------------------------ media
def _image(ctx, node, p):
    m = ctx.media(p["media"])
    if m is None or m.kind != "image" or not m.file:
        return ""
    caption = p["caption"]
    img = ctx.image(m, alt=p["alt"] or m.alt_text or "", sizes=p["sizes"] or "(max-width: 767px) 100vw, 1140px", node_id=node["id"],
                    cls="" if caption else "st-core")
    if p["link_url"]:
        img = f'<a{ctx.link_attrs(p["link_url"], p["link_new_tab"])}>{img}</a>'
    if caption:
        return f'<figure class="st-core">{img}<figcaption>{escape(caption)}</figcaption></figure>'
    return img


def _video(ctx, node, p):
    title = p["title"] or gettext("Video")
    if p["mode"] == "file":
        m = ctx.media(p["file"])
        if m is None or m.kind != "video" or not m.file:
            return ""
        poster = ctx.media(p["poster"])
        poster_attr = f' poster="{escape(poster.file.url)}"' if poster is not None and poster.kind == "image" and poster.file else ""
        return (f'<div class="st-core st-video st-r-{p["ratio"].replace("/", "-")}">'
                f'<video controls preload="none" playsinline title="{escape(title)}"{poster_attr} src="{escape(m.file.url)}"></video></div>')
    return ctx.lite_video(p["source"], title=title, ratio=p["ratio"], poster_media=ctx.media(p["poster"]))


def _gallery(ctx, node, p):
    items = []
    for item in p["images"]:
        m = ctx.media(item["media"])
        if m is None or m.kind != "image" or not m.file:
            continue
        img = ctx.image(m, alt=item["alt"] or m.alt_text or "", sizes="(max-width: 767px) 50vw, 400px", node_id=node["id"], eager=False, cls="")
        if p["link_full"]:
            img = f'<a href="{escape(m.file.url)}" target="_blank" rel="noopener noreferrer">{img}</a>'
        items.append(f"<li>{img}</li>")
    return f'<ul class="st-core st-gallery">{"".join(items)}</ul>' if items else ""


register_widget(WidgetDef(
    "image", gettext_lazy("Image"), "image", "media",
    fields=(Field("media", "media", gettext_lazy("Image"), None, "content", required=True),
            Field("alt", "text", gettext_lazy("Alternative text (Alt)"), "", "content", max_len=200, help=gettext_lazy("If empty, the media library’s alt text is used.")),
            Field("caption", "text", gettext_lazy("Caption (optional)"), "", "content", max_len=200),
            Field("link_url", "url", gettext_lazy("Image link (optional)"), "", "content"),
            Field("link_new_tab", "bool", gettext_lazy("Open the link in a new tab"), False, "content"),
            Field("sizes", "text", gettext_lazy("Display width hint (sizes attribute)"), "", "layout", max_len=120,
                  help=gettext_lazy("Advanced. Helps the browser pick the right file size, for example “(max-width: 767px) 100vw, 560px”."))),
    style_fields=(I("width", gettext_lazy("Image width (percent)"), 100, 5, 100, css="width", unit="%", target="core"),),
    render=_image,
    css=".st-w-image .st-core{display:inline-block;max-width:100%;height:auto;vertical-align:top;margin:0}.st-w-image img{display:block;max-width:100%;height:auto;border-radius:inherit}.st-w-image figcaption{font-size:.875em;opacity:.8;padding-block-start:.5em}",
))
register_widget(WidgetDef(
    "video", gettext_lazy("Video"), "play", "media",
    fields=(Field("mode", "choice", gettext_lazy("Video source"), "embed", "content",
                  choices=(("embed", gettext_lazy("YouTube, Vimeo or Aparat link")), ("file", gettext_lazy("A video from the media library")))),
            Field("source", "video", gettext_lazy("Video link"), "", "content", max_len=300, help=gettext_lazy("Paste a YouTube, Vimeo or Aparat link. The player loads only after a click.")),
            Field("file", "media", gettext_lazy("Video file"), None, "content", media_kind="video"),
            Field("poster", "media", gettext_lazy("Cover image (optional)"), None, "content"),
            Field("title", "text", gettext_lazy("Title (for screen readers)"), "", "content", max_len=120),
            Field("ratio", "choice", gettext_lazy("Aspect ratio"), "16/9", "layout", choices=RATIOS)),
    render=_video, script="video",
    css=(".st-video{position:relative;width:100%;background:#111;overflow:hidden;border-radius:inherit;aspect-ratio:16/9}.st-r-4-3{aspect-ratio:4/3}.st-r-1-1{aspect-ratio:1/1}.st-r-21-9{aspect-ratio:21/9}.st-r-16-9{aspect-ratio:16/9}.st-video video,.st-video iframe{position:absolute;inset:0;width:100%;height:100%;border:0}"
         ".st-video-btn{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#fff;text-decoration:none;background:#1a1a1a}"
         ".st-video-btn img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}.st-video-play{position:relative;width:68px;height:48px;border-radius:12px;background:rgba(0,0,0,.72);display:flex;align-items:center;justify-content:center}"
         ".st-video-btn:hover .st-video-play,.st-video-btn:focus-visible .st-video-play{background:var(--rv-primary,#1d4ed8)}.st-video-play .st-ic{width:24px;height:24px;fill:currentColor}"),
))
register_widget(WidgetDef(
    "gallery", gettext_lazy("Gallery"), "image", "media",
    fields=(Field("images", "list", gettext_lazy("Images"), [], "content", max_items=24, item_fields=(
        Field("media", "media", gettext_lazy("Image"), None, "content"), Field("alt", "text", gettext_lazy("Alternative text (Alt)"), "", "content", max_len=200))),
            Field("link_full", "bool", gettext_lazy("Open the full image when clicked"), False, "content")),
    style_fields=(I("columns", gettext_lazy("Columns"), 3, 1, 8, css="--st-cols", unit="", target="core"),
                  I("gap", gettext_lazy("Gap (px)"), 12, 0, 80, css="--st-gap", target="core")),
    default_style={"desktop": {"columns": 3}, "mobile": {"columns": 2}},
    render=_gallery,
    css=".st-gallery{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(var(--st-cols,3),minmax(0,1fr));gap:var(--st-gap,12px)}.st-gallery img{display:block;width:100%;height:auto;border-radius:inherit}",
))


# ------------------------------------------------------------------------------------------------------------ content
def _icon_list(ctx, node, p):
    rows = []
    for item in p["items"]:
        if not item["text"]:
            continue
        icon = ctx.icon(item["icon"] or p["default_icon"] or "check", label="") if (item["icon"] or p["default_icon"]) else ""
        rows.append(f'<li>{"<span class=st-il-ic>" + icon + "</span>" if icon else ""}<span dir="auto">{escape(item["text"])}</span></li>')
    return f'<ul class="st-core st-iconlist">{"".join(rows)}</ul>' if rows else ""


def _card(ctx, node, p):
    parts = []
    m = ctx.media(p["image"])
    if m is not None and m.kind == "image" and m.file:
        parts.append(f'<div class="st-card-img">{ctx.image(m, alt=p["image_alt"] or m.alt_text or "", sizes="(max-width: 767px) 100vw, 560px", node_id=node["id"])}</div>')
    body = []
    if p["title"]:
        body.append(f'<h3 dir="auto">{escape(p["title"])}</h3>')
    if p["text"]:
        body.append(f'<p dir="auto">{escape(p["text"])}</p>')
    if p["button_label"] and p["button_url"]:
        body.append(f'<p><a class="st-btn st-btn-outline st-btn-sm"{ctx.link_attrs(p["button_url"], False)}>{escape(p["button_label"])}</a></p>')
    if body:
        parts.append(f'<div class="st-card-body">{"".join(body)}</div>')
    return f'<article class="st-core st-card">{"".join(parts)}</article>' if parts else ""


def _quote(ctx, node, p):
    cite = f'<footer>{escape(p["cite"])}</footer>' if p["cite"] else ""
    return f'<blockquote class="st-core st-quote"><p dir="auto">{escape(p["text"])}</p>{cite}</blockquote>' if p["text"] else ""


def _custom_html(ctx, node, p):
    html = sanitize_html(p["html"]) if p["html"] else ""  # cleaned when stored and again here, so a tampered stored document cannot inject anything
    return f'<div class="st-core st-html">{html}</div>' if html else ""


register_widget(WidgetDef(
    "icon_list", gettext_lazy("Icon list"), "check", "content",
    fields=(Field("default_icon", "icon", gettext_lazy("Default icon"), "check", "content"),
            Field("items", "list", gettext_lazy("Items"), [], "content", max_items=30, item_fields=(
                Field("text", "text", gettext_lazy("Text"), "", "content", max_len=300),
                Field("icon", "icon", gettext_lazy("Icon (optional)"), "", "content")))),
    style_fields=(C("icon_color", gettext_lazy("Icon color"), "--st-ic", target="core"),
                  I("gap", gettext_lazy("Gap between items (px)"), 10, 0, 60, css="--st-gap", target="core"),
                  *_typography(size_default=17, line=False)),
    render=_icon_list,
    css=".st-iconlist{list-style:none;margin:0;padding:0;display:grid;gap:var(--st-gap,10px)}.st-iconlist li{display:flex;gap:.6em;align-items:flex-start}.st-il-ic{color:var(--st-ic,var(--rv-primary,#1d4ed8));display:inline-flex;margin-block-start:.2em}",
))
register_widget(WidgetDef(
    "card", gettext_lazy("Card"), "package", "content",
    fields=(Field("image", "media", gettext_lazy("Image (optional)"), None, "content"),
            Field("image_alt", "text", gettext_lazy("Alternative text (Alt)"), "", "content", max_len=200),
            Field("title", "text", gettext_lazy("Title"), gettext_lazy("Card title"), "content", max_len=120),
            Field("text", "textarea", gettext_lazy("Text"), "", "content", max_len=600),
            Field("button_label", "text", gettext_lazy("Button text (optional)"), "", "content", max_len=60),
            Field("button_url", "url", gettext_lazy("Button link"), "", "content")),
    style_fields=(), render=_card,
    default_style={"desktop": {"border_width": 1, "border_color": "#d9dee7", "radius": 12, "bg_color": "#ffffff"}},
    css=".st-w-card{overflow:hidden}.st-card-img img{display:block;width:100%;height:auto}.st-card-body{padding:20px}.st-card-body h3{margin:0 0 .5em}.st-card-body p{margin:0 0 1em}.st-card-body p:last-child{margin-block-end:0}",
))
register_widget(WidgetDef(
    "quote", gettext_lazy("Quote"), "message-circle", "content",
    fields=(Field("text", "textarea", gettext_lazy("Quote"), gettext_lazy("A short, memorable quote."), "content", max_len=600, required=True),
            Field("cite", "text", gettext_lazy("Source (optional)"), "", "content", max_len=120)),
    style_fields=(C("accent", gettext_lazy("Accent color"), "--st-ac", target="core"), *_typography(size_default=20)),
    render=_quote,
    css=".st-quote{margin:0;padding-inline-start:1.1em;border-inline-start:4px solid var(--st-ac,var(--rv-primary,#1d4ed8))}.st-quote p{margin:0}.st-quote footer{margin-block-start:.6em;font-size:.875em;opacity:.8}.st-quote footer::before{content:\"— \"}",
))
register_widget(WidgetDef(
    "custom_html", gettext_lazy("Custom HTML"), "code", "content", admin_only=True,
    fields=(Field("html", "html", gettext_lazy("HTML"), "", "content", max_len=20000,
                  help=gettext_lazy("Only administrators can use this widget. The HTML is cleaned: scripts, styles, frames, forms and event attributes are removed.")),),
    render=_custom_html, css=".st-html{overflow-wrap:anywhere}.st-html img{max-width:100%;height:auto}",
))


# ------------------------------------------------------------------------------------------------------------ layout
register_widget(WidgetDef(
    "inner_section", gettext_lazy("Inner section"), "layers", "layout", container=True,
    fields=(I("gap", gettext_lazy("Gap between columns (px)"), 24, 0, 120, group="layout"),
            Field("valign", "choice", gettext_lazy("Vertical alignment of columns"), "start", "layout",
                  choices=(("start", gettext_lazy("Top")), ("center", gettext_lazy("Center")), ("end", gettext_lazy("Bottom")), ("stretch", gettext_lazy("Stretch"))))),
    render=None,
))
