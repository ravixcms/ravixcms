"""Pages and posts whose address (slug) is a language code.

``/de/`` is the German home page since the public site lives under a language prefix, so a static page called ``de`` can
no longer be reached at its old address (and ``en``, ``fa`` … are reserved for pages). Sites upgraded from an older
version may still have such slugs. :func:`fix` renames them (``de`` → ``de-page``), creates a 301 redirect from the old
address, and writes an audit-log entry for every change. The same function runs from the data migration
(``0007_fix_language_slugs``), from ``manage.py ravix_fix_slugs`` and is what the health page / preflight describe.

It takes the model classes as arguments so the migration can pass its historical models.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db.models import Q
from django.utils.translation import gettext

from apps.core.utils import RESERVED_PAGE_SLUGS, unique_slug


def language_codes() -> list[str]:
    return [code for code, _name in settings.LANGUAGES]


@dataclass
class Change:
    kind: str  # "page" | "post"
    pk: int
    title: str
    old_slug: str
    new_slug: str
    old_path: str
    new_path: str


def _lookup(codes):
    query = Q()
    for code in codes:
        query |= Q(slug__iexact=code)
    return query


def find(page_model, post_model) -> list:
    """[(kind, object)] for every page/post whose slug is a language code (case-insensitive)."""
    query = _lookup(language_codes())
    found = [("page", obj) for obj in page_model.objects.filter(query).order_by("pk")]
    found += [("post", obj) for obj in post_model.objects.filter(query).order_by("pk")]
    return found


def count(page_model, post_model) -> tuple[int, int]:
    query = _lookup(language_codes())
    return page_model.objects.filter(query).count(), post_model.objects.filter(query).count()


def fix(page_model, post_model, redirect_model, audit_model, *, dry_run: bool = False) -> list[Change]:
    changes: list[Change] = []
    for kind, obj in find(page_model, post_model):
        model = page_model if kind == "page" else post_model
        suffix = "page" if kind == "page" else "post"
        old = obj.slug
        new = unique_slug(model, f"{old.lower()}-{suffix}", instance=obj, fallback=suffix, max_length=190, reserved=RESERVED_PAGE_SLUGS)
        old_path = f"/{old}/" if kind == "page" else f"/blog/{old}/"
        new_path = f"/{new}/" if kind == "page" else f"/blog/{new}/"
        changes.append(Change(kind, obj.pk, str(obj.title)[:200], old, new, old_path, new_path))
        if dry_run:
            continue
        model.objects.filter(pk=obj.pk).update(slug=new)  # not save(): no body re-render, no signals, nothing else changes
        redirect_model.objects.filter(old_path=new_path).delete()
        redirect_model.objects.filter(new_path=old_path).update(new_path=new_path)  # no redirect chains
        redirect_model.objects.update_or_create(
            old_path=old_path,
            defaults={"new_path": new_path, "status_code": 301, "is_auto": True,
                      "note": gettext("Automatic: the address was a language code")[:200]},
        )
        audit_model.objects.create(
            action="update", object_type=str(model._meta.verbose_name)[:60], object_id=str(obj.pk), object_repr=str(obj.title)[:200],
            message=(gettext("Address changed automatically because “%(slug)s” is a language prefix: %(old)s → %(new)s")
                     % {"slug": old, "old": old_path, "new": new_path})[:400])
    return changes
