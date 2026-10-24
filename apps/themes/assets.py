"""Static files of themes installed from the panel.

A theme uploaded from the panel lives in ``RAVIX_THEME_INSTALL_DIR``, not in the project, so it is not part of the
``collectstatic`` manifest (``collectstatic`` is never run from a web request, and ``STATICFILES_DIRS`` is read once at start-up). Instead
of rewriting ``STATIC_ROOT`` at run time, its ``static/`` folder is served read-only by :func:`serve` at
``/theme-assets/<slug>/<path>``. It works immediately after install, in every worker, needs no restart and no manifest, and
disappears with the theme. Themes that ship with the project keep using the normal static pipeline.
"""
from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseNotModified
from django.templatetags.static import static
from django.urls import reverse
from django.utils.http import http_date
from django.views.decorators.http import require_safe
from django.views.static import serve as django_serve

from . import registry

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,48}$")
PATH_RE = re.compile(r"^[A-Za-z0-9._\-/]+$")
VERSION_RE = re.compile(r"^[0-9A-Za-z.+\-]{1,45}$")
# the theme validator already refuses these inside static/; refused again here because this is the last line before the browser
BLOCKED_SUFFIXES = {".html", ".htm", ".xhtml", ".svg", ".xml", ".php", ".py", ".pyc", ".phtml", ".cgi", ".sh", ".exe"}


def is_web_installed(path: Path) -> bool:
    """True when the theme folder sits directly in the panel-install folder."""
    try:
        return Path(path).resolve().parent == Path(settings.RAVIX_THEME_INSTALL_DIR).resolve()
    except OSError:
        return False


def theme_static_url(slug: str, rel: str, version: str = "") -> str:
    """URL of ``static/<rel>`` of theme ``slug``: the asset route for panel-installed themes, the static pipeline otherwise."""
    info = registry.get_theme(slug)
    if info is not None and is_web_installed(info.path):
        url = reverse("theme_assets", kwargs={"slug": slug, "path": rel})
    else:
        url = static(f"themes/{slug}/{rel}")
    return f"{url}?v={version}" if VERSION_RE.match(version or "") else url


def _theme_static_root(slug: str) -> Path:
    if not SLUG_RE.match(slug):
        raise Http404
    info = registry.get_theme(slug)
    if info is None or not info.valid or not is_web_installed(info.path):
        raise Http404
    root = info.path / "static"
    if root.is_symlink() or not root.is_dir():
        raise Http404
    return root


@require_safe
def serve(request, slug, path):
    if not PATH_RE.match(path) or ".." in path.split("/") or path.startswith("/") or any(p.startswith(".") for p in path.split("/")):
        raise Http404
    if Path(path).suffix.lower() in BLOCKED_SUFFIXES:
        raise Http404
    root = _theme_static_root(slug)
    target = root / path
    # no symlink anywhere on the way (the installer refuses them; checked again because the folder is writable by the service user)
    probe = target
    while probe != root:
        if probe.is_symlink():
            raise Http404
        probe = probe.parent
    response = django_serve(request, path, document_root=str(root))
    if isinstance(response, HttpResponseNotModified):
        return response
    guessed = mimetypes.guess_type(path)[0]
    if guessed:
        response["Content-Type"] = guessed
    response["X-Content-Type-Options"] = "nosniff"
    versioned = VERSION_RE.match(request.GET.get("v", "") or "") is not None
    response["Cache-Control"] = "public, max-age=31536000, immutable" if versioned else "public, max-age=3600"
    return response
