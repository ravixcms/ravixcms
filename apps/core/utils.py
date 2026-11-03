import os
import uuid

from django.utils.deconstruct import deconstructible
from django.utils.text import slugify


@deconstructible
class RandomUploadPath:
    """upload_to با نام تصادفی: نام و پسوند ارسالی کاربر هرگز روی دیسک نمی‌آید (فقط پسوند اعتبارسنجی‌شده)."""

    def __init__(self, folder: str):
        self.folder = folder.strip("/")

    def __call__(self, instance, filename):
        ext = os.path.splitext(filename)[1].lstrip(".").lower()[:5]
        return f"{self.folder}/{uuid.uuid4().hex}.{ext}" if ext else f"{self.folder}/{uuid.uuid4().hex}"

    def __eq__(self, other):
        return isinstance(other, RandomUploadPath) and other.folder == self.folder


# نامک‌هایی که صفحه ثابت نمی‌تواند داشته باشد (با مسیرهای سیستمی سایت تداخل دارند)
RESERVED_PAGE_SLUGS = frozenset(
    {"panel", "accounts", "api", "django-admin", "blog", "category", "tag", "search", "static", "media",
     "sitemap.xml", "robots.txt", "favicon.ico", "admin", "login", "logout", "ext", "shop", "healthz", "registry", "feed",
     "install", "i18n", "jsi18n", "mcp", "oauth", ".well-known",
     # language codes are public URL prefixes (/en/, /de/ …)
     "fa", "en", "ar", "tr", "es", "fr", "de", "ru", "pt-br", "zh-hans"}
)


def unique_slug(model, value: str, *, instance=None, field: str = "slug", fallback: str = "item", max_length: int = 200,
                reserved=frozenset()) -> str:
    """نامک یکتا می‌سازد (پشتیبانی از حروف فارسی). در صورت تکرار، پسوند -۲، -۳ و ... اضافه می‌شود."""
    base = slugify(value or "", allow_unicode=True)[:max_length].strip("-_") or fallback
    slug, n = base, 2
    qs = model.objects.all()
    if instance is not None and instance.pk:
        qs = qs.exclude(pk=instance.pk)
    while slug in reserved or qs.filter(**{field: slug}).exists():
        suffix = f"-{n}"
        slug = f"{base[: max_length - len(suffix)]}{suffix}"
        n += 1
    return slug
