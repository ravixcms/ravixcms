import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.urls import reverse

from apps.core.models import SiteSettings

logger = logging.getLogger(__name__)


def notify_admin(request, comment) -> bool:
    """ایمیل اطلاع به «ایمیل مدیر» سایت. خطای ارسال هرگز دیدگاه را رد نمی‌کند (فقط لاگ). متن دیدگاه کوتاه و بدون HTML می‌آید."""
    from apps.accounts.emailing import absolute_url
    from .services import plain_text

    cfg = SiteSettings.load()
    if not (cfg.comments_notify_admin and cfg.admin_email):
        return False
    state = "منتظر تأیید شماست" if comment.status == comment.Status.PENDING else "منتشر شد"
    subject = f"دیدگاه جدید در «{cfg.site_name}»"[:150].replace("\n", " ")
    lines = [
        f"یک دیدگاه جدید روی نوشتهٔ «{comment.post.title}» ثبت شد و {state}.",
        "",
        f"نام: {comment.author_name}",
        f"متن: {plain_text(comment.body_html)[:300]}",
        "",
        "مدیریت دیدگاه‌ها: " + absolute_url(request, reverse("comments:list") + f"?status={comment.status}"),
    ]
    try:
        EmailMessage(subject, "\n".join(lines), settings.DEFAULT_FROM_EMAIL, [cfg.admin_email]).send()
    except Exception:  # noqa: BLE001
        logger.exception("ارسال ایمیل اطلاع دیدگاه جدید شکست خورد.")
        return False
    return True
