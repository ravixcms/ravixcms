from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .models import AuditLog
from .services import log_event


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    log_event(request, AuditLog.Action.LOGIN, message="ورود به سامانه", object_type="نشست", actor=user)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user is not None:
        log_event(request, AuditLog.Action.LOGOUT, message="خروج از سامانه", object_type="نشست", actor=user)


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    attempted = str(credentials.get("username", ""))[:100]  # هرگز رمز عبور ثبت نمی‌شود
    log_event(request, AuditLog.Action.LOGIN_FAILED, message=f"تلاش ناموفق برای ورود با «{attempted}»", object_type="نشست")
