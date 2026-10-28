"""Widgets declared by plugins (``studio_widgets`` in ``plugin.json``): added while the plugin is active, removed when it is not.

Pages already published keep their stored HTML either way; only the palette (and the editing of such a widget) follows the plugin's state.
"""
import logging


logger = logging.getLogger(__name__)
_signature: tuple | None = None


def _field(spec: dict):
    from .fields import Field

    kwargs = {k: v for k, v in spec.items() if k in {"min", "max", "max_len", "required", "unit", "media_kind"}}
    choices = spec.get("choices") or ()
    default = spec.get("default", 0 if spec["kind"] == "int" else False if spec["kind"] == "bool" else "")
    return Field(name=spec["name"], kind=spec["kind"], label=spec.get("label") or spec["name"], default=default, help=spec.get("help", ""),
                 choices=tuple((str(c[0]), str(c[1])) for c in choices if isinstance(c, (list, tuple)) and len(c) == 2), **kwargs)


def sync() -> None:
    """Make the registry match the plugins that are active right now (cheap when nothing changed)."""
    global _signature
    from apps.plugins import registry as plugin_registry
    from apps.plugins import state

    from . import registry

    try:
        active = state.active_plugins()
    except Exception:  # noqa: BLE001 — before migrate / without a database the palette simply has no plugin widgets
        return
    signature = tuple(sorted((slug, info.fingerprint) for slug, info in active.items() if info.manifest.studio_widgets))
    if signature == _signature:
        return
    for slug in {w.plugin for w in registry._widgets.values() if w.plugin}:
        registry.unregister_plugin_widgets(slug)
    for slug, info in active.items():
        if not info.manifest.studio_widgets:
            continue
        loaded = plugin_registry.get(slug)
        if loaded is None:
            continue
        for spec in info.manifest.studio_widgets:
            fn = loaded.studio_widgets.get(spec["type"])
            if fn is None:
                continue
            try:
                registry.register_studio_widget(f'{slug}.{spec["type"]}', title=spec["title"], icon=spec["icon"], plugin=slug,
                                                fields=[_field(f) for f in spec["fields"]], render=fn, css=spec["css"])
            except Exception:  # noqa: BLE001 — one bad widget must not hide the others
                logger.exception("Ravix Studio: widget %s of plugin %s was not registered", spec["type"], slug)
    _signature = signature
