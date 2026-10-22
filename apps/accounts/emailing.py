"""ایمیل‌های حساب کاربری: تأیید ایمیل (پیوند امضاشده، دارای انقضا، یک‌بارمصرف) و ابزارهای مشترک.

بازیابی رمز از ``django.contrib.auth.views`` می‌آید (توکن آن به هش رمز وابسته است، پس بعد از استفاده باطل می‌شود).
هیچ پاسخ HTTP نباید فاش کند که یک ایمیل در سیستم هست یا نه؛ پس خطای ارسال فقط لاگ می‌شود.
"""
import hashlib
import logging
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.urls import reverse

from . import throttle

logger = logging.getLogger(__name__)
User = get_user_model()
VERIFY_SALT = "ravin.accounts.email-verify"


def _email_fingerprint(email: str) -> str:
    return hashlib.sha256((email or "").strip().lower().encode()).hexdigest()[:20]


def make_verify_token(user) -> str:
    """توکن امضاشده: شناسهٔ کاربر + اثرانگشت ایمیل فعلی (نه خود ایمیل). با تغییر ایمیل یا پس از تأیید، پذیرفته نمی‌شود."""
    return signing.dumps({"u": user.pk, "h": _email_fingerprint(user.email)}, salt=VERIFY_SALT, compress=False)


def read_verify_token(token: str):
    """(user, state) با state یکی از: ``ok`` | ``done`` (قبلاً تأیید شده) | ``invalid`` (امضای نادرست/منقضی/ایمیل عوض‌شده)."""
    try:
        data = signing.loads(token, salt=VERIFY_SALT, max_age=settings.RAVIN_EMAIL_VERIFY_MAX_AGE)
        user = User.objects.filter(pk=data["u"], is_active=True).first()
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return None, "invalid"
    if user is None or _email_fingerprint(user.email) != data.get("h"):
        return None, "invalid"
    return user, ("done" if user.email_verified_at else "ok")


def absolute_url(request, path: str) -> str:
    """نشانی مطلق؛ اگر RAVIN_SITE_URL تنظیم شده باشد از آن (نه از سرآیند Host) ساخته می‌شود."""
    if settings.SITE_URL:
        return settings.SITE_URL + path
    return request.build_absolute_uri(path)


def site_domain_args() -> dict:
    """آرگومان‌های ``PasswordResetForm.save`` برای استفاده از RAVIN_SITE_URL."""
    if not settings.SITE_URL:
        return {}
    parts = urlsplit(settings.SITE_URL)
    return {"domain_override": parts.netloc, "use_https": parts.scheme == "https"}


def send_verification_email(request, user) -> bool:
    """ایمیل تأیید می‌فرستد. False = ارسال نشد (محدودیت نرخ یا خطای SMTP)؛ هرگز استثنا نمی‌دهد."""
    if user.email_verified_at or not user.email:
        return False
    if not throttle.allow_send(request, user.email, "verify"):
        return False
    link = absolute_url(request, reverse("accounts:verify_email", args=[make_verify_token(user)]))
    ctx = {"user": user, "link": link, "hours": settings.RAVIN_EMAIL_VERIFY_MAX_AGE // 3600}
    subject = " ".join(render_to_string("accounts/email/verify_subject.txt", ctx).split())
    body = render_to_string("accounts/email/verify.txt", ctx)
    try:
        EmailMessage(subject, body, to=[user.email]).send(fail_silently=False)
    except Exception:  # noqa: BLE001 - SMTP خراب نباید ثبت‌نام/ورود را بشکند یا چیزی فاش کند
        logger.exception("ارسال ایمیل تأیید برای کاربر %s ناموفق بود.", user.pk)
        return False
    return True
