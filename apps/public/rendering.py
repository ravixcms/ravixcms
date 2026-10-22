"""
رندر صفحات عمومی با قالب فعال.

مسئولیت‌ها: انتخاب قالب و پیکربندی (منتشرشده یا پیش‌نویس)، ساخت زمینه‌ی مشترک (سایت، منوها، متغیرهای رنگ/فونت،
نوار مدیر)، هدرهای امنیتی و کش، و بازگشت خودکار به قالب پیش‌فرض اگر قالب فعال ناموجود یا خراب باشد.
"""
import logging
from dataclasses import dataclass

from django.conf import settings
from django.http import HttpResponse
from django.templatetags.static import static
from django.template import TemplateDoesNotExist, TemplateSyntaxError
from django.urls import reverse
from django.utils.cache import patch_vary_headers

from apps.content.models import Category, Page
from apps.core.models import SiteSettings
from apps.media_library.models import Media
from apps.themes import engine, preview, registry, services
from apps.themes.colors import derived_palette
from apps.themes.constants import DEFAULT_THEME, FONT_SIZES, FONTS
from apps.themes.models import ThemeConfig

from .builders import category_url, page_url
from .dto import Link
from .feeds import feed_links

logger = logging.getLogger(__name__)

CSP = (
    "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
    "font-src 'self'; media-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
)


class ThemeUnavailable(Exception):
    """هیچ قالب معتبری (حتی پیش‌فرض) برای رندر وجود ندارد."""


@dataclass
class Resolved:
    theme: object
    doc: dict
    is_preview: bool
    fallback: bool = False


def resolve(request, *, force_draft: bool = False) -> Resolved:
    cfg = ThemeConfig.load()
    is_preview = force_draft or preview.is_active(request)
    doc = services.working_doc(cfg) if is_preview else services.published_doc(cfg)
    themes = registry.get_themes()
    theme = themes.get(doc["theme"])
    fallback = False
    if theme is None or not theme.valid:
        logger.warning("قالب «%s» در دسترس نیست؛ قالب پیش‌فرض جایگزین شد.", doc["theme"])
        theme, fallback = themes.get(DEFAULT_THEME), True
    if theme is None or not theme.valid:
        raise ThemeUnavailable()
    return Resolved(theme=theme, doc=doc, is_preview=is_preview, fallback=fallback)


# ------------------------------------------------------------------ اجزای زمینه
def _theme_vars(appearance: dict) -> dict:
    palette = derived_palette(appearance["primary"], appearance["background"], appearance["text"])
    font = FONTS[appearance["font"]]
    return {
        **palette,
        "font_stack": font["stack"],
        "font_size_px": FONT_SIZES[appearance["font_size"]][1],
        "font_preload_url": static(font["preload"]) if font["preload"] else "",
    }


def _media_urls(ids: list[int]) -> dict[int, str]:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    return {m.pk: m.file.url for m in Media.objects.filter(pk__in=ids, kind=Media.Kind.IMAGE) if m.file}


def _build_site(request, doc: dict) -> dict:
    cfg = SiteSettings.load()
    a = doc["appearance"]
    urls = _media_urls([a["logo_media"], a["favicon_media"]])
    logo = urls.get(a["logo_media"]) or (cfg.logo.url if cfg.logo else "")
    favicon = urls.get(a["favicon_media"]) or (cfg.favicon.url if cfg.favicon else "")
    return {
        "name": cfg.site_name,
        "tagline": cfg.tagline,
        "description": cfg.description,
        "logo_url": logo,
        "favicon_url": favicon,
        "share_image_url": cfg.default_share_image.url if cfg.default_share_image else "",
        "email": cfg.admin_email,
        "phone": cfg.contact_phone,
        "address": cfg.address,
        "footer_text": cfg.footer_text,
        "home_url": reverse("public:home"),
        "search_url": reverse("public:search"),
    }


def _is_active(url: str, path: str) -> bool:
    if url == "/":
        return path == "/"
    return path == url or path.startswith(url.rstrip("/") + "/")


def _build_menus(doc: dict, path: str) -> dict:
    items = [i for loc in doc["menus"].values() for i in loc]
    page_ids = {i["target"] for i in items if i["kind"] == "page"}
    cat_ids = {i["target"] for i in items if i["kind"] == "category"}
    pages = {p.pk: p for p in Page.objects.filter(pk__in=page_ids, status="published")} if page_ids else {}
    cats = {c.pk: c for c in Category.objects.filter(pk__in=cat_ids)} if cat_ids else {}
    result = {}
    for location, entries in doc["menus"].items():
        links = []
        for e in entries:
            kind = e["kind"]
            if kind == "home":
                url = reverse("public:home")
            elif kind == "blog":
                url = reverse("public:post_list")
            elif kind == "page":
                target = pages.get(e["target"])
                url = page_url(target) if target else ""
            elif kind == "category":
                target = cats.get(e["target"])
                url = category_url(target) if target else ""
            else:
                url = e["url"]
            if not url:
                continue  # هدف حذف یا منتشرنشده؛ آیتم نمایش داده نمی‌شود (پیکربندی دست‌نخورده می‌ماند)
            internal = url.startswith("/")
            links.append(Link(label=e["label"], url=url, active=internal and _is_active(url, path), new_tab=e["new_tab"]))
        result[location] = tuple(links)
    return result


def _build_viewer(request, edit_url: str = ""):
    if not preview.has_session_cookie(request):
        return None  # بازدیدکننده ناشناس: نشست را لمس نمی‌کنیم (کش عمومی و Vary سالم می‌ماند)
    user = request.user
    if not user.is_authenticated or not user.has_cap("dashboard.view"):
        return None
    return {
        "name": user.display_name,
        "panel_url": reverse("dashboard:home"),
        "edit_url": edit_url,
        "customize_url": reverse("themes:customize") if user.has_cap("themes.manage") else "",
    }


def base_context(request, resolved: Resolved, *, embedded: bool = False, edit_url: str = "") -> dict:
    doc = resolved.doc
    site = _build_site(request, doc)
    header, footer = doc["header"], doc["footer"]
    cta = Link(label=header["cta_label"], url=header["cta_url"]) if header["cta_url"] else None
    return {
        "site": site,
        "theme": {"slug": resolved.theme.slug, "name": resolved.theme.display_name, "version": resolved.theme.version},
        "theme_vars": _theme_vars(doc["appearance"]),
        "header": {"show_tagline": header["show_tagline"], "sticky": header["sticky"], "show_search": header["show_search"], "cta": cta},
        "footer": {
            "text": footer["text"] or site["footer_text"],
            "show_menu": footer["show_menu"],
            "show_contact": footer["show_contact"],
            "social": tuple(Link(label=s["label"], url=s["url"], new_tab=True) for s in footer["social"]),
        },
        "menus": _build_menus(doc, request.path),
        "feeds": feed_links(request, site_name=site["name"]),
        "viewer": _build_viewer(request, edit_url),
        "is_preview": resolved.is_preview,
        "embedded": embedded,
    }


# ------------------------------------------------------------------ رندر نهایی
def _unavailable() -> HttpResponse:
    return HttpResponse(
        "<!doctype html><html lang=\"fa\" dir=\"rtl\"><meta charset=\"utf-8\"><title>سایت در دسترس نیست</title>"
        "<p>قالب سایت در دسترس نیست. لطفاً بعداً دوباره تلاش کنید.</p></html>",
        status=503,
        content_type="text/html; charset=utf-8",
    )


def _apply_headers(response, request, resolved: Resolved, *, embedded: bool, status: int):
    response["Content-Security-Policy"] = CSP
    response["X-Content-Type-Options"] = "nosniff"
    if embedded:
        response["X-Frame-Options"] = "SAMEORIGIN"
    personalised = resolved.is_preview or embedded or preview.has_session_cookie(request)
    if personalised:
        response["Cache-Control"] = "private, no-store"
    elif status == 200 and settings.PUBLIC_CACHE_SECONDS > 0:
        response["Cache-Control"] = f"public, max-age={settings.PUBLIC_CACHE_SECONDS}"
    else:
        response["Cache-Control"] = "public, max-age=0, must-revalidate"
    if personalised:
        patch_vary_headers(response, ["Cookie"])
    return response


def render_page(request, template: str, context: dict, *, status: int = 200, embedded: bool = False,
                force_draft: bool = False, edit_url: str = "", seo_factory=None) -> HttpResponse:
    """``seo_factory(site)`` دادهٔ سئو را با اطلاعات سایت می‌سازد (تا هر ویو دوباره کوئری نزند).

    ``context`` یا یک dict است یا تابعی ``(resolved, base_ctx) -> dict`` برای ویوهایی (مثل صفحه اصلی) که برای ساخت
    داده‌هایشان به قالب/پیکربندی انتخاب‌شده نیاز دارند؛ این‌طور قالب و تنظیمات فقط یک‌بار خوانده می‌شوند.
    """
    try:
        resolved = resolve(request, force_draft=force_draft)
    except ThemeUnavailable:
        return _unavailable()
    base = base_context(request, resolved, embedded=embedded, edit_url=edit_url)
    extra = context(resolved, base) if callable(context) else context
    ctx = {**base, **extra}
    ctx["seo"] = seo_factory(ctx["site"]) if seo_factory else {"title": ctx["site"]["name"]}
    try:
        html = engine.render(resolved.theme, template, ctx)
    except (TemplateDoesNotExist, TemplateSyntaxError):
        default = registry.get_theme(DEFAULT_THEME)
        if resolved.theme.slug == DEFAULT_THEME or default is None or not default.valid:
            raise
        logger.exception("رندر قالب «%s» ناموفق بود؛ قالب پیش‌فرض جایگزین شد.", resolved.theme.slug)
        ctx["theme"] = {"slug": default.slug, "name": default.display_name, "version": default.version}
        html = engine.render(default, template, ctx)
    response = HttpResponse(html, status=status, content_type="text/html; charset=utf-8")
    return _apply_headers(response, request, resolved, embedded=embedded, status=status)
