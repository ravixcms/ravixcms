from apps.core.clilang import cli_gettext
from django.utils.translation import gettext
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
    state = gettext("is awaiting your approval") if comment.status == comment.Status.PENDING else gettext("has been published")
    subject = (gettext("New comment on “%(site_name)s”") % {"site_name": cfg.site_name})[:150].replace("\n", " ")
    lines = [
        (gettext("A new comment was posted on the post “%(title)s” and %(state)s.") % {"title": comment.post.title, "state": state}),
        "",
        (gettext("Name: %(author_name)s") % {"author_name": comment.author_name}),
        (gettext("Text: %(value)s") % {"value": plain_text(comment.body_html)[:300]}),
        "",
        (gettext("Manage comments: %(absolute_url)s") % {"absolute_url": absolute_url(request, reverse("comments:list") + f"?status={comment.status}")}),
    ]
    try:
        EmailMessage(subject, "\n".join(lines), settings.DEFAULT_FROM_EMAIL, [cfg.admin_email]).send()
    except Exception:  # noqa: BLE001
        logger.exception(cli_gettext("Sending the new-comment notification email failed."))
        return False
    return True
