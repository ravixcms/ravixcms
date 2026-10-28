"""
The Ravix Studio document: a versioned JSON tree ``section → column → widget`` (widgets such as ``inner_section`` hold columns again).

    {"schema_version": 2, "meta": {"template": "full_width", "show_title": true},
     "sections": [{"id": "n1a2b3c4", "type": "section", "props": {...},
                   "style": {"desktop": {...}, "tablet": {...}, "mobile": {...}},
                   "columns": [{"id": ..., "type": "column", "props": {...}, "style": {...},
                                "widgets": [{"id": ..., "type": "heading", "props": {...}, "style": {...}}]}]}]}

* ``props`` hold the content and are the same on every screen size; ``style`` holds the responsive look and only stores values that were set
  (an unset value inherits from the larger screen).
* Old documents are upgraded on read by the migration chain in ``MIGRATIONS``; a newer or unknown version is an error, never a guess.
* Nothing free-form is accepted: see ``fields.py``.
"""
import hashlib
import json
import re
import secrets

from django.utils.translation import gettext, gettext_lazy

from .css import DEVICES
from .fields import (
    BOX_STYLE, Collector, DocumentInvalid, Field, I, field_defaults, icon_names, validate_value,
)
from .registry import BOX_INDEX, WidgetDef, get_widget

SCHEMA_VERSION = 2

LIMITS = {
    "sections": 40,
    "columns_per_section": 6,
    "widgets_per_column": 40,
    "total_nodes": 1500,
    "max_depth": 3,  # section = 1, inner section = 2, inner of inner = 3
    "max_json_bytes": 600_000,
}

_ID_RE = re.compile(r"^[a-z0-9]{4,16}$")
TEMPLATES = (("default", gettext_lazy("Theme default")), ("full_width", gettext_lazy("Full width (no sidebar)")))

META_FIELDS = [
    Field("template", "choice", gettext_lazy("Page template"), "full_width", "content", choices=TEMPLATES),
    Field("show_title", "bool", gettext_lazy("Show the page title above the content"), True, "content"),
]

SECTION_PROPS = [
    Field("content_width", "choice", gettext_lazy("Content width"), "boxed", "layout",
          choices=(("boxed", gettext_lazy("Contained (centered)")), ("full", gettext_lazy("Full width")))),
    I("max_width", gettext_lazy("Maximum content width (px)"), 1140, 320, 1920, group="layout"),
    I("gap", gettext_lazy("Gap between columns (px)"), 24, 0, 120, group="layout"),
    Field("valign", "choice", gettext_lazy("Vertical alignment of columns"), "start", "layout",
          choices=(("start", gettext_lazy("Top")), ("center", gettext_lazy("Center")), ("end", gettext_lazy("Bottom")), ("stretch", gettext_lazy("Stretch")))),
    Field("anchor", "anchor", gettext_lazy("Anchor ID (for in-page links)"), "", "layout", max_len=40,
          help=gettext_lazy("Lowercase letters, digits and hyphens. Link to it with #your-id.")),
]
COLUMN_PROPS = [
    Field("valign", "choice", gettext_lazy("Vertical alignment in the row"), "", "layout",
          choices=(("", gettext_lazy("Inherit")), ("start", gettext_lazy("Top")), ("center", gettext_lazy("Center")), ("end", gettext_lazy("Bottom")), ("stretch", gettext_lazy("Stretch")))),
]
COLUMN_STYLE = [I("span", gettext_lazy("Column width (out of 12)"), 12, 1, 12, unit="", group="layout")]
SECTION_PROP_INDEX = {f.name: f for f in SECTION_PROPS}
COLUMN_PROP_INDEX = {f.name: f for f in COLUMN_PROPS}
SECTION_STYLE_INDEX = dict(BOX_INDEX)
COLUMN_STYLE_INDEX = {**BOX_INDEX, **{f.name: f for f in COLUMN_STYLE}}
META_INDEX = {f.name: f for f in META_FIELDS}


def new_id() -> str:
    return "n" + secrets.token_hex(4)


def derived_id(parent: str, tag: str) -> str:
    return "n" + hashlib.sha1(f"{parent}:{tag}".encode(), usedforsecurity=False).hexdigest()[:8]


def empty_document() -> dict:
    return {"schema_version": SCHEMA_VERSION, "meta": {f.name: f.default for f in META_FIELDS}, "sections": []}


# ----------------------------------------------------------------------------------------------------------- migrations
from .legacy_import import v1_to_v2  # noqa: E402

MIGRATIONS = {1: v1_to_v2}  # {from_version: function(doc) -> doc of the next version}


def migrate_document(raw):
    """Upgrade a stored document to the current schema. Unknown or newer versions are an error."""
    if not isinstance(raw, dict):
        raise DocumentInvalid([("$", gettext("The document must be an object."))])
    version = raw.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise DocumentInvalid([("$.schema_version", gettext("The document schema version is invalid."))])
    if version > SCHEMA_VERSION or version < 1:
        raise DocumentInvalid([("$.schema_version", gettext("Schema version %(version)s is not supported.") % {"version": version})])
    while version < SCHEMA_VERSION:
        raw = MIGRATIONS[version](raw)
        version = raw["schema_version"]
    return raw


# ----------------------------------------------------------------------------------------------------------- validation
def _check_keys(node, allowed: set, path: str, errs: Collector) -> bool:
    if not isinstance(node, dict):
        errs.add(path, gettext("Must be an object."))
        return False
    for key in node:
        if key not in allowed:
            errs.add(f"{path}.{key}", gettext("Unknown key."))
    return True


def _node_id(node: dict, path: str, seen: set, errs: Collector) -> str:
    nid = node.get("id")
    if not isinstance(nid, str) or not _ID_RE.match(nid):
        errs.add(f"{path}.id", gettext("The node ID is invalid."))
        return new_id()
    if nid in seen:
        errs.add(f"{path}.id", gettext("The node ID is duplicated."))
    seen.add(nid)
    return nid


def _list(node: dict, key: str, limit: int, path: str, errs: Collector, label) -> list:
    items = node.get(key, [])
    if not isinstance(items, list):
        errs.add(f"{path}.{key}", gettext("Must be a list."))
        return []
    if len(items) > limit:
        errs.add(f"{path}.{key}", gettext("At most %(limit)s %(label)s are allowed.") % {"limit": limit, "label": label})
        return []
    return items


def _props(index: dict, raw, path: str, errs: Collector) -> dict:
    if not isinstance(raw, dict):
        errs.add(path, gettext("Properties must be an object."))
        return field_defaults(index.values())
    for key in raw:
        if key not in index:
            errs.add(f"{path}.{key}", gettext("This property is not allowed."))
    return {name: (validate_value(f, raw[name], f"{path}.{name}", errs) if name in raw else ([] if f.kind == "list" else f.default))
            for name, f in index.items()}


def _style(index: dict, raw, path: str, errs: Collector) -> dict:
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        errs.add(path, gettext("Must be an object."))
        return {}
    out = {}
    for device, values in raw.items():
        if device not in DEVICES:
            errs.add(f"{path}.{device}", gettext("Unknown key."))
            continue
        if not isinstance(values, dict):
            errs.add(f"{path}.{device}", gettext("Must be an object."))
            continue
        clean = {}
        for key, value in values.items():
            f = index.get(key)
            if f is None:
                errs.add(f"{path}.{device}.{key}", gettext("This property is not allowed."))
                continue
            if value is None or value == "":
                continue  # unset: inherit
            before = len(errs.errors)
            value = validate_value(f, value, f"{path}.{device}.{key}", errs)
            if len(errs.errors) == before and value not in (None, ""):
                clean[key] = value
        if clean:
            out[device] = clean
    return out


class _Walker:
    def __init__(self, errs: Collector):
        self.errs = errs
        self.seen: set = set()
        self.count = 0

    def column(self, col, path: str, depth: int) -> dict:
        errs = self.errs
        self.count += 1
        out = {"id": _node_id(col, path, self.seen, errs), "type": "column",
               "props": _props(COLUMN_PROP_INDEX, col.get("props", {}), f"{path}.props", errs),
               "style": _style(COLUMN_STYLE_INDEX, col.get("style", {}), f"{path}.style", errs), "widgets": []}
        for wi, w in enumerate(_list(col, "widgets", LIMITS["widgets_per_column"], path, errs, gettext("Widget"))):
            if errs.full:
                break
            node = self.widget(w, f"{path}.widgets[{wi}]", depth)
            if node is not None:
                out["widgets"].append(node)
        return out

    def widget(self, w, path: str, depth: int):
        errs = self.errs
        if not isinstance(w, dict):
            errs.add(path, gettext("Must be an object."))
            return None
        wtype = w.get("type")
        spec = get_widget(wtype) if isinstance(wtype, str) else None
        if spec is None:
            if isinstance(wtype, str) and "." in wtype:  # a plugin widget whose plugin is off: kept as is, rendered as nothing
                return self.unavailable(w, path)
            errs.add(f"{path}.type", gettext("The widget type is not allowed."))
            return None
        _check_keys(w, {"id", "type", "props", "style"} | ({"columns"} if spec.container else set()), path, errs)
        self.count += 1
        nid = _node_id(w, path, self.seen, errs)
        errs.current_id = nid
        if spec.admin_only and not errs.can_html and nid not in errs.existing_html:
            errs.add(path, gettext("Only administrators can add or change custom HTML."))
        node = {"id": nid, "type": spec.type, "props": _props(spec.field_index, w.get("props", {}), f"{path}.props", errs),
                "style": _style(spec.style_index, w.get("style", {}), f"{path}.style", errs)}
        if spec.container:
            if depth >= LIMITS["max_depth"]:
                errs.add(path, gettext("Sections can be nested at most %(n)s levels deep.") % {"n": LIMITS["max_depth"]})
                node["columns"] = []
            else:
                node["columns"] = self.columns(w, path, depth + 1)
        return node

    def unavailable(self, w, path: str) -> dict:
        errs = self.errs
        self.count += 1
        raw = json.dumps(w.get("props", {}), ensure_ascii=False)
        if len(raw) > 20_000:
            errs.add(f"{path}.props", gettext("The data size exceeds the limit."))
        nid = _node_id(w, path, self.seen, errs)
        props = w.get("props", {}) if isinstance(w.get("props", {}), dict) else {}
        return {"id": nid, "type": w["type"], "props": json.loads(raw) if isinstance(props, dict) else {}, "style": _style(BOX_INDEX, w.get("style", {}), f"{path}.style", errs)}

    def columns(self, node, path: str, depth: int) -> list:
        errs = self.errs
        out = []
        for ci, col in enumerate(_list(node, "columns", LIMITS["columns_per_section"], path, errs, gettext("Column"))):
            cp = f"{path}.columns[{ci}]"
            if not _check_keys(col, {"id", "type", "props", "style", "widgets"}, cp, errs):
                continue
            out.append(self.column(col, cp, depth))
        return out


def validate_document(raw, *, strict: bool = False, can_html: bool = True, existing_html: dict | None = None) -> dict:
    """Validate and normalise a document (from the browser, an import or the database). Raises ``DocumentInvalid``.

    ``strict`` (submitting / publishing) also requires every required field. ``can_html=False`` forbids adding or changing the custom
    HTML widget, except for the HTML already stored for that widget (``existing_html``).
    """
    raw = migrate_document(raw)
    errs = Collector(strict, can_html, existing_html)
    _check_keys(raw, {"schema_version", "meta", "sections"}, "$", errs)
    try:
        size = len(json.dumps(raw, ensure_ascii=False))
    except (TypeError, ValueError):
        raise DocumentInvalid([("$", gettext("Invalid JSON."))])
    if size > LIMITS["max_json_bytes"]:
        raise DocumentInvalid([("$", gettext("The data size exceeds the limit."))])

    meta_raw = raw.get("meta", {})
    meta = {f.name: f.default for f in META_FIELDS}
    if not isinstance(meta_raw, dict):
        errs.add("$.meta", gettext("Must be an object."))
    else:
        for key, value in meta_raw.items():
            if key not in META_INDEX:
                errs.add(f"$.meta.{key}", gettext("This property is not allowed."))
            else:
                meta[key] = validate_value(META_INDEX[key], value, f"$.meta.{key}", errs)

    walker = _Walker(errs)
    sections = []
    for si, sec in enumerate(_list(raw, "sections", LIMITS["sections"], "$", errs, gettext("Section"))):
        if errs.full:
            break
        sp = f"$.sections[{si}]"
        if not _check_keys(sec, {"id", "type", "props", "style", "columns"}, sp, errs):
            continue
        walker.count += 1
        out = {"id": _node_id(sec, sp, walker.seen, errs), "type": "section",
               "props": _props(SECTION_PROP_INDEX, sec.get("props", {}), f"{sp}.props", errs),
               "style": _style(SECTION_STYLE_INDEX, sec.get("style", {}), f"{sp}.style", errs),
               "columns": walker.columns(sec, sp, 1)}
        sections.append(out)
    if walker.count > LIMITS["total_nodes"]:
        errs.add("$", gettext("The total number of page components must not exceed %(LIMITS)s.") % {"LIMITS": LIMITS["total_nodes"]})
    if errs.errors:
        raise DocumentInvalid(errs.errors)
    return {"schema_version": SCHEMA_VERSION, "meta": meta, "sections": sections}


# ----------------------------------------------------------------------------------------------------------- traversal helpers
def iter_widgets(doc: dict):
    """Every widget of a validated document, inner sections included (depth-first, document order)."""
    def from_columns(columns):
        for col in columns:
            for w in col["widgets"]:
                yield w
                if "columns" in w:
                    yield from from_columns(w["columns"])

    for sec in doc["sections"]:
        yield from from_columns(sec["columns"])


def iter_nodes(doc: dict):
    """(kind, node) for sections, columns and widgets."""
    def from_columns(columns):
        for col in columns:
            yield "column", col
            for w in col["widgets"]:
                yield "widget", w
                if "columns" in w:
                    yield from from_columns(w["columns"])

    for sec in doc["sections"]:
        yield "section", sec
        yield from from_columns(sec["columns"])


def _media_in_props(spec, props):
    for f in spec.fields:
        if f.kind == "media" and props.get(f.name):
            yield props[f.name]
        elif f.kind == "list":
            for item in props.get(f.name, []):
                for sub in f.item_fields:
                    if sub.kind == "media" and item.get(sub.name):
                        yield item[sub.name]


def media_ids(doc: dict) -> set:
    """IDs of every media file the document refers to (props and background images)."""
    found = set()
    for kind, node in iter_nodes(doc):
        for values in node.get("style", {}).values():
            if values.get("bg_media"):
                found.add(values["bg_media"])
        if kind == "widget":
            spec = get_widget(node["type"])
            if spec is not None:
                found.update(_media_in_props(spec, node["props"]))
    return found


def html_by_id(doc: dict) -> dict:
    """{widget id: stored HTML} of the admin-only HTML fields (so an author can save a page that has an admin's HTML widget unchanged)."""
    out = {}
    try:
        for w in iter_widgets(doc):
            spec = get_widget(w["type"])
            if spec is not None and spec.admin_only:
                for f in spec.fields:
                    if f.kind == "html":
                        out[w["id"]] = w["props"].get(f.name, "")
    except (KeyError, TypeError):
        return {}
    return out


def has_admin_widgets(doc: dict) -> bool:
    return any((get_widget(w["type"]) is not None and get_widget(w["type"]).admin_only) for w in iter_widgets(doc))


def client_specs() -> dict:
    from .registry import all_widgets

    widgets = []
    for w in all_widgets():
        widgets.append({
            "type": w.type, "title": str(w.title), "icon": w.icon, "category": w.category, "container": w.container,
            "admin_only": w.admin_only, "description": str(w.description),
            "fields": [f.to_client() for f in w.fields], "style_fields": [f.to_client() for f in w.style_index.values()],
            "default_style": w.default_style, "defaults": field_defaults(w.fields),
        })
    return {
        "schema_version": SCHEMA_VERSION, "limits": LIMITS, "devices": list(DEVICES),
        "meta": [f.to_client() for f in META_FIELDS],
        "section": {"fields": [f.to_client() for f in SECTION_PROPS], "style_fields": [f.to_client() for f in BOX_STYLE]},
        "column": {"fields": [f.to_client() for f in COLUMN_PROPS], "style_fields": [f.to_client() for f in [*COLUMN_STYLE, *BOX_STYLE]]},
        "widgets": widgets, "icons": list(icon_names()),
    }
