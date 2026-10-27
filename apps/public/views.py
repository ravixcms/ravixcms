"""ویوهای سایت عمومی. همگی فقط‌خواندنی‌اند و فقط محتوای منتشرشده را نشان می‌دهند."""
from apps.core.clilang import cli_gettext
from django.utils.translation import gettext
import logging
from xml.sax.saxutils import escape

from django.conf import settings
from apps.core.params import safe_id
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View

from apps.accounts.mixins import CapabilityRequiredMixin
from apps.builder.public import published_body
from apps.content.models import Category, Post, Tag
from apps.plugins.hooks import apply_filter
from apps.themes.models import ThemeConfig  # noqa: F401  (برای وابستگی مهاجرت)

from . import builders as b
from .dto import Crumb, Link
from .feeds import feed_links
from .formatting import date_iso
from .rendering import render_page
from .seo import absolute_url, clip, finish_seo, make_seo

# مسیرهایی که صفحه خطای ۴۰۴ آن‌ها با ظاهر پنل نمایش داده می‌شود، نه قالب سایت.
logger = logging.getLogger(__name__)

ADMIN_PREFIXES = ("/panel/", "/accounts/", "/api/", "/django-admin/", "/static/", "/media/")


class PublicView(View):
    http_method_names = ["get", "head", "options"]
    template = ""
    embedded = False
    force_draft = False

    def respond(self, request, context, *, seo, status=200, edit_url="", template=None):
        return render_page(
            request, template or self.template, context, status=status, embedded=self.embedded,
            force_draft=self.force_draft, edit_url=edit_url, seo_factory=seo,
        )


def _crumbs(*items) -> tuple:
    return tuple(Crumb(label=l, url=u) for l, u in items)


def _page_suffix(pagination) -> str:
    return (gettext(" (page %(page)s)") % {"page": pagination.page}) if pagination.page > 1 else ""


def _paging_seo(pagination):
    return pagination.prev_url if pagination.has_prev else "", pagination.next_url if pagination.has_next else ""


# ------------------------------------------------------------------ صفحه اصلی
class HomeView(PublicView):
    template = "home.html"

    def get(self, request):
        def context(resolved, base):
            # بخش‌ها به اطلاعات سایت (نام/شعار) و فهرست بخش‌هایی که قالب پشتیبانی می‌کند نیاز دارند
            return {"sections": tuple(b.build_sections(resolved.doc, resolved.theme.home_sections, base["site"]))}

        def seo(site):
            url = absolute_url(request, reverse("public:home"))
            data = {
                "@context": "https://schema.org", "@type": "WebSite", "name": site["name"], "url": url,
                "potentialAction": {
                    "@type": "SearchAction",
                    "target": absolute_url(request, reverse("public:search")) + "?q={search_term_string}",
                    "query-input": "required name=search_term_string",
                },
            }
            return make_seo(request, site, path=reverse("public:home"), jsonld=[data])

        return self.respond(request, context, seo=seo)


class PreviewFrameView(CapabilityRequiredMixin, HomeView):
    """نمای زنده‌ی پیش‌نویس برای iframe هم‌مبدأ در ویرایشگر ظاهر (فقط مدیران قالب؛ همیشه پیش‌نویس)."""

    required_capability = "themes.manage"
    embedded = True
    force_draft = True

    def get(self, request):
        if request.GET.get("view") == "blog":
            return PostListView.as_view(embedded=True, force_draft=True)(request)
        return super().get(request)


# ------------------------------------------------------------------ نوشته‌ها
class PostListView(PublicView):
    template = "post_list.html"

    def get(self, request):
        page_obj, pagination = b.paginate(request, b.live_posts(), base_url=reverse("public:post_list"))
        posts = tuple(b.post_item(p) for p in page_obj.object_list)
        categories = tuple(b.category_item(c) for c in b.categories_with_counts().filter(live_count__gt=0))
        prev, nxt = _paging_seo(pagination)

        def seo(site):
            return make_seo(
                request, site, title=(gettext("Posts%(page_suffix)s") % {"page_suffix": _page_suffix(pagination)}), path=request.path + (f"?page={pagination.page}" if pagination.page > 1 else ""),
                prev=prev, next_=nxt,
            )

        ctx = {"posts": posts, "pagination": pagination, "categories": categories, "breadcrumbs": _crumbs((gettext("Home"), reverse("public:home")), (gettext("Posts"), ""))}
        return self.respond(request, ctx, seo=seo)


class PostDetailView(PublicView):
    template = "post_detail.html"

    def get_post(self, slug):
        return get_object_or_404(Post.objects.live().select_related("author").prefetch_related("categories", "tags"), slug=slug)

    def get(self, request, slug):
        reply = request.GET.get("reply", "")
        return self.render_post(request, self.get_post(slug), notice=request.GET.get("cm", ""), reply_to=safe_id(reply) or 0)

    def render_post(self, request, post, *, state=None, notice="", reply_to=0, status=200):
        item = b.post_item(post, full=True)
        category_ids = [c.pk for c in post.categories.all()]
        related = ()
        if category_ids:
            qs = b.live_posts().filter(categories__in=category_ids).exclude(pk=post.pk).distinct()[:3]
            related = tuple(b.post_item(p) for p in qs)
        crumbs = [(gettext("Home"), reverse("public:home")), (gettext("Posts"), reverse("public:post_list"))]
        first = next(iter(post.categories.all()), None)
        if first:
            crumbs.append((first.name, b.category_url(first)))
        crumbs.append((post.title, ""))

        def seo(site):
            article = {
                "@context": "https://schema.org", "@type": "Article", "headline": post.title[:110],
                "datePublished": item.date_iso, "dateModified": date_iso(post.updated_at),
                "mainEntityOfPage": absolute_url(request, item.url),
                "publisher": {"@type": "Organization", "name": site["name"]},
            }
            if item.author_name:
                article["author"] = {"@type": "Person", "name": item.author_name}
            if item.image_url:
                article["image"] = [absolute_url(request, item.image_url)]
            data = make_seo(request, site, title=post.effective_title_tag, description=post.seo_description or item.excerpt,
                            path=item.url, og_type="article", og_image=item.image_url, jsonld=[article],
                            robots="noindex,follow" if post.noindex else "",
                            published_time=item.date_iso, modified_time=date_iso(post.updated_at))
            return finish_seo(request, apply_filter("public.seo", data, kind="post", obj_id=post.pk, site=site, request=request))

        ctx = {"post": item, "related": related, "breadcrumbs": _crumbs(*crumbs),
               "comments": b.build_comments(post, state=state, notice=notice, reply_to=reply_to)}
        return self.respond(request, ctx, seo=seo, status=status, edit_url=reverse("content:post_update", args=[post.pk]))


class CommentSubmitView(PostDetailView):
    """ثبت دیدگاه عمومی (POST). بدون CSRF چون هیچ نشست/هویتی به دیدگاه وصل نمی‌شود و صفحه‌های عمومی کش می‌شوند؛
    به‌جایش: بررسی Origin، honeypot، محدودیت نرخ، کلمات مسدود و تشخیص تکراری (apps/comments/services.py)."""

    http_method_names = ["post", "options"]

    def post(self, request, slug):
        from urllib.parse import urlsplit

        from apps.comments import services

        origin = request.META.get("HTTP_ORIGIN")
        if origin:
            allowed = {request.get_host()}
            if settings.SITE_URL:
                allowed.add(urlsplit(settings.SITE_URL).netloc)
            if urlsplit(origin).netloc not in allowed:
                return HttpResponse("Forbidden", status=403, content_type="text/plain; charset=utf-8")
        post = self.get_post(slug)
        data = {k: request.POST.get(k, "") for k in ("name", "email", "body", "parent", services.HONEYPOT)}
        try:
            comment = services.submit(request, post, data)
        except services.CommentRejected as exc:
            return self.render_post(request, post, state={"errors": exc.errors, "values": data}, status=exc.http_status)
        base = reverse("public:post_detail", args=[post.slug])
        if comment is not None and comment.status == "approved":
            return HttpResponseRedirect(f"{base}?cm=ok#comment-{comment.pk}")
        # در انتظار، اسپم و honeypot دقیقاً یک پاسخ دارند (ربات نفهمد کدام‌یک شده است)
        return HttpResponseRedirect(f"{base}?cm=pending#comments")


class CategoryDetailView(PublicView):
    template = "category_detail.html"

    def get(self, request, slug):
        category = get_object_or_404(Category, slug=slug)
        ids = category.descendant_ids() | {category.pk}
        queryset = b.live_posts().filter(categories__in=ids).distinct()
        url = b.category_url(category)
        page_obj, pagination = b.paginate(request, queryset, base_url=url)
        posts = tuple(b.post_item(p) for p in page_obj.object_list)
        children = tuple(b.category_item(c) for c in b.categories_with_counts().filter(parent=category, live_count__gt=0))
        prev, nxt = _paging_seo(pagination)
        crumbs = _crumbs((gettext("Home"), reverse("public:home")), (gettext("Posts"), reverse("public:post_list")), (category.name, ""))

        def seo(site):
            return make_seo(
                request, site, title=category.name + _page_suffix(pagination), description=category.description,
                path=url + (f"?page={pagination.page}" if pagination.page > 1 else ""), prev=prev, next_=nxt,
            )

        ctx = {"category": b.category_item(category, pagination.total), "posts": posts, "pagination": pagination,
               "children": children, "breadcrumbs": crumbs, "feeds": feed_links(request, category=category)}
        return self.respond(request, ctx, seo=seo, edit_url=reverse("content:category_update", args=[category.pk]))


class TagDetailView(PublicView):
    template = "tag_detail.html"

    def get(self, request, slug):
        tag = get_object_or_404(Tag, slug=slug)
        url = b.tag_url(tag)
        page_obj, pagination = b.paginate(request, b.live_posts().filter(tags=tag), base_url=url)
        posts = tuple(b.post_item(p) for p in page_obj.object_list)
        prev, nxt = _paging_seo(pagination)
        crumbs = _crumbs((gettext("Home"), reverse("public:home")), (gettext("Posts"), reverse("public:post_list")), ((gettext("Tag: %(name)s") % {"name": tag.name}), ""))

        def seo(site):
            return make_seo(
                request, site, title=(gettext("Tag: %(name)s%(page_suffix)s") % {"name": tag.name, "page_suffix": _page_suffix(pagination)}), path=url + (f"?page={pagination.page}" if pagination.page > 1 else ""),
                prev=prev, next_=nxt, robots="" if pagination.total else "noindex,follow",
            )

        ctx = {"tag": b.tag_item(tag), "posts": posts, "pagination": pagination, "breadcrumbs": crumbs, "feeds": feed_links(request, tag=tag)}
        return self.respond(request, ctx, seo=seo, edit_url=reverse("content:tag_update", args=[tag.pk]))


# ------------------------------------------------------------------ صفحه ثابت
class PageDetailView(PublicView):
    template = "page_detail.html"

    def get(self, request, slug):
        page = get_object_or_404(b.live_pages(), slug=slug)
        item = b.page_item(page)
        built = published_body(page)
        if built is not None:  # صفحه طرح منتشرشده‌ی صفحه‌ساز دارد؛ همان صفحه/نشانی/سئو، فقط بدنه از صفحه‌ساز
            item = b.page_item(page, body_html=built[0], builder=True, show_title=built[1])

        def seo(site):
            data = make_seo(request, site, title=page.effective_title_tag, description=page.seo_description or item.excerpt, path=item.url)
            return finish_seo(request, apply_filter("public.seo", data, kind="page", obj_id=page.pk, site=site, request=request))

        ctx = {"page": item, "breadcrumbs": _crumbs((gettext("Home"), reverse("public:home")), (page.title, ""))}
        return self.respond(request, ctx, seo=seo, edit_url=reverse("content:page_update", args=[page.pk]))


# ------------------------------------------------------------------ جستجو
class SearchView(PublicView):
    template = "search.html"
    MIN_LENGTH = 2

    def get(self, request):
        query = " ".join(request.GET.get("q", "").split())[:100]
        searched = len(query) >= self.MIN_LENGTH
        posts, pages, pagination = (), (), None
        if searched:
            cond = Q(title__icontains=query) | Q(excerpt__icontains=query) | Q(body__icontains=query)
            page_obj, pagination = b.paginate(request, b.live_posts().filter(cond), base_url=reverse("public:search"), extra={"q": query})
            posts = tuple(b.post_item(p) for p in page_obj.object_list)
            if pagination.page == 1:
                found = b.live_pages().filter(Q(title__icontains=query) | Q(body__icontains=query))[:10]
                pages = tuple(Link(label=p.title, url=b.page_url(p)) for p in found)

        def seo(site):
            return make_seo(request, site, title=(gettext("Search: %(query)s") % {"query": query}) if query else gettext("Search"), path=reverse("public:search"), robots="noindex,follow")

        ctx = {"query": query, "searched": searched, "posts": posts, "pages": pages, "pagination": pagination,
               "total": (pagination.total if pagination else 0) + len(pages), "breadcrumbs": _crumbs((gettext("Home"), reverse("public:home")), (gettext("Search"), ""))}
        return self.respond(request, ctx, seo=seo)


# ------------------------------------------------------------------ sitemap و robots
class SitemapView(View):
    """``/<lang>/sitemap.xml``: every public URL in this language, each with ``xhtml:link`` hreflang alternates."""

    http_method_names = ["get", "head"]
    LIMIT = 50000

    def get(self, request):
        from apps.core.regional import language_choices, load_site_settings, regional_for, swap_language_prefix

        from .seo import hreflang

        entries = [(reverse("public:home"), None), (reverse("public:post_list"), None)]
        for c in b.categories_with_counts().filter(live_count__gt=0):
            entries.append((b.category_url(c), c.updated_at))
        for p in b.live_pages():
            entries.append((b.page_url(p), p.updated_at))
        for p in b.live_posts().filter(noindex=False).only("slug", "updated_at", "publish_at", "status", "created_at", "author", "featured_image", "title", "excerpt"):
            entries.append((b.post_url(p), p.updated_at))
        from apps.core import modules

        if settings.SHOP_ENABLED and modules.is_enabled("shop"):  # محصولات فعالِ سایتِ این دامنه
            from apps.shop.models import Product
            from apps.sites.middleware import current_site

            site = current_site(request)
            if site is not None:
                entries.append((reverse("shop:list"), None))
                for pr in Product.objects.filter(site=site, is_active=True).only("slug", "updated_at"):
                    entries.append((pr.get_absolute_url(), pr.updated_at))
        languages = [code for code, _name in language_choices()]
        default = regional_for(load_site_settings(request))[0]
        lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">']
        for path, modified in entries[: self.LIMIT]:
            lastmod = f"<lastmod>{modified.date().isoformat()}</lastmod>" if modified else ""
            alternates = ""
            if swap_language_prefix(path, "fa") is not None:
                links = [(hreflang(code), swap_language_prefix(path, code)) for code in languages]
                links.append(("x-default", swap_language_prefix(path, default)))
                alternates = "".join(
                    f'<xhtml:link rel="alternate" hreflang="{lang}" href="{escape(absolute_url(request, href))}"/>' for lang, href in links)
            lines.append(f"<url><loc>{escape(absolute_url(request, path))}</loc>{lastmod}{alternates}</url>")
        lines.append("</urlset>")
        response = HttpResponse("\n".join(lines), content_type="application/xml; charset=utf-8")
        response["Cache-Control"] = "public, max-age=0, must-revalidate"
        return response


class SitemapIndexView(View):
    """``/sitemap.xml``: a sitemap index pointing at one sitemap per UI language (language-neutral, never redirected)."""

    http_method_names = ["get", "head"]

    def get(self, request):
        from apps.core.regional import language_choices

        from django.utils import translation

        lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for code, _name in language_choices():
            with translation.override(code):
                loc = reverse("public:sitemap")
            lines.append(f"<sitemap><loc>{escape(absolute_url(request, loc))}</loc></sitemap>")
        lines.append("</sitemapindex>")
        response = HttpResponse("\n".join(lines), content_type="application/xml; charset=utf-8")
        response["Cache-Control"] = "public, max-age=0, must-revalidate"
        return response


class RobotsView(View):
    http_method_names = ["get", "head"]

    def get(self, request):
        from apps.core.regional import language_choices

        blocked = ["/panel/", "/accounts/", "/api/", "/django-admin/"]
        per_language = ["search/", "shop/cart/", "shop/checkout/", "shop/order/", "shop/pay/"]
        for code, _name in language_choices():
            blocked += [f"/{code}/{tail}" for tail in per_language]
        blocked += ["/search/", "/shop/cart/", "/shop/checkout/", "/shop/order/", "/shop/pay/"]  # legacy unprefixed URLs
        text = "\n".join(
            ["User-agent: *", *[f"Disallow: {path}" for path in blocked], "",
             f"Sitemap: {absolute_url(request, reverse('seo:index'))}", ""]
        )
        response = HttpResponse(text, content_type="text/plain; charset=utf-8")
        response["Cache-Control"] = "public, max-age=3600"
        return response


# ------------------------------------------------------------------ ۴۰۴ با ظاهر سایت
def render_not_found(request):
    """صفحه ۴۰۴ با قالب فعال؛ فقط برای مسیرهای سایت عمومی (نه پنل/API). خطا در قالب هرگز به ۵۰۰ تبدیل نمی‌شود."""
    if request.path.startswith(ADMIN_PREFIXES):
        return None

    def seo(site):
        return {"title": (gettext("Page not found | %(site)s") % {"site": site['name']}), "robots": "noindex,follow", "description": "", "canonical": ""}

    try:
        return render_page(request, "404.html", {"breadcrumbs": ()}, status=404, seo_factory=seo)
    except Exception:  # noqa: BLE001 - هر خطای قالب → صفحه خطای ساده پنل
        logger.exception(cli_gettext("Rendering the 404 page with the site theme failed."))
        return None
