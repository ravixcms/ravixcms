"""راهنمای گام‌به‌گام شروع کار در داشبورد (لوگو، اولین نوشته، انتخاب قالب).

وضعیت هر گام از روی داده‌های واقعی تشخیص داده می‌شود؛ پس هر کاری که مدیر از هر راهی انجام دهد، گام تیک می‌خورد.
"""
from dataclasses import dataclass

from django.urls import reverse

from apps.content.models import Post, PublishStatus
from apps.core.models import SiteSettings
from apps.themes.models import ThemeConfig


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    hint: str
    url: str
    button: str
    done: bool


def steps(site: SiteSettings) -> list[Step]:
    first_post = Post.objects.filter(is_sample=False, status__in=[PublishStatus.PUBLISHED, PublishStatus.SCHEDULED]).exists()
    cfg = ThemeConfig.load()
    theme_done = cfg.published_at is not None
    return [
        Step("logo", "لوگوی سایت را بگذارید", "لوگو در سربرگ سایت و صفحهٔ ورود نمایش داده می‌شود.",
             reverse("core:site_settings"), "بارگذاری لوگو", bool(site.logo)),
        Step("post", "اولین نوشتهٔ خودتان را منتشر کنید", "نوشتهٔ نمونه را می‌توانید پاک کنید یا جایش بنویسید.",
             reverse("content:post_create"), "نوشتن نوشته", first_post),
        Step("theme", "قالب و رنگ سایت را انتخاب کنید", "قالب را عوض کنید یا رنگ‌ها و منوها را شخصی‌سازی کنید.",
             reverse("themes:list"), "انتخاب قالب", theme_done),
    ]


def context(site: SiteSettings) -> dict | None:
    """None یعنی کارت نمایش داده نشود (بسته‌شده یا همهٔ گام‌ها انجام شده)."""
    if site.onboarding_dismissed:
        return None
    items = steps(site)
    done = sum(s.done for s in items)
    if done == len(items):
        return None
    return {"steps": items, "done": done, "total": len(items), "percent": round(done * 100 / len(items))}
