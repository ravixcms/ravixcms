"""ساخت دادهٔ سئو (عنوان، توضیح، canonical، robots، JSON-LD) برای هر صفحه عمومی."""
from django.conf import settings
from django.utils.encoding import iri_to_uri
from django.utils.text import Truncator

from .formatting import plain_text


def absolute_url(request, path: str) -> str:
    if not path:
        return ""
    if path.startswith(("http://", "https://")):
        return path
    base = settings.SITE_URL or f"{request.scheme}://{request.get_host()}"
    return base + iri_to_uri(path)


def hreflang(code: str) -> str:
    """Django language code → BCP-47 hreflang value: "pt-br" → "pt-BR", "zh-hans" → "zh-Hans"."""
    parts = code.split("-")
    out = [parts[0].lower()]
    for part in parts[1:]:
        out.append(part.title() if len(part) == 4 else part.upper())
    return "-".join(out)


def alternate_links(request, path: str) -> list[tuple[str, str]]:
    """``[(hreflang, absolute_url), …, ("x-default", url)]`` for a localized public path (every UI language, plus
    x-default = the site's default language). Empty when ``path`` carries no language prefix."""
    from apps.core.regional import language_choices, load_site_settings, regional_for, swap_language_prefix

    if not path or swap_language_prefix(path, "fa") is None:
        return []
    links = [(hreflang(code), absolute_url(request, swap_language_prefix(path, code))) for code, _name in language_choices()]
    default = regional_for(load_site_settings(request))[0]
    links.append(("x-default", absolute_url(request, swap_language_prefix(path, default))))
    return links


def clip(text: str, length: int = 160) -> str:
    return Truncator(plain_text(text or "")).chars(length)


def make_seo(request, site: dict, *, title: str = "", description: str = "", path: str = "", robots: str = "",
             og_type: str = "website", og_image: str = "", jsonld=None, prev: str = "", next_: str = "",
             published_time: str = "", modified_time: str = "", alternates: bool = True) -> dict:
    """``og_image`` خالی ← تصویر پیش‌فرض اشتراک‌گذاری سایت (در صورت تعریف)."""
    image = absolute_url(request, og_image or site.get("share_image_url", ""))
    canonical_path = path or request.path
    full_title = f"{title} | {site['name']}" if title else (f"{site['name']} | {site['tagline']}" if site["tagline"] else site["name"])
    return {
        "title": full_title,
        "description": clip(description or site["description"] or site["tagline"]),
        "canonical": absolute_url(request, canonical_path),
        "alternates": [] if ("noindex" in robots or not alternates) else alternate_links(request, canonical_path),
        "robots": robots,
        "og_type": og_type,
        "og_image": image,
        "published_time": published_time if og_type == "article" else "",
        "modified_time": modified_time if og_type == "article" else "",
        "jsonld": jsonld or [],
        "prev": absolute_url(request, prev),
        "next": absolute_url(request, next_),
    }


def finish_seo(request, data: dict) -> dict:
    """پس از hookهای افزونه: og_image نسبی (مثلاً از افزونه) به نشانی مطلق تبدیل می‌شود؛ شبکه‌های اجتماعی نشانی نسبی را نمی‌پذیرند."""
    if data.get("og_image"):
        data["og_image"] = absolute_url(request, data["og_image"])
    return data
