"""The tools an AI assistant can call. This list is the whole of what Ravix MCP can do; a test locks the names.

There is deliberately no tool to install or remove plugins or themes, to touch users, roles, security settings, keys, environment or email, to
run a command, or to read anything from Ravix Shop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from django.utils.translation import gettext_lazy as _lazy

from . import scopes as sc


class ToolError(Exception):
    """A problem the model can understand and fix; it is returned as a tool result with isError, not as a protocol error."""


@dataclass
class Context:
    request: object
    user: object
    connection: object
    scopes: list


@dataclass(frozen=True)
class Tool:
    name: str
    title: object
    description: object
    scopes: tuple
    handler: Callable
    input_schema: dict = field(default_factory=lambda: {"type": "object", "properties": {}, "additionalProperties": False})
    read_only: bool = True
    destructive: bool = False
    idempotent: bool = True
    open_world: bool = False
    writes: bool = False

    def definition(self) -> dict:
        return {
            "name": self.name,
            "title": str(self.title),
            "description": str(self.description),
            "inputSchema": self.input_schema,
            "annotations": {"readOnlyHint": self.read_only, "destructiveHint": self.destructive, "idempotentHint": self.idempotent, "openWorldHint": self.open_world},
        }


_REGISTRY: list[Tool] = []


def register(tool: Tool) -> Tool:
    _REGISTRY.append(tool)
    return tool


def all_tools() -> list[Tool]:
    return list(_REGISTRY)  # registration order = list order, so the list is stable between calls


def visible_tools(scopes) -> list[Tool]:
    have = set(scopes)
    return [t for t in _REGISTRY if set(t.scopes) <= have]


def get(name: str) -> Tool | None:
    return next((t for t in _REGISTRY if t.name == name), None)


# --------------------------------------------------------------------------------------------------------------- get_site_status
def _site_status(ctx: Context) -> dict:
    from apps.core import sitehealth
    from apps.core.models import SiteSettings
    from apps.core.version import RAVIX_VERSION

    checks = sitehealth.run_all(ctx.request)
    summary = sitehealth.summarize(checks)
    site = SiteSettings.objects.filter(pk=1).first()
    # Only the name of each check and its level: never the paths, addresses, versions of server software or fix commands in the detail text.
    return {
        "site_name": site.site_name if site else "",
        "ravix_version": RAVIX_VERSION,
        "verdict": str(summary["verdict"]),
        "score": summary["score"],
        "counts": summary["counts"],
        "checks": [{"group": str(c.group), "title": str(c.title), "status": c.status} for c in checks],
    }


register(Tool(
    name="get_site_status",
    title=_lazy("Site status"),
    description=_lazy(
        "Read-only summary of how healthy this Ravix site is: an overall verdict and score, how many checks are good, recommended or critical, "
        "and the name and level of each check. It contains no server paths, addresses or secrets. It changes nothing."),
    scopes=(sc.SITE_READ,),
    handler=_site_status,
))
