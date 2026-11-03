from django.utils.translation import gettext_lazy
from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """رویدادهای مهم مدیریتی؛ فقط افزودنی (Append-only) و از طریق ``audit.services.log_event`` ثبت می‌شود."""

    class Action(models.TextChoices):
        CREATE = "create", gettext_lazy("Create")
        UPDATE = "update", gettext_lazy("Edit")
        DELETE = "delete", gettext_lazy("Delete")
        PUBLISH = "publish", gettext_lazy("Publish")
        LOGIN = "login", gettext_lazy("Sign in")
        LOGOUT = "logout", gettext_lazy("Sign out")
        LOGIN_FAILED = "login_failed", gettext_lazy("Failed sign-in")
        MCP = "mcp", gettext_lazy("AI assistant")

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events", verbose_name=gettext_lazy("User")
    )
    actor_name = models.CharField(gettext_lazy("User name"), max_length=150, blank=True)  # نسخه ثابت؛ با حذف کاربر هم می‌ماند
    action = models.CharField(gettext_lazy("Action"), max_length=20, choices=Action.choices, db_index=True)
    object_type = models.CharField(gettext_lazy("Object type"), max_length=60, blank=True, db_index=True)
    object_id = models.CharField(gettext_lazy("Object ID"), max_length=40, blank=True)
    object_repr = models.CharField(gettext_lazy("Object title"), max_length=200, blank=True)
    message = models.CharField(gettext_lazy("Event description"), max_length=400, blank=True)
    ip_address = models.GenericIPAddressField(gettext_lazy("IP address"), null=True, blank=True)
    created_at = models.DateTimeField(gettext_lazy("Time"), auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = gettext_lazy("Admin event")
        verbose_name_plural = gettext_lazy("Admin events")
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.get_action_display()} — {self.message or self.object_repr}"
