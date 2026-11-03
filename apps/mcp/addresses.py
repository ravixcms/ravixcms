"""The public addresses of the MCP endpoint and of the authorisation server, built the same way everywhere.

The issuer is the site's canonical address (``RAVIX_SITE_URL``) when set, otherwise the address of the request (which behind a trusted proxy is
already the public one). The resource is always ``<issuer>/mcp/``; ``/mcp`` without the slash names the same resource.
"""
from __future__ import annotations

from django.conf import settings
from django.urls import reverse


def issuer(request) -> str:
    base = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
    if base:
        return base
    return request.build_absolute_uri("/").rstrip("/") if request is not None else ""


def absolute(request, name: str, **kwargs) -> str:
    """Absolute address of a named URL (``reverse`` already includes any sub-folder the site is installed in)."""
    base = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
    path = reverse(name, kwargs=kwargs or None)
    if base:
        # SITE_URL may already contain the sub-folder; reverse() contains it too. Keep one copy.
        from urllib.parse import urlsplit

        folder = urlsplit(base).path.rstrip("/")
        if folder and path.startswith(folder + "/"):
            path = path[len(folder):]
        return base + path
    return request.build_absolute_uri(path)


def resource(request) -> str:
    return absolute(request, "mcp:endpoint")


def same_resource(a: str, b: str) -> bool:
    return (a or "").rstrip("/") == (b or "").rstrip("/") and bool(a)
