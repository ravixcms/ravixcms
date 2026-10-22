"""منطق دیدگاه: پاک‌سازی، ضداسپم (honeypot خارج از اینجا، نرخ، کلمات مسدود، تکراری)، ثبت و تغییر وضعیت."""
import hashlib
import logging
import re

import nh3
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils.html import strip_tags

from apps.accounts import throttle
from apps.core.models import SiteSettings

from .models import Comment

logger = logging.getLogger(__name__)

ALLOWED_TAGS = {"a", "strong", "b", "em", "i", "code", "blockquote", "p", "br"}
ALLOWED_ATTRIBUTES = {"a": {"href"}}
URL_SCHEMES = {"http", "https"}
HONEYPOT = "website"


class CommentRejected(Exception):
    """رد دیدگاه با خطای قابل‌نمایش به بازدیدکننده. ``errors``: dict فیلد ← پیام (کلید ``form`` برای خطای کلی)."""

    def __init__(self, errors: dict, status: int = 400):
        super().__init__(errors)
        self.errors = errors
        self.http_status = status


def clean_body(text: str) -> str:
    """HTML مجاز (لینک http/https، پررنگ، مورب، کد، نقل‌قول) را نگه می‌دارد، بقیه را حذف می‌کند؛ خط‌های جدید پس از پاک‌سازی به <br> تبدیل می‌شوند."""
    cleaned = nh3.clean(text or "", tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES, url_schemes=URL_SCHEMES,
                        link_rel="noopener noreferrer nofollow ugc", strip_comments=True)
    cleaned = re.sub(r"\r\n?|\n", "<br>", cleaned.strip())
    return re.sub(r"(<br>){3,}", "<br><br>", cleaned)


def plain_text(html: str) -> str:
    return " ".join(strip_tags(html or "").split())


def blocked_words(settings_obj=None) -> list[str]:
    cfg = settings_obj or SiteSettings.load()
    return [w.strip().casefold() for w in (cfg.comments_blocked_words or "").splitlines() if w.strip()]


def contains_blocked(words: list[str], *parts: str) -> bool:
    haystack = " ".join(p or "" for p in parts).casefold()
    return any(w in haystack for w in words)


def rate_limited(request) -> bool:
    """هر تلاش (پذیرفته یا ردشده) شمرده می‌شود؛ True = از حد گذشته."""
    ip = throttle.client_ip(request)
    if ip is None:  # IP نامعلوم: محدودیت سراسری اعمال نمی‌شود (جلوگیری از قفل‌شدن همه با هم)
        return False
    key = f"comment-hit:{hashlib.sha256(ip.encode()).hexdigest()[:24]}"
    window = settings.RAVIX_COMMENT_WINDOW_SECONDS

    def hit(c):
        c.add(key, 0, window)
        try:
            return c.incr(key)
        except ValueError:
            c.set(key, 1, window)
            return 1

    return throttle._with_cache(hit) > settings.RAVIX_COMMENT_LIMIT_PER_IP


def validate_fields(name, email, body, parent_id, post):
    errors = {}
    name = " ".join((name or "").split())
    email = (email or "").strip()
    body = (body or "").strip()
    if not 2 <= len(name) <= 80:
        errors["name"] = "نام را وارد کنید (۲ تا ۸۰ نویسه)."
    try:
        validate_email(email)
    except ValidationError:
        errors["email"] = "ایمیل معتبر وارد کنید. ایمیل شما نمایش داده نمی‌شود."
    if len(body) > settings.RAVIX_COMMENT_MAX_LENGTH:
        errors["body"] = f"متن دیدگاه حداکثر {settings.RAVIX_COMMENT_MAX_LENGTH} نویسه می‌تواند باشد."
    elif len(plain_text(clean_body(body))) < 2:
        errors["body"] = "متن دیدگاه را بنویسید."
    parent = None
    if parent_id not in (None, ""):
        try:
            parent = Comment.objects.filter(pk=int(parent_id), post=post, status=Comment.Status.APPROVED).first()
        except (TypeError, ValueError):
            parent = None
        if parent is None or parent.depth >= settings.RAVIX_COMMENT_MAX_DEPTH:
            errors["form"] = "امکان پاسخ به این دیدگاه وجود ندارد."
    return name, email, body, parent, errors


def submit(request, post, data) -> Comment | None:
    """دیدگاه عمومی را ثبت می‌کند. ``None`` یعنی honeypot پر بوده (چیزی ذخیره نمی‌شود ولی به ربات موفق نشان داده می‌شود).
    در خطا ``CommentRejected`` پرتاب می‌شود."""
    from apps.plugins.hooks import apply_filter, run_action

    if (data.get(HONEYPOT) or "").strip():
        logger.info("honeypot دیدگاه پر شده بود؛ نادیده گرفته شد.")
        return None
    if rate_limited(request):
        raise CommentRejected({"form": "تعداد دیدگاه‌های ارسالی شما زیاد است؛ چند دقیقه بعد دوباره تلاش کنید."}, status=429)
    if not post.comments_open:
        raise CommentRejected({"form": "ارسال دیدگاه برای این نوشته بسته شده است."}, status=403)
    name, email, body, parent, errors = validate_fields(data.get("name"), data.get("email"), data.get("body"), data.get("parent"), post)
    if errors:
        raise CommentRejected(errors)
    if Comment.objects.filter(post=post, author_email__iexact=email, body=body).exclude(status=Comment.Status.TRASH).exists():
        raise CommentRejected({"form": "این دیدگاه قبلاً ثبت شده است."})
    cfg = SiteSettings.load()
    status = Comment.Status.PENDING if cfg.comments_require_approval else Comment.Status.APPROVED
    if contains_blocked(blocked_words(cfg), name, email, body):
        status = Comment.Status.SPAM
    payload = {"author_name": name, "author_email": email, "body": body, "status": status, "ip": throttle.client_ip(request),
               "parent_id": parent.pk if parent else None}
    status = apply_filter("comments.before_save", payload, post_id=post.pk, request=request)["status"]
    comment = Comment.objects.create(
        post=post, parent=parent, depth=(parent.depth + 1) if parent else 0, author_name=name, author_email=email, body=body,
        body_html=clean_body(body), status=status, ip_address=payload["ip"] or None,
    )
    run_action("comments.created", comment_id=comment.pk, post_id=post.pk, status=comment.status, request=request)
    if comment.status != Comment.Status.SPAM:
        from .notify import notify_admin

        notify_admin(request, comment)
    return comment


BULK_ACTIONS = {
    "approve": Comment.Status.APPROVED,
    "pending": Comment.Status.PENDING,
    "spam": Comment.Status.SPAM,
    "trash": Comment.Status.TRASH,
}


def change_status(request, queryset, new_status) -> int:
    """وضعیت را عوض می‌کند و برای هر دیدگاه واقعاً تغییرکرده hook ``comments.status_changed`` را اجرا می‌کند."""
    from apps.plugins.hooks import run_action

    changed = [(c.pk, c.status) for c in queryset.exclude(status=new_status).only("id", "status")]
    if not changed:
        return 0
    Comment.objects.filter(pk__in=[pk for pk, _ in changed]).update(status=new_status)
    for pk, old in changed:
        run_action("comments.status_changed", comment_id=pk, old=old, new=new_status, request=request)
    return len(changed)
