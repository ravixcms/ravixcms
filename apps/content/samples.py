"""محتوای نمونهٔ بعد از نصب: یک نوشته، برگهٔ «درباره ما» و برگهٔ «تماس با ما».

همه با ``is_sample=True`` ساخته می‌شوند و با اولین ویرایش دستی دیگر نمونه حساب نمی‌شوند؛
``delete_samples`` فقط نمونه‌های ویرایش‌نشده را پاک می‌کند.
"""
from django.db import transaction

from .models import Page, Post, PublishStatus

POST_TITLE = "به راویکس خوش آمدید"
POST_BODY = """سلام و خوش آمدید! 👋

این **نوشتهٔ نمونه** است تا ببینید یک نوشته در سایت شما چه شکلی دارد. می‌توانید آن را از پنل ویرایش کنید یا حذف کنید.

## قدم بعدی چیست؟

- از بخش **نوشته‌ها** اولین نوشتهٔ خودتان را بنویسید.
- از **ظاهر** قالب و رنگ‌ها را انتخاب کنید.
- از **تنظیمات** لوگو و نام سایت را کامل کنید.

> نوشتن را شروع کنید؛ بقیهٔ کارها ساده‌تر از چیزی است که فکر می‌کنید.
"""
ABOUT_BODY = """این برگهٔ نمونه است. اینجا دربارهٔ خودتان، کسب‌وکار یا هدف سایتتان بنویسید.

## ما که هستیم؟

چند جمله دربارهٔ تیم یا ماجرای شروع کار.

## چه می‌کنیم؟

خدمات، محصولات یا موضوعاتی که در این سایت می‌خوانید.
"""
CONTACT_BODY = """این برگهٔ نمونه است. راه‌های ارتباط با خودتان را اینجا بنویسید.

- ایمیل: `you@example.com`
- تلفن: ۰۲۱-۰۰۰۰۰۰۰۰
- نشانی: شهر، خیابان، پلاک

ساعت پاسخگویی را هم اینجا بنویسید تا مخاطبان بدانند چه زمانی منتظر پاسخ باشند.
"""


def has_samples() -> bool:
    return Post.objects.filter(is_sample=True).exists() or Page.objects.filter(is_sample=True).exists()


@transaction.atomic
def create_samples(author=None) -> dict:
    """نمونه‌ها را می‌سازد (idempotent: اگر نمونه‌ای هست، دوباره نمی‌سازد). منوی سربرگ را هم با دو برگه تکمیل می‌کند."""
    if has_samples():
        return {"created": False}
    post = Post.objects.create(title=POST_TITLE, body=POST_BODY, excerpt="نوشتهٔ نمونه برای آشنایی با راویکس.",
                               status=PublishStatus.PUBLISHED, author=author, is_sample=True)
    about = Page.objects.create(title="درباره ما", slug="about", body=ABOUT_BODY, status=PublishStatus.PUBLISHED,
                                author=author, is_sample=True)
    contact = Page.objects.create(title="تماس با ما", slug="contact", body=CONTACT_BODY, status=PublishStatus.PUBLISHED,
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
