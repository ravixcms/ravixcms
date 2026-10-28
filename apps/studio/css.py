"""
CSS generation for Ravix Studio.

No CSS string ever comes from a user. Rules are written by this module from typed style values (numbers in a range, hex colours, choices
from a list, a media ID turned into a URL by the server) and every selector is ``.st-n-<id>`` with an ID that matches a fixed pattern.
``assert_safe_css`` re-checks the final text as a second line of defence.
"""
import logging
import re
from urllib.parse import quote

from .fields import COLOR_RE

logger = logging.getLogger(__name__)

DEVICES = ("desktop", "tablet", "mobile")
# desktop first: tablet rules also apply on mobile unless the mobile settings override them
MEDIA = {"tablet": "@media (max-width:1023px){", "mobile": "@media (max-width:767px){"}
HIDDEN_MEDIA = {"desktop": "@media (min-width:1024px){", "tablet": "@media (min-width:768px) and (max-width:1023px){", "mobile": "@media (max-width:767px){"}
SHADOW_CSS = {
    "sm": "0 1px 3px rgba(0,0,0,.14),0 1px 2px rgba(0,0,0,.1)",
    "md": "0 6px 16px rgba(0,0,0,.14),0 2px 6px rgba(0,0,0,.08)",
    "lg": "0 18px 40px rgba(0,0,0,.2),0 6px 14px rgba(0,0,0,.1)",
}
_NODE_ID_RE = re.compile(r"^[a-z0-9]{4,16}$")
_VALUE_OK = re.compile(r'^[#a-zA-Z0-9%.,()\-+ /_:"!]*$')
_PROP_OK = re.compile(r"^(--[a-z][a-z0-9-]*|[a-z][a-z-]*)$")
_FORBIDDEN = re.compile(r"@import|expression\s*\(|javascript:|vbscript:|behavior\s*:|-moz-binding|</?style|<|>|\\|/\*|\*/", re.I)


def css_url(url: str) -> str:
    return quote(url, safe="/:%?=&-_.~")


def sel(node_id: str) -> str:
    if not _NODE_ID_RE.match(node_id):
        raise ValueError("bad node id")
    return f".st-n-{node_id}"


def _decl(prop: str, value: str) -> str:
    if not _PROP_OK.match(prop) or not _VALUE_OK.match(value) or _FORBIDDEN.search(value):
        logger.warning("Ravix Studio dropped an unsafe CSS declaration (%s)", prop)
        return ""
    return f"{prop}:{value}"


def _background(style: dict, media_urls: dict) -> str:
    layers = []
    c1, c2 = style.get("bg_grad_from"), style.get("bg_grad_to")
    if c1 and c2 and COLOR_RE.match(c1) and COLOR_RE.match(c2):
        layers.append(f"linear-gradient({int(style.get('bg_grad_angle', 180))}deg,{c1},{c2})")
    url = media_urls.get(style.get("bg_media")) if style.get("bg_media") else None
    if url:
        layers.append(f'url("{css_url(url)}")')
    if not layers:
        return ""
    out = [_decl("background-image", ",".join(layers))]
    if url:
        out.append(_decl("background-size", style.get("bg_size", "cover")))
        out.append(_decl("background-position", style.get("bg_pos", "center")))
        out.append("background-repeat:no-repeat")
    return ";".join(d for d in out if d)


def declarations(style: dict, index: dict, media_urls: dict, target: str = "outer") -> str:
    """Declarations for one device of one node, for the fields whose ``target`` is ``target``."""
    parts = []
    for name, value in style.items():
        f = index.get(name)
        if f is None or f.css is None or f.target != target:
            continue
        if f.kind == "int":
            text = f"{value}{f.unit}"
        elif f.kind in ("choice", "color"):
            text = str(value)
        else:
            continue
        parts.append(_decl(f.css, text))
    if target == "outer":
        shadow = SHADOW_CSS.get(style.get("shadow", ""))
        if shadow:
            parts.append(_decl("box-shadow", shadow))
        bg = _background(style, media_urls)
        if bg:
            parts.append(bg)
        if style.get("border_width") and "border_style" not in style:
            parts.append("border-style:solid")
    return ";".join(p for p in parts if p)


class Sheet:
    """Collects rules per device and prints them desktop → tablet → mobile."""

    def __init__(self, preview: bool = False):
        self.rules = {d: [] for d in DEVICES}
        self.hidden = {d: [] for d in DEVICES}
        self.preview = preview  # in the editor a hidden node stays visible (faded) so it can still be selected

    def node(self, node_id: str, style_by_device: dict, index: dict, media_urls: dict, *, extra=None):
        selector = sel(node_id)
        for device in DEVICES:
            style = (style_by_device or {}).get(device) or {}
            for target, suffix in (("outer", ""), ("core", " .st-core")):
                body = declarations(style, index, media_urls, target)
                if target == "outer" and extra and device in extra:
                    body = ";".join(x for x in (body, extra[device]) if x)
                if body:
                    self.rules[device].append(f"{selector}{suffix}{{{body}}}")
            if style.get("hidden") is True:
                self.hidden[device].append(f"{selector}{{opacity:.35}}" if self.preview else f"{selector}{{display:none!important}}")

    def raw(self, device: str, rule: str):
        self.rules[device].append(rule)

    def text(self) -> str:
        out = list(self.rules["desktop"])
        for device in ("tablet", "mobile"):
            if self.rules[device]:
                out.append(MEDIA[device] + "".join(self.rules[device]) + "}")
        for device in DEVICES:
            if self.hidden[device]:
                out.append(HIDDEN_MEDIA[device] + "".join(self.hidden[device]) + "}")
        return "".join(out)


_RULE_RE = re.compile(r"(?P<sel>[^{}]+)\{(?P<body>[^{}]*)\}")


def assert_safe_css(css: str) -> str:
    """Raises ``ValueError`` unless ``css`` is made only of simple rules (optionally inside @media) with allowlisted characters."""
    if _FORBIDDEN.search(css):
        raise ValueError("forbidden token in generated CSS")
    depth = 0
    for ch in css:
        depth += (ch == "{") - (ch == "}")
        if depth < 0 or depth > 2:
            raise ValueError("unbalanced CSS")
    if depth:
        raise ValueError("unbalanced CSS")
    flat = re.sub(r"@media[^{]*\{", "", css)
    flat = re.sub(r"\}\}", "}", flat) if "@media" in css else flat
    for m in _RULE_RE.finditer(flat):
        for decl in m.group("body").split(";"):
            if not decl.strip():
                continue
            prop, _, value = decl.partition(":")
            if not _PROP_OK.match(prop.strip()) or not _VALUE_OK.match(value.strip()):
                raise ValueError(f"unsafe declaration: {prop.strip()}")
    return css


_PLUGIN_SEL = re.compile(r"^[.#a-zA-Z0-9_\-\s>+~:,*]+$")


def scope_plugin_css(css: str, scope_class: str) -> str:
    """CSS shipped by a plugin widget: simple rules only; every selector is forced under ``.<scope_class>``; no url(), @import or tricks."""
    out = []
    for m in _RULE_RE.finditer(css or ""):
        selectors = [s.strip() for s in m.group("sel").split(",") if s.strip()]
        if not selectors or not all(_PLUGIN_SEL.match(s) for s in selectors):
            continue
        decls = []
        for decl in m.group("body").split(";"):
            prop, _, value = decl.partition(":")
            prop, value = prop.strip(), value.strip()
            if prop and _PROP_OK.match(prop) and value and _VALUE_OK.match(value) and not _FORBIDDEN.search(value) and "url(" not in value.lower():
                decls.append(f"{prop}:{value}")
        if decls:
            out.append(",".join(f".{scope_class} {s}" for s in selectors) + "{" + ";".join(decls) + "}")
    return "".join(out)
