"""Translation groups: posts (or pages) that are the same text in different languages.

A group is a UUID shared by the members; a language appears at most once per group (also a database constraint). Posts only group with posts and
pages only with pages. The feature is off while ``RAVIX_MULTISITE_ENABLED`` is on: content is not separated per site in that mode, so a group could
tie together texts that belong to different sites.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext

from .models import Page, Post, PublishStatus


class TranslationError(ValueError):
    """A grouping that is not allowed; the message is shown to the user."""


def enabled() -> bool:
    return not bool(getattr(settings, "RAVIX_MULTISITE_ENABLED", False))


def language_name(code: str) -> str:
    return dict(settings.LANGUAGES).get(code, code)


def members(obj):
    """Every member of the object's group (the object itself included), ordered by language; just the object when it has no group."""
    model = type(obj)
    if not obj.translation_group:
        return [obj]
    order = {code: i for i, (code, _n) in enumerate(settings.LANGUAGES)}
    return sorted(model.objects.filter(translation_group=obj.translation_group), key=lambda o: (order.get(o.language, 99), o.pk))


def others(obj):
    return [m for m in members(obj) if m.pk != obj.pk]


def free_languages(obj) -> list[tuple[str, str]]:
    """Languages of the site that no member of the object's group uses yet (what "create a translation" can offer)."""
    used = {m.language for m in members(obj)}
    return [(code, name) for code, name in settings.LANGUAGES if code not in used]


@transaction.atomic
def link(objs) -> uuid.UUID:
    """Put the objects into one group. They must be of one model, have distinct languages, and not belong to two different groups."""
    objs = list(objs)
    if not enabled():
        raise TranslationError(gettext("Translations are not available while several sites are enabled."))
    if len(objs) < 2:
        raise TranslationError(gettext("At least two items are needed to make translations."))
    if len({type(o) for o in objs}) != 1:
        raise TranslationError(gettext("A post can only be a translation of a post, and a page of a page."))
    model = type(objs[0])
    groups = {o.translation_group for o in objs if o.translation_group}
    if len(groups) > 1:
        raise TranslationError(gettext("These items already belong to different translation groups. Detach one of them first."))
    group = groups.pop() if groups else uuid.uuid4()
    final = {o.pk: o for o in objs}
    if groups or any(o.translation_group for o in objs):  # members the group already has stay in it
        for existing in model.objects.filter(translation_group=group):
            final.setdefault(existing.pk, existing)
    seen: dict[str, object] = {}
    for obj in final.values():
        if obj.language in seen:
            raise TranslationError(gettext("The language “%(language)s” is already used by another item of this group: “%(title)s”.")
                                   % {"language": language_name(obj.language), "title": seen[obj.language].title})
        seen[obj.language] = obj
    model.objects.filter(pk__in=[o.pk for o in objs]).update(translation_group=group)
    for o in objs:
        o.translation_group = group
    return group


@transaction.atomic
def unlink(obj) -> None:
    """Take the object out of its group; a group left with a single member is dissolved (one text is not a translation of anything)."""
    group = obj.translation_group
    if not group:
        return
    model = type(obj)
    model.objects.filter(pk=obj.pk).update(translation_group=None)
    obj.translation_group = None
    rest = list(model.objects.filter(translation_group=group).values_list("pk", flat=True))
    if len(rest) == 1:
        model.objects.filter(pk__in=rest).update(translation_group=None)


def check_language_change(obj, new_language: str) -> None:
    """The form calls this before saving an existing item with another language: the new language must be free in its group."""
    if not obj.translation_group or new_language == obj.language:
        return
    for other in others(obj):
        if other.language == new_language:
            raise TranslationError(gettext("The language “%(language)s” is already used by another item of this group: “%(title)s”.")
                                   % {"language": language_name(new_language), "title": other.title})


# ------------------------------------------------------------------------------------------------------------------------- public side
@dataclass(frozen=True)
class Alternate:
    code: str  # Django language code ("pt-br")
    path: str  # site-relative URL under the item's own language prefix
    current: bool


def is_indexable_live(obj) -> bool:
    if isinstance(obj, Post):
        return obj.is_live and not obj.noindex
    return obj.status == PublishStatus.PUBLISHED


def is_live(obj) -> bool:
    return obj.is_live


def path_of(obj) -> str:
    route = "public:post_detail" if isinstance(obj, Post) else "public:page_detail"
    with translation.override(obj.language):
        return reverse(route, args=[obj.slug])


def live_alternates(obj) -> list[Alternate]:
    """The live translations of ``obj`` (itself included) with their own URLs; empty when there is nothing to alternate between."""
    if not enabled() or not obj.translation_group:
        return []
    found = [Alternate(m.language, path_of(m), m.pk == obj.pk) for m in members(obj) if m.language in dict(settings.LANGUAGES) and is_live(m)]
    return found if len(found) >= 2 and any(a.current for a in found) else []


def hreflang_entries(obj, absolute) -> list[tuple[str, str]]:
    """``[(hreflang, absolute_url), …]`` for the <head>/sitemap of ``obj``. Nothing when ``obj`` is a draft, not yet live or noindex, and
    translations that are drafts, scheduled in the future or noindex are left out. ``absolute`` turns a path into an absolute URL.
    x-default points at the English version, else at the site's default language when it is in the group, else it is omitted."""
    from apps.core.regional import regional_for
    from apps.public.seo import hreflang

    if not enabled() or not obj.translation_group or not is_indexable_live(obj):
        return []
    group = [m for m in members(obj) if m.language in dict(settings.LANGUAGES) and is_indexable_live(m)]
    if len(group) < 2 or not any(m.pk == obj.pk for m in group):
        return []
    entries = [(hreflang(m.language), absolute(path_of(m))) for m in group]
    by_lang = {m.language: m for m in group}
    default = regional_for(_site())[0]
    chosen = by_lang.get("en") or by_lang.get(default)
    if chosen is not None:
        entries.append(("x-default", absolute(path_of(chosen))))
    return entries


def _site():
    from apps.core.models import SiteSettings

    return SiteSettings.load()


def switcher_paths(obj) -> dict[str, str]:
    """Language code → path of the live translation, for the language switcher of a post or page."""
    return {a.code: a.path for a in live_alternates(obj)}


def translation_redirect(path: str, code: str):
    """For the language view: the URL of the translation in ``code`` of the post/page that ``path`` (a public path, any language prefix) shows,
    or None when ``path`` is not a post/page with such a translation."""
    from django.urls import Resolver404, resolve

    from apps.core.regional import split_language_prefix

    if not enabled():
        return None
    prefix, rest = split_language_prefix(path)
    if not prefix:
        return None
    try:
        with translation.override(prefix):
            match = resolve(path)
    except Resolver404:
        return None
    if match.view_name == "public:post_detail":
        model = Post
    elif match.view_name == "public:page_detail":
        model = Page
    else:
        return None
    slug = match.kwargs.get("slug") or (match.args[0] if match.args else "")
    obj = model.objects.filter(slug=slug).first()
    if obj is None or not is_live(obj):
        return None
    return switcher_paths(obj).get(code) if code != obj.language else None


def sitemap_alternates(absolute) -> dict[str, list[tuple[str, str]]]:
    """Path of every live, indexable member of a translation group → its hreflang entries (itself, its live translations, x-default)."""
    if not enabled():
        return {}
    result: dict[str, list[tuple[str, str]]] = {}
    for model in (Post, Page):
        groups: dict = {}
        for obj in model.objects.exclude(translation_group=None).defer("body", "body_html"):
            if obj.language in dict(settings.LANGUAGES) and is_indexable_live(obj):
                groups.setdefault(obj.translation_group, []).append(obj)
        for group in groups.values():
            if len(group) < 2:
                continue
            entries = hreflang_entries(group[0], absolute)  # same list for every member of the group
            for obj in group:
                result[path_of(obj)] = entries
    return result
