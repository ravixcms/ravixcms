from .models import AuditLog


def _client_ip(request):
    if request is None:
        return None
    from apps.core.clientip import client_ip  # تنها تابع تعیین IP؛ سربرگ‌ها فقط از پروکسی مورداعتماد
    return client_ip(request)


def log_event(request=None, action=AuditLog.Action.UPDATE, obj=None, message="", *, object_type=None, actor=None):
    """یک رویداد را ثبت می‌کند. هرگز نباید اطلاعات حساس (مثل رمز عبور) در message قرار گیرد."""
    if actor is None and request is not None:
        user = getattr(request, "user", None)
        actor = user if getattr(user, "is_authenticated", False) else None
    kwargs = {}
    if obj is not None:
        kwargs["object_type"] = object_type or obj._meta.verbose_name
        kwargs["object_id"] = str(obj.pk or "")
        kwargs["object_repr"] = str(obj)[:200]
    elif object_type:
        kwargs["object_type"] = object_type
    return AuditLog.objects.create(
        actor=actor,
        actor_name=(actor.display_name if actor is not None else "")[:150],
        action=action,
        message=(message or "")[:400],
        ip_address=_client_ip(request),
        **kwargs,
    )
