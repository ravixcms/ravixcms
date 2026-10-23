"""فید RSS 2.0 و Atom با ``django.contrib.syndication``: همهٔ نوشته‌ها، به‌تفکیک دسته (با زیردسته‌ها) و برچسب.

- فقط نوشته‌های منتشرشده (شامل زمان‌بندی‌شده‌های رسیده) و جدیدترین اول؛ تعداد: ``RAVIX_FEED_ITEMS`` (پیش‌فرض ۲۰).
- تاریخ انتشار هر مورد ``publish_at`` است (نه زمان ساخت پیش‌نویس) و ``updated_at`` تاریخ به‌روزرسانی؛ هر دو aware (UTC) و با offset درست.
- نشانی‌ها مطلق‌اند و در صورت تنظیم، از ``RAVIX_SITE_URL`` ساخته می‌شوند (نه از سرآیند Host).
- ``request`` از طریق شیء ``obj`` (نه ``self``) رد می‌شود؛ چون نمونهٔ Feed بین درخواست‌ها مشترک است.
"""
from django.utils.translation import gettext
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.contrib.syndication.views import Feed
from django.shortcuts import get_object_or_404
from django.utils.feedgenerator import Atom1Feed, Rss201rev2Feed
from django.views.decorators.http import require_safe

from apps.content.models import Category, Tag
from apps.core.models import SiteSettings

from . import builders as b
from .dto import FeedLink
from .seo import absolute_url

RSS_TYPE = "application/rss+xml"
ATOM_TYPE = "application/atom+xml"


def feed_items_limit() -> int:
    return max(1, min(int(getattr(settings, "RAVIX_FEED_ITEMS", 20)), 100))


@dataclass
class FeedTarget:
    request: Any
    site: SiteSettings
    category: Any = None
    tag: Any = None


@dataclass
class FeedEntry:
    post: Any
    item: Any  # PostItem
    request: Any
    site_name: str


def _paths(category=None, tag=None):
    """(مسیر RSS، مسیر Atom) برای دامنهٔ فید."""
    from django.urls import reverse

    if category is not None:
        return reverse("public:category_feed", args=[category.slug]), reverse("public:category_feed_atom", args=[category.slug])
    if tag is not None:
        return reverse("public:tag_feed", args=[tag.slug]), reverse("public:tag_feed_atom", args=[tag.slug])
    return reverse("public:feed"), reverse("public:feed_atom")


def feed_links(request, *, category=None, tag=None, site_name: str = "") -> tuple:
    """لینک‌های autodiscovery برای <head>: فید همین دسته/برچسب (اگر هست) و بعد فید کل سایت."""
    name = site_name or SiteSettings.load().site_name
    links = []
    scopes = [(category, tag)] if (category is not None or tag is not None) else []
    scopes.append((None, None))
    for cat, tg in scopes:
        label = name if cat is None and tg is None else (gettext("%(name)s — %(value)s") % {"name": name, "value": (cat.name if cat is not None else gettext("Tag: ") + tg.name)})
        rss, atom = _paths(cat, tg)
        links.append(FeedLink(title=f"{label} (RSS)", url=absolute_url(request, rss), type=RSS_TYPE))
        links.append(FeedLink(title=f"{label} (Atom)", url=absolute_url(request, atom), type=ATOM_TYPE))
    return tuple(links)


class PostFeed(Feed):
    feed_type = Rss201rev2Feed
    item_guid_is_permalink = True
    scope = "site"  # site | category | tag

    # ------------------------------------------------------------ شیء فید
    def get_object(self, request, slug=None):
        target = FeedTarget(request=request, site=SiteSettings.load())
        if self.scope == "category":
            target.category = get_object_or_404(Category, slug=slug)
        elif self.scope == "tag":
            target.tag = get_object_or_404(Tag, slug=slug)
        return target

    def title(self, obj):
        name = obj.site.site_name
        if obj.category is not None:
            return f"{name} — {obj.category.name}"
        if obj.tag is not None:
            return (gettext("%(name)s — tag: %(name2)s") % {"name": name, "name2": obj.tag.name})
        return name

    def description(self, obj):
        if obj.category is not None and obj.category.description:
            return obj.category.description
        return obj.site.description or obj.site.tagline or self.title(obj)

    def link(self, obj):
        if obj.category is not None:
            path = b.category_url(obj.category)
        elif obj.tag is not None:
            path = b.tag_url(obj.tag)
        else:
            from django.urls import reverse

            path = reverse("public:home")
        return absolute_url(obj.request, path)

    def feed_url(self, obj):
        return absolute_url(obj.request, obj.request.path)

    # ------------------------------------------------------------ موردها
    def items(self, obj):
        qs = b.live_posts()
        if obj.category is not None:
            ids = obj.category.descendant_ids() | {obj.category.pk}
            qs = qs.filter(categories__in=ids).distinct()
        elif obj.tag is not None:
            qs = qs.filter(tags=obj.tag)
        return [FeedEntry(p, b.post_item(p), obj.request, obj.site.site_name) for p in qs[: feed_items_limit()]]

    def item_title(self, entry):
        return entry.post.title

    def item_description(self, entry):
        return entry.item.excerpt

    def item_link(self, entry):
        return absolute_url(entry.request, entry.item.url)

    def item_guid(self, entry):
        return self.item_link(entry)

    def item_pubdate(self, entry):
        return entry.post.publish_at or entry.post.created_at

    def item_updateddate(self, entry):
        return entry.post.updated_at

    def item_author_name(self, entry):
        return entry.item.author_name or entry.site_name  # Atom به نویسنده نیاز دارد

    def item_categories(self, entry):
        names = [c.name for c in entry.post.categories.all()] + [t.name for t in entry.post.tags.all()]
        return tuple(dict.fromkeys(names))


class AtomPostFeed(PostFeed):
    feed_type = Atom1Feed

    def subtitle(self, obj):
        return self.description(obj)


def _view(feed):
    @require_safe
    def view(request, *args, **kwargs):
        response = feed(request, *args, **kwargs)
        response["Cache-Control"] = "public, max-age=0, must-revalidate"  # با ETag/Last-Modified بازبینی می‌شود
        return response

    return view


class _CategoryRss(PostFeed):
    scope = "category"


class _CategoryAtom(AtomPostFeed):
    scope = "category"


class _TagRss(PostFeed):
    scope = "tag"


class _TagAtom(AtomPostFeed):
    scope = "tag"


site_rss = _view(PostFeed())
site_atom = _view(AtomPostFeed())
category_rss = _view(_CategoryRss())
category_atom = _view(_CategoryAtom())
tag_rss = _view(_TagRss())
tag_atom = _view(_TagAtom())
