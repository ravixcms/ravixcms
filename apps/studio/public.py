"""
How the public site gets a Studio page. This module never touches the editor: a published page is stored HTML, so it keeps showing when
Ravix Studio is switched off (module off or ``RAVIX_STUDIO_ENABLED=false``).
"""
import logging
from dataclasses import dataclass

from django.conf import settings
from django.templatetags.static import static
from django.urls import reverse
from django.utils.safestring import mark_safe

from .models import StudioDocument

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StudioBody:
    html: str
    show_title: bool
    template: str  # "full_width" | "default"
    frame_hosts: tuple


def _css_part(version) -> str:
    css = version.css or ""
    if "</" in css or "<" in css:
        return ""
    if len(css.encode("utf-8")) > int(getattr(settings, "STUDIO_INLINE_CSS_MAX", 50_000)) and version.css_hash:
        return f'<link rel="stylesheet" href="{reverse("studio_public:css", args=[version.css_hash])}">'
    return f"<style>{css}</style>"


def assemble(version) -> str:
    script = f'<script src="{static("studio/front.js")}" defer></script>' if "video" in (version.scripts or []) else ""
    # The HTML was sanitised (nh3) and the CSS built by the server when the version was rendered.
    return mark_safe(_css_part(version) + version.html + script)  # nosec B308 B703


def _lazy_rebuild(version) -> None:
    """A published version without its stored page (just imported, or a new renderer): render it now, once."""
    from . import render as renderer, services

    if version.html and version.renderer_version == renderer.RENDERER_VERSION:
        return
    from apps.core import modules

    if version.html and not modules.is_enabled("studio"):
        return  # module off: keep showing what is stored
    try:
        services.rebuild_version(version)
    except Exception:  # noqa: BLE001 — never break a public page; the stored page (or the text content) shows instead
        logger.exception("Ravix Studio could not rebuild a page lazily")


def published_body(target) -> StudioBody | None:
    """The live Studio layout of a page or post, or None (the classic text content is shown)."""
    field = "page" if target.__class__.__name__ == "Page" else "post"
    doc = StudioDocument.objects.filter(**{field: target, "published_version__isnull": False}).select_related("published_version").first()
    if doc is None:
        return None
    version = doc.published_version
    _lazy_rebuild(version)
    if not version.html:
        return None
    return StudioBody(assemble(version), bool(version.show_title), version.template, tuple(version.frame_hosts or ()))
