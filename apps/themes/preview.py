"""حالت پیش‌نمایش: پرچمی در نشست مدیر که باعث می‌شود سایت عمومی «پیش‌نویس» را نشان دهد، نه نسخه منتشرشده."""
from django.conf import settings

SESSION_KEY = "ravix_preview"
CAPABILITY = "themes.manage"


def can_preview(request) -> bool:
    user = getattr(request, "user", None)
    return bool(user is not None and user.is_authenticated and user.has_cap(CAPABILITY))


def has_session_cookie(request) -> bool:
    return settings.SESSION_COOKIE_NAME in request.COOKIES


def is_active(request) -> bool:
    # بازدیدکننده بی‌نشست: بدون لمس نشست (تا هدر Vary: Cookie و کش عمومی خراب نشود)
    if not has_session_cookie(request) or not can_preview(request):
        return False
    return bool(request.session.get(SESSION_KEY))


def start(request) -> None:
    request.session[SESSION_KEY] = True


def stop(request) -> None:
    request.session.pop(SESSION_KEY, None)
