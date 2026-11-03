"""The scopes a connected app can be granted, and what each one needs from the person who grants it.

A token is only ever as strong as the *person* behind it: the scopes it carries are intersected, on every single call, with
(1) what the administrator allowed that person's role to grant and (2) what that person's role can do right now. Demote or
deactivate somebody and their connected apps lose the power at once.
"""
from __future__ import annotations

from django.utils.translation import gettext_lazy

SITE_READ = "site:read"
CONTENT_READ = "content:read"
CONTENT_WRITE = "content:write"
CONTENT_PUBLISH = "content:publish"
MEDIA_WRITE = "media:write"
SEO_WRITE = "seo:write"
MENUS_WRITE = "menus:write"
STUDIO_WRITE = "studio:write"
OFFLINE_ACCESS = "offline_access"

# Stable order: it is the order shown on the consent page and advertised in the metadata.
SCOPES = (SITE_READ, CONTENT_READ, CONTENT_WRITE, CONTENT_PUBLISH, MEDIA_WRITE, SEO_WRITE, MENUS_WRITE, STUDIO_WRITE, OFFLINE_ACCESS)

LABELS = {
    SITE_READ: gettext_lazy("See whether the site is healthy (a short summary, no server details)"),
    CONTENT_READ: gettext_lazy("Read posts, pages, categories, tags, media and menus"),
    CONTENT_WRITE: gettext_lazy("Create and edit drafts of posts and pages (nothing goes live)"),
    CONTENT_PUBLISH: gettext_lazy("Publish, unpublish and move posts and pages to the trash — this changes the live site"),
    MEDIA_WRITE: gettext_lazy("Upload images and files, and set their descriptions"),
    SEO_WRITE: gettext_lazy("Edit search-engine titles and descriptions"),
    MENUS_WRITE: gettext_lazy("Edit the site menus"),
    STUDIO_WRITE: gettext_lazy("Edit page layouts made with Ravix Studio (drafts only)"),
    OFFLINE_ACCESS: gettext_lazy("Stay connected without asking you to sign in again"),
}

# A scope is usable only if the person's role has AT LEAST ONE of these capabilities. offline_access needs none.
NEEDS = {
    SITE_READ: ("dashboard.view",),
    CONTENT_READ: ("content.write", "pages.manage"),
    CONTENT_WRITE: ("content.write", "pages.manage"),
    CONTENT_PUBLISH: ("content.publish",),
    MEDIA_WRITE: ("media.upload",),
    SEO_WRITE: ("content.write", "pages.manage"),
    MENUS_WRITE: ("themes.manage",),
    STUDIO_WRITE: ("studio.edit",),
    OFFLINE_ACCESS: (),
}

# What each role may grant until the superadmin changes it. A superadmin can always grant everything their role can do.
DEFAULT_ROLE_SCOPES = {
    "admin": tuple(s for s in SCOPES if s != CONTENT_PUBLISH),
    "author": (),
}


def parse(text: str | None) -> list[str]:
    """A space-separated scope string → the known scopes in canonical order, without duplicates."""
    wanted = set((text or "").split())
    return [s for s in SCOPES if s in wanted]


def join(scopes) -> str:
    return " ".join(s for s in SCOPES if s in set(scopes))


def role_allows(user, scope: str) -> bool:
    """Whether the person's CURRENT role has the capability behind the scope."""
    if not getattr(user, "is_active", False):
        return False
    needs = NEEDS.get(scope)
    if needs is None:
        return False
    return not needs or any(user.has_cap(cap) for cap in needs)


def grantable(user, settings_obj=None) -> list[str]:
    """Scopes this person may hand to an app: allowed for their role by the administrator AND backed by their capabilities."""
    from .models import McpSettings

    cfg = settings_obj or McpSettings.load()
    allowed = cfg.scopes_for_role(user.role)
    return [s for s in SCOPES if s in allowed and role_allows(user, s)]


def effective(user, granted, settings_obj=None) -> list[str]:
    """What an access token may do right now: granted ∩ grantable (recomputed on every call)."""
    have = set(grantable(user, settings_obj))
    return [s for s in SCOPES if s in set(granted) and s in have]
