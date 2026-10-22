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


def clip(text: str, length: int = 160) -> str:
    return Truncator(plain_text(text or "")).chars(length)


def make_seo(request, site: dict, *, title: str = "", description: str = "", path: str = "", robots: str = "",
             og_type: str = "website", og_image: str = "", jsonld=None, prev: str = "", next_: str = "",
             published_time: str = "", modified_time: str = "") -> dict:
    """``og_image`` خالی ← تصویر پیش‌فرض اشتراک‌گذاری سایت (در صورت تعریف)."""
    image = absolute_url(request, og_image or site.get("share_image_url", ""))
    full_title = f"{title} | {site['name']}" if title else (f"{site['name']} | {site['tagline']}" if site["tagline"] else site["name"])
    return {
        "title": full_title,
        "description": clip(description or site["description"] or site["tagline"]),
        "canonical": absolute_url(request, path or request.path),
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
