from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """رویدادهای مهم مدیریتی؛ فقط افزودنی (Append-only) و از طریق ``audit.services.log_event`` ثبت می‌شود."""

    class Action(models.TextChoices):
        CREATE = "create", "ایجاد"
        UPDATE = "update", "ویرایش"
        DELETE = "delete", "حذف"
        PUBLISH = "publish", "انتشار"
        LOGIN = "login", "ورود"
        LOGOUT = "logout", "خروج"
        LOGIN_FAILED = "login_failed", "ورود ناموفق"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events", verbose_name="کاربر"
    )
    actor_name = models.CharField("نام کاربر", max_length=150, blank=True)  # نسخه ثابت؛ با حذف کاربر هم می‌ماند
    action = models.CharField("عملیات", max_length=20, choices=Action.choices, db_index=True)
    object_type = models.CharField("نوع مورد", max_length=60, blank=True, db_index=True)
    object_id = models.CharField("شناسه مورد", max_length=40, blank=True)
    object_repr = models.CharField("عنوان مورد", max_length=200, blank=True)
    message = models.CharField("شرح", max_length=400, blank=True)
    ip_address = models.GenericIPAddressField("نشانی IP", null=True, blank=True)
    created_at = models.DateTimeField("زمان", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "رویداد مدیریتی"
        verbose_name_plural = "رویدادهای مدیریتی"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.get_action_display()} — {self.message or self.object_repr}"
