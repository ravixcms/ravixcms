"""محتوای نمونهٔ بعد از نصب: یک نوشته، برگهٔ «درباره ما» و برگهٔ «تماس با ما».

همه با ``is_sample=True`` ساخته می‌شوند و با اولین ویرایش دستی دیگر نمونه حساب نمی‌شوند؛
``delete_samples`` فقط نمونه‌های ویرایش‌نشده را پاک می‌کند.
"""
from django.utils.translation import gettext, gettext_lazy
from django.db import transaction

from .models import Page, Post, PublishStatus

POST_TITLE = gettext_lazy("Welcome to Ravix")
POST_BODY = gettext_lazy("Hello and welcome! 👋\n\nThis is a **sample post** so you can see what a post looks like on your site. You can edit it from the panel or delete it.\n\n## What’s next?\n\n- Write your own first post in the **Posts** section.\n- Choose the theme and colors in **Appearance**.\n- Complete the logo and site name in **Settings**.\n\n> Start writing; the rest is simpler than you think.\n")
ABOUT_BODY = gettext_lazy("This is a sample page. Write about yourself, your business or the purpose of your site here.\n\n## Who are we?\n\nA few sentences about the team or how it all started.\n\n## What do we do?\n\nThe services, products or topics you read about on this site.\n")
CONTACT_BODY = gettext_lazy("This is a sample page. Write how people can reach you here.\n\n- Email: `you@example.com`\n- Phone: 555-0100\n- Address: city, street, number\n\nAlso write your response hours here so visitors know when to expect an answer.\n")


def has_samples() -> bool:
    return Post.objects.filter(is_sample=True).exists() or Page.objects.filter(is_sample=True).exists()


@transaction.atomic
def create_samples(author=None) -> dict:
    """نمونه‌ها را می‌سازد (idempotent: اگر نمونه‌ای هست، دوباره نمی‌سازد). منوی سربرگ را هم با دو برگه تکمیل می‌کند."""
    if has_samples():
        return {"created": False}
    post = Post.objects.create(title=POST_TITLE, body=POST_BODY, excerpt=gettext("A sample post to get to know Ravix."),
                               status=PublishStatus.PUBLISHED, author=author, is_sample=True)
    about = Page.objects.create(title=gettext("About us"), slug="about", body=ABOUT_BODY, status=PublishStatus.PUBLISHED,
                                author=author, is_sample=True)
    contact = Page.objects.create(title=gettext("Contact us"), slug="contact", body=CONTACT_BODY, status=PublishStatus.PUBLISHED,
                                  author=author, is_sample=True)
    _add_menu(about, contact)
    return {"created": True, "post": post.pk, "pages": [about.pk, contact.pk]}


def _add_menu(*pages) -> None:
    from apps.themes.models import ThemeConfig

    cfg = ThemeConfig.load()
    cfg = ThemeConfig.objects.select_for_update().get(pk=1)
    doc = dict(cfg.published) if isinstance(cfg.published, dict) else {}
    menus = dict(doc.get("menus") or {})
    header = list(menus.get("header") or [])
    if not header:
        return
    for page in pages:
        header.append({"label": page.title, "kind": "page", "target": page.pk, "url": "", "new_tab": False})
    menus["header"] = header
    doc["menus"] = menus
    cfg.published = doc
    cfg.save(update_fields=["published"])


@transaction.atomic
def delete_samples() -> dict:
    """فقط نمونه‌های ویرایش‌نشده را حذف می‌کند؛ ورودی‌های منو به‌خاطر حذف هدف خودبه‌خود پنهان می‌شوند."""
    posts, _ = Post.objects.filter(is_sample=True).delete()
    pages, _ = Page.objects.filter(is_sample=True).delete()
    return {"posts": posts, "pages": pages}
