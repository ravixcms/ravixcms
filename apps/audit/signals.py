from django.utils.translation import gettext
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .models import AuditLog
from .services import log_event


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    log_event(request, AuditLog.Action.LOGIN, message=gettext("Signed in"), object_type=gettext("Session"), actor=user)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user is not None:
        log_event(request, AuditLog.Action.LOGOUT, message=gettext("Signed out"), object_type=gettext("Session"), actor=user)


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    attempted = str(credentials.get("username", ""))[:100]  # هرگز رمز عبور ثبت نمی‌شود
    log_event(request, AuditLog.Action.LOGIN_FAILED, message=(gettext("Failed sign-in attempt with “%(attempted)s”") % {"attempted": attempted}), object_type=gettext("Session"))
