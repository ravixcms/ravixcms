"""
ساخت داده‌های سایت عمومی: کوئری‌ها (فقط محتوای منتشرشده) و تبدیل به DTO.

هیچ‌یک از این توابع داده‌ای نمی‌نویسند. قالب‌ها فقط خروجی این ماژول (``dto.py``) را می‌بینند.
"""
from django.utils.translation import gettext
import re
from urllib.parse import urlencode

from django.conf import settings
from apps.core.params import safe_id
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Count, Q
from django.db.models.functions import Substr
from django.http import Http404
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.html import linebreaks
from django.utils.safestring import mark_safe

from apps.content.models import Category, Page, Post, PublishStatus, Tag

from apps.core.markup import render_markdown
from apps.themes.config import localize_default_text, localized_section

from .dto import LanguageLink, LanguageOption, CategoryItem, CommentItem, CommentsSection, FeatureItem, PageItem, PageLink, Pagination, PostItem, Section, SectionButton, TagItem
from .formatting import date_display, date_iso, excerpt_from, reading_time


_STYLE_BLOCK = re.compile(r"<style\b.*?</style>", re.S | re.I)  # بلوک سبک صفحه‌ساز نباید وارد توضیحات سئو شود


# ------------------------------------------------------------------ نشانی‌ها
def _own_language(obj):
    """The object's language when it differs from the active one and the site separates languages (so links, e.g. from a menu,
    go straight to the right prefix instead of through a redirect); else None."""
    lang = getattr(obj, "language", "")
    if lang and lang != (translation.get_language() or "").lower() and lang in dict(settings.LANGUAGES) and separated_language():
        return lang
    return None


def post_url(post) -> str:
    own = _own_language(post)
    if own:
        with translation.override(own):
            return reverse("public:post_detail", args=[post.slug])
    return reverse("public:post_detail", args=[post.slug])


def category_url(category) -> str:
    return reverse("public:category_detail", args=[category.slug])


def tag_url(tag) -> str:
    return reverse("public:tag_detail", args=[tag.slug])


def page_url(page) -> str:
    own = _own_language(page)
    if own:
        with translation.override(own):
            return reverse("public:page_detail", args=[page.slug])
    return reverse("public:page_detail", args=[page.slug])


def localize_internal_url(url: str) -> str:
    """A site-relative URL typed by the admin (``/about/``) gets the current language prefix when the prefixed form is a
    real public route, so visitors stay in their language; anything else (panel, files, external) is returned unchanged."""
    from django.urls import is_valid_path
    from django.utils import translation

    from apps.core.regional import split_language_prefix

    if not url.startswith("/") or url.startswith("//") or split_language_prefix(url)[0]:
        return url
    path, sep, rest = url.partition("?")
    prefixed = f"/{translation.get_language()}{path}"
    return f"{prefixed}{sep}{rest}" if is_valid_path(prefixed) else url


# ------------------------------------------------------------------ کوئری‌های پایه (فقط منتشرشده)
def separated_language():
    """The language code the public lists are limited to: the active language when the site shows each post and page only in its
    own language, else ``None`` (every post under every prefix, as before the option existed)."""
    from apps.core.models import SiteSettings

    try:
        if not SiteSettings.load().content_per_language:
            return None
    except Exception:  # noqa: BLE001 - before the first migrate
        return None
    return (translation.get_language() or "").lower() or None


def live_posts(language=...):
    """نوشته‌های منتشرشده (شامل زمان‌بندی‌شده‌های رسیده)، جدیدترین اول. بدنه کامل بارگذاری نمی‌شود.
    ``language``: default = the separated language of this request (see ``separated_language``); ``None`` = all languages."""
    lang = separated_language() if language is ... else language
    qs = Post.objects.filter(language=lang) if lang else Post.objects.all()
    return (
        qs.live()
        .select_related("author")
        .prefetch_related("categories", "tags")
        .defer("body", "body_html")
        .annotate(body_head=Substr("body_html", 1, 800))
        .order_by("-publish_at", "-id")
    )


def live_pages(language=...):
    lang = separated_language() if language is ... else language
    qs = Page.objects.filter(status=PublishStatus.PUBLISHED)
    return qs.filter(language=lang) if lang else qs


def categories_with_counts():
    now = timezone.now()
    live = Q(posts__status=PublishStatus.PUBLISHED) | Q(posts__status=PublishStatus.SCHEDULED, posts__publish_at__lte=now)
    lang = separated_language()
    if lang:  # counts and "has posts" only for the posts of this language
        live = live & Q(posts__language=lang)
    return Category.objects.annotate(live_count=Count("posts", filter=live, distinct=True)).order_by("name")


# ------------------------------------------------------------------ DTO
def author_name(post) -> str:
    return post.author.display_name if post.author_id else ""


def category_item(category, count=None) -> CategoryItem:
    return CategoryItem(
        name=category.name,
        slug=category.slug,
        url=category_url(category),
        description=category.description,
        post_count=count if count is not None else getattr(category, "live_count", 0),
    )


def tag_item(tag) -> TagItem:
    return TagItem(name=tag.name, url=tag_url(tag))


def post_item(post, *, full: bool = False, body_html: str | None = None) -> PostItem:
    when = post.publish_at or post.created_at
    body = (post.body_html if body_html is None else body_html) if full else ""
    head = body if full else getattr(post, "body_head", "")
    return PostItem(
        title=post.title,
        url=post_url(post),
        excerpt=excerpt_from(post.excerpt, _STYLE_BLOCK.sub("", head)),
        image_url=post.featured_image.url if post.featured_image else "",
        author_name=author_name(post),
        date_display=date_display(when),
        date_iso=date_iso(when),
        categories=tuple(category_item(c, 0) for c in post.categories.all()),
        tags=tuple(tag_item(t) for t in post.tags.all()),
        reading_time=reading_time(post.body_html) if full else "",
        builder=body_html is not None and full,
        body_html=mark_safe(body),  # خروجی nh3 هنگام ذخیره؛ امن
    )


def page_item(page, *, body_html: str | None = None, builder: bool = False, show_title: bool = True, full_width: bool = True) -> PageItem:
    """``body_html`` را فقط کد سرور (خروجی رندر امن صفحه‌ساز) می‌دهد؛ پیش‌فرض همان HTML پاک‌سازی‌شده‌ی Markdown است."""
    return PageItem(
        builder=builder,
        show_title=show_title,
        full_width=full_width,
        title=page.title,
        url=page_url(page),
        body_html=mark_safe(page.body_html if body_html is None else body_html),
        date_display=date_display(page.updated_at),
        date_iso=date_iso(page.updated_at),
        excerpt=excerpt_from(page.seo_description, _STYLE_BLOCK.sub("", page.body_html if body_html is None else body_html)),
    )


# ------------------------------------------------------------------ دیدگاه‌ها
def build_comments(post, *, state: dict | None = None, notice: str = "", reply_to: int = 0) -> CommentsSection:
    """درخت دیدگاه‌های تأییدشدهٔ یک نوشته + وضعیت فرم. ``state`` = {errors, values} پس از ارسال ناموفق."""
    from apps.comments.models import Comment

    state = state or {}
    rows = list(Comment.objects.filter(post=post, status=Comment.Status.APPROVED).order_by("created_at", "id"))
    children: dict = {}
    for c in rows:
        children.setdefault(c.parent_id, []).append(c)
    base = post_url(post)
    can_reply = post.comments_open

    def node(c) -> CommentItem:
        return CommentItem(
            id=c.pk, author_name=c.author_name, date_display=date_display(c.created_at), date_iso=date_iso(c.created_at),
            body_html=mark_safe(c.body_html), depth=c.depth,
            reply_url=f"{base}?reply={c.pk}#comment-form" if can_reply and c.depth < settings.RAVIX_COMMENT_MAX_DEPTH else "",
            replies=tuple(node(r) for r in children.get(c.pk, [])),
        )

    items = tuple(node(c) for c in children.get(None, []))
    target = next((c for c in rows if c.pk == reply_to and c.depth < settings.RAVIX_COMMENT_MAX_DEPTH), None) if can_reply else None
    values = dict(state.get("values", {}))
    if target is None and safe_id(state.get("values", {}).get("parent", "")) is not None:
        target = next((c for c in rows if c.pk == safe_id(values["parent"]) and c.depth < settings.RAVIX_COMMENT_MAX_DEPTH), None)
    return CommentsSection(
        count=len(rows), items=items, open=post.comments_open, action_url=reverse("public:comment_submit", args=[post.slug]),
        honeypot_name="website", notice=notice if notice in ("pending", "ok") else "", errors=state.get("errors", {}),
        values={k: v for k, v in values.items() if k in ("name", "email", "body")},
        reply_to_id=target.pk if target else 0, reply_to_name=target.author_name if target else "",
        cancel_reply_url=f"{base}#comment-form" if target else "", max_length=settings.RAVIX_COMMENT_MAX_LENGTH,
    )


# ------------------------------------------------------------------ صفحه‌بندی
def paginate(request, queryset, *, base_url: str, extra: dict | None = None, per_page: int | None = None):
    """(page_obj, Pagination). شماره صفحه نامعتبر یا خارج از محدوده ← 404."""
    per_page = per_page or settings.PUBLIC_PAGE_SIZE
    paginator = Paginator(queryset, per_page)
    raw = request.GET.get("page", "1")
    try:
        page_obj = paginator.page(raw)
    except (PageNotAnInteger, EmptyPage):
        raise Http404(gettext("Page does not exist"))

    def link(n: int) -> str:
        params = dict(extra or {})
        if n > 1:
            params["page"] = n
        if not params:
            return base_url
        return f"{base_url}?{urlencode(params)}"

    current, last = page_obj.number, paginator.num_pages
    numbers = sorted({1, last, *range(max(1, current - 2), min(last, current + 2) + 1)})
    links, previous = [], 0
    for n in numbers:
        if previous and n - previous > 1:
            links.append(PageLink(number=0, url=""))
        links.append(PageLink(number=n, url=link(n), current=n == current))
        previous = n
    return page_obj, Pagination(
        page=current,
        num_pages=last,
        total=paginator.count,
        has_prev=page_obj.has_previous(),
        has_next=page_obj.has_next(),
        prev_url=link(current - 1) if page_obj.has_previous() else "",
        next_url=link(current + 1) if page_obj.has_next() else "",
        pages=tuple(links),
    )


# ------------------------------------------------------------------ بخش‌های صفحه اصلی
def _markdown_html(text: str):
    return mark_safe(render_markdown(text)) if text else ""  # sanitized by nh3 (apps.core.markup)


def build_sections(doc: dict, allowed: tuple, site: dict) -> list[Section]:
    from django.utils.translation import get_language

    from apps.core.models import SiteSettings

    language, default_language = get_language() or "", SiteSettings.load().language
    sections = []
    for section in doc["home"]["sections"]:
        if not section["enabled"] or section["type"] not in allowed:
            continue
        raw = localized_section(section, language, default_language)  # the visitor's language, field by field
        kind, count = raw["type"], raw["count"]
        buttons = tuple(SectionButton(label=localize_default_text(b["label"]), url=localize_internal_url(b["url"])) for b in raw["buttons"])
        base = dict(
            type=kind,
            title=localize_default_text(raw["title"]),
            subtitle=raw["subtitle"],
            body_html=_markdown_html(raw["body"]),
            button_label=localize_default_text(raw["button_label"]) if raw["button_url"] else "",
            button_url=localize_internal_url(raw["button_url"]),
            buttons=buttons,
        )
        if kind == "hero":
            base["title"] = raw["title"] or site["name"]
            base["subtitle"] = raw["subtitle"] or site["tagline"]
        elif kind == "latest_posts":
            base["posts"] = tuple(post_item(p) for p in live_posts()[:count])
            if not base["posts"]:
                continue
        elif kind == "category_posts":
            category = Category.objects.filter(pk=raw["category"]).first() if raw["category"] else None
            if category is None:
                continue
            ids = category.descendant_ids() | {category.pk}
            posts = live_posts().filter(categories__in=ids).distinct()[:count]
            base["posts"] = tuple(post_item(p) for p in posts)
            base["title"] = raw["title"] or category.name
            base["button_url"] = base["button_url"] or category_url(category)
            base["button_label"] = base["button_label"] or gettext("View all")
            if not base["posts"]:
                continue
        elif kind == "categories":
            base["categories"] = tuple(category_item(c) for c in categories_with_counts().filter(live_count__gt=0)[:count])
            if not base["categories"]:
                continue
        elif kind == "page":
            page = live_pages(language=None).filter(pk=raw["page"]).first() if raw["page"] else None
            if page is None:
                continue
            base["page"] = page_item(page)
            base["title"] = raw["title"] or page.title
        elif kind == "text" and not (raw["title"] or raw["body"]):
            continue
        elif kind == "features":
            base["items"] = tuple(FeatureItem(title=i["title"], icon=i["icon"], text=i["text"]) for i in raw["items"])
            if not base["items"]:
                continue
        sections.append(Section(**base))
    return sections


def language_options():
    """Languages (in ``settings.LANGUAGES`` order) that have at least one live post, each with the address of its blog."""
    from django.utils.translation import override

    from apps.core.regional import language_choices

    counts = dict(Post.objects.live().order_by().values_list("language").annotate(n=Count("pk")))
    options = []
    for code, name in language_choices():
        if counts.get(code):
            with override(code):
                options.append(LanguageOption(code=code, name=name, url=reverse("public:post_list"), post_count=counts[code]))
    return tuple(options)


def wrong_language_redirect(request, obj, route: str, *args):
    """301 to the object's own language when posts and pages are separated by language and the visitor is under another prefix."""
    from django.http import HttpResponsePermanentRedirect

    lang = separated_language()
    if not lang or not obj.language or obj.language == lang or obj.language not in dict(settings.LANGUAGES):
        return None
    with translation.override(obj.language):
        url = reverse(route, args=args)
    query = request.META.get("QUERY_STRING", "")
    return HttpResponsePermanentRedirect(f"{url}?{query}" if query else url)


_BLOG_HOME_ROUTES = {"public:post_detail", "public:comment_submit", "public:category_detail", "public:tag_detail"}


def language_links(request) -> tuple:
    """One ``LanguageLink`` per site language for a language switcher (the ``languages`` variable of every theme page).

    Home, blog, search and other pages that exist in every language keep their page; a post, category or tag goes to the blog of the
    chosen language (a post exists in one language only); a static page goes to the home page when languages are separated; an address
    without a language prefix (a plugin page) keeps its path and the language cookie decides."""
    from apps.core.regional import split_language_prefix, swap_language_prefix

    from .seo import hreflang

    current = (translation.get_language() or "").lower()
    path = request.path
    route = getattr(getattr(request, "resolver_match", None), "view_name", "") or ""
    separated = False
    if route == "public:page_detail":
        separated = bool(separated_language())
    prefixed = split_language_prefix(path)[0] != ""
    links = []
    for code, name in settings.LANGUAGES:
        if route in _BLOG_HOME_ROUTES:
            with translation.override(code):
                url = reverse("public:post_list")
        elif separated:
            with translation.override(code):
                url = reverse("public:home")
        elif prefixed:
            url = swap_language_prefix(path, code) or path
        else:
            url = path
        links.append(LanguageLink(code=code, hreflang=hreflang(code), name=name, short_name=name.split(" (")[0], url=url, current=(code == current),
                                  dir="rtl" if code in settings.LANGUAGES_BIDI else "ltr"))
    return tuple(links)
