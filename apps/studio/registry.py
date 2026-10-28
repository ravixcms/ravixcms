"""The widget registry: one ``WidgetDef`` per widget type is the single source of truth for validation, the editor form and rendering."""
import re
from dataclasses import dataclass, field
from typing import Callable

from .fields import BOX_STYLE, Field

_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}(\.[a-z][a-z0-9_]{1,30})?$")

BOX_INDEX = {f.name: f for f in BOX_STYLE}


@dataclass
class WidgetDef:
    type: str
    title: object
    icon: str  # an icon name from icons.json
    category: str  # basic | media | content | layout | plugin
    fields: tuple = ()  # content settings (not responsive)
    style_fields: tuple = ()  # extra responsive style settings besides the common box ones
    render: Callable | None = None  # (ctx, node, props) -> HTML string (already safe)
    css: str = ""  # static CSS of this widget type (included only on pages that use it)
    admin_only: bool = False  # needs the studio.html capability
    container: bool = False  # holds columns (inner section)
    plugin: str = ""  # plugin slug for plugin widgets
    description: object = ""
    default_style: dict = field(default_factory=dict)  # style a new widget starts with: {"desktop": {...}}
    script: str = ""  # a front-end script this widget needs ("video")
    hidden_style: tuple = ()  # common style names this widget does not offer

    @property
    def style_index(self) -> dict:
        idx = {n: f for n, f in BOX_INDEX.items() if n not in self.hidden_style}
        idx.update({f.name: f for f in self.style_fields})
        return idx

    @property
    def field_index(self) -> dict:
        return {f.name: f for f in self.fields}


_widgets: dict[str, WidgetDef] = {}


def register_widget(widget: WidgetDef) -> WidgetDef:
    if not _TYPE_RE.match(widget.type):
        raise ValueError(f"bad widget type: {widget.type!r}")
    if widget.plugin and not widget.type.startswith(widget.plugin + "."):
        raise ValueError("plugin widget types must start with '<plugin slug>.'")
    if not widget.plugin and "." in widget.type:
        raise ValueError("only plugin widgets may contain a dot")
    _widgets[widget.type] = widget
    return widget


def register_studio_widget(type: str, *, title, icon="puzzle", fields=(), style_fields=(), render, css="", plugin: str = "", **extra) -> WidgetDef:  # noqa: A002
    """Public API for widgets shipped by Ravix modules and (through ``plugin.json``) by plugins.

    ``render(props, ctx) -> str`` returns HTML; Ravix cleans it with the same allowlist as the custom HTML widget. ``css`` is plain CSS of simple
    rules; every selector is forced under the widget's own class and ``url()`` is not allowed.
    """
    from .css import scope_plugin_css
    from .render import plugin_render_adapter

    scope = "st-w-" + type.replace(".", "-").replace("_", "-")
    return register_widget(WidgetDef(
        type=type, title=title, icon=icon if icon else "layers", category="plugin" if plugin else extra.pop("category", "content"),
        fields=tuple(fields), style_fields=tuple(style_fields), render=plugin_render_adapter(render),
        css=scope_plugin_css(css, scope), plugin=plugin, **extra,
    ))


def unregister_plugin_widgets(slug: str) -> None:
    for key in [k for k, w in _widgets.items() if w.plugin == slug]:
        del _widgets[key]


def get_widget(type_: str) -> WidgetDef | None:
    from . import plugin_widgets

    plugin_widgets.sync()
    return _widgets.get(type_)


def all_widgets() -> list[WidgetDef]:
    from . import plugin_widgets

    plugin_widgets.sync()
    return list(_widgets.values())
