"""راهنمای گام‌به‌گام شروع کار در داشبورد (لوگو، اولین نوشته، انتخاب قالب).

وضعیت هر گام از روی داده‌های واقعی تشخیص داده می‌شود؛ پس هر کاری که مدیر از هر راهی انجام دهد، گام تیک می‌خورد.
"""
from django.utils.translation import gettext
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
        Step("logo", gettext("Add your site logo"), gettext("The logo is shown in the site header and on the sign-in page."),
             reverse("core:site_settings"), gettext("Upload logo"), bool(site.logo)),
        Step("post", gettext("Publish your first post"), gettext("You can delete the sample post or write over it."),
             reverse("content:post_create"), gettext("Write a post"), first_post),
        Step("theme", gettext("Choose the site theme and colors"), gettext("Change the theme or customize colors and menus."),
             reverse("themes:list"), gettext("Choose a theme"), theme_done),
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
