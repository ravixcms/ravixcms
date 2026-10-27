"""
مدل‌های داده‌ی ساده‌ای که به قالب‌های سایت داده می‌شوند (به‌جای اشیای ORM).

این فایل «مرز امنیتی» قالب‌هاست: قالب فقط فیلدهای اعلام‌شده اینجا را می‌بیند و راهی به ``post.author.password``،
``request.session``، ``settings`` یا کوئری‌های دلخواه ندارد. هر فیلد جدید باید در مستندات قرارداد قالب هم ثبت شود.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Link:
    label: str
    url: str
    active: bool = False
    new_tab: bool = False


@dataclass(frozen=True)
class LanguageOption:
    """A language that has published posts: its code, native name, the address of its blog and how many posts it has."""
    code: str
    name: str
    url: str
    post_count: int = 0


@dataclass(frozen=True)
class FeedLink:
    """پیوند autodiscovery فید برای <head> (``rel="alternate"``)؛ url مطلق است."""

    title: str
    url: str
    type: str


@dataclass(frozen=True)
class Crumb:
    label: str
    url: str


@dataclass(frozen=True)
class CategoryItem:
    name: str
    slug: str
    url: str
    description: str = ""
    post_count: int = 0


@dataclass(frozen=True)
class TagItem:
    name: str
    url: str


@dataclass(frozen=True)
class PostItem:
    title: str
    url: str
    excerpt: str
    image_url: str
    author_name: str
    date_display: str
    date_iso: str
    categories: tuple = field(default_factory=tuple)
    tags: tuple = field(default_factory=tuple)
    reading_time: str = ""
    body_html: str = ""  # فقط در صفحه خود نوشته پر می‌شود؛ خروجی پاک‌سازی‌شده (nh3)


@dataclass(frozen=True)
class PageItem:
    title: str
    url: str
    body_html: str
    date_display: str
    date_iso: str
    excerpt: str = ""
    builder: bool = False  # true: body_html خروجی صفحه‌ساز است (HTML امن + <style> خودش)؛ قالب می‌تواند تمام‌عرض نشانش دهد
    show_title: bool = True  # فقط برای صفحات صفحه‌ساز: عنوان صفحه بالای محتوا دیده شود؟


@dataclass(frozen=True)
class PageLink:
    number: int  # صفر یعنی «...»
    url: str
    current: bool = False


@dataclass(frozen=True)
class Pagination:
    page: int
    num_pages: int
    total: int
    has_prev: bool
    has_next: bool
    prev_url: str
    next_url: str
    pages: tuple = field(default_factory=tuple)


@dataclass(frozen=True)
class SectionButton:
    label: str
    url: str


@dataclass(frozen=True)
class FeatureItem:
    """یک مورد از بخش «ویژگی‌ها». ``icon`` یکی از نام‌های ثابت (``{% feature_icon item.icon %}``) یا خالی است."""
    title: str = ""
    icon: str = ""
    text: str = ""


@dataclass(frozen=True)
class Section:
    """یک بخش صفحهٔ اصلی. ``button_label``/``button_url`` همان دکمهٔ نخست ``buttons`` است (سازگاری با قالب‌های قدیمی؛
    hero تا ۳ دکمه دارد). ``body_html`` Markdown پاک‌سازی‌شده است. ``items``: فقط در بخش ``features``."""
    type: str
    title: str = ""
    subtitle: str = ""
    body_html: str = ""
    button_label: str = ""
    button_url: str = ""
    buttons: tuple = field(default_factory=tuple)
    items: tuple = field(default_factory=tuple)
    posts: tuple = field(default_factory=tuple)
    categories: tuple = field(default_factory=tuple)
    page: Optional[PageItem] = None


@dataclass(frozen=True)
class CommentItem:
    """یک دیدگاه تأییدشده. ایمیل هرگز در DTO نیست. ``replies`` پاسخ‌های تأییدشده (تودرتو تا عمق مجاز)."""
    id: int
    author_name: str
    date_display: str
    date_iso: str
    body_html: str  # پاک‌سازی‌شده با nh3 هنگام ثبت
    depth: int = 0
    reply_url: str = ""  # خالی = پاسخ به این دیدگاه ممکن نیست (عمق مجاز تمام شده یا دیدگاه بسته است)
    replies: tuple = field(default_factory=tuple)


@dataclass(frozen=True)
class CommentsSection:
    """متغیر ``comments`` در ``post_detail.html``. فرم باید با ``method="post"`` به ``action_url`` ارسال شود و فیلد مخفی
    ``honeypot`` (نام: ``honeypot_name``) را که با CSS پنهان است، حتماً داشته باشد. توکن CSRF لازم نیست."""
    count: int
    items: tuple = field(default_factory=tuple)
    open: bool = True
    action_url: str = ""
    honeypot_name: str = "website"
    notice: str = ""  # "" | "pending" (در انتظار تأیید) | "ok" (منتشر شد)
    errors: dict = field(default_factory=dict)  # name/email/body/form ← پیام
    values: dict = field(default_factory=dict)  # name/email/body برای پرکردن دوباره فرم
    reply_to_id: int = 0
    reply_to_name: str = ""
    cancel_reply_url: str = ""
    max_length: int = 2000
