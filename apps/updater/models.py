from django.utils.translation import gettext_lazy
from django.conf import settings
from django.db import models


class UpdateRun(models.Model):
    """یک اجرای ارتقا/بازگردانی (هسته، قالب یا افزونه) با مراحل، نتیجهٔ مهاجرت و مسیر بازیابی. هیچ کلید یا رمزی اینجا ذخیره نمی‌شود."""

    class Component(models.TextChoices):
        CORE = "core", gettext_lazy("Core")
        THEME = "theme", gettext_lazy("Theme")
        PLUGIN = "plugin", gettext_lazy("Plugin")

    class Status(models.TextChoices):
        RUNNING = "running", gettext_lazy("Running")
        SUCCEEDED = "succeeded", gettext_lazy("Succeeded")
        FAILED = "failed", gettext_lazy("Failed (no changes)")
        ROLLED_BACK = "rolled_back", gettext_lazy("Failed and rolled back")
        NEEDS_RECOVERY = "needs_recovery", gettext_lazy("Failed; manual recovery required")
        REVERTED = "reverted", gettext_lazy("Manually rolled back")

    class Kind(models.TextChoices):
        UPGRADE = "upgrade", gettext_lazy("Upgrade")
        ROLLBACK = "rollback", gettext_lazy("Roll back to previous version")

    component = models.CharField(max_length=10, choices=Component.choices)
    slug = models.CharField(max_length=60, blank=True)  # قالب/افزونه؛ برای هسته خالی
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.UPGRADE)
    from_version = models.CharField(max_length=40, blank=True)
    to_version = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.RUNNING, db_index=True)
    phase = models.CharField(max_length=30, blank=True)
    source = models.CharField(max_length=200, blank=True)
    sha256 = models.CharField(max_length=64, blank=True)
    signer_key_id = models.CharField(max_length=16, blank=True)
    signer_label = models.CharField(max_length=80, blank=True)
    steps = models.JSONField(default=list, blank=True)  # [{name, ok, at, detail}]
    migrations_before = models.JSONField(default=list, blank=True)
    migrations_applied = models.JSONField(default=list, blank=True)  # مهاجرت‌های تازه اعمال‌شده در همین ارتقا
    migration_output = models.TextField(blank=True)
    backup = models.JSONField(default=dict, blank=True)  # {db_file, files_archive, sha256ها}؛ فقط نام فایل‌ها نسبت به پوشهٔ پشتیبان
    error = models.TextField(blank=True)
    recovery = models.TextField(blank=True)
    restart_required = models.BooleanField(default=False)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="rollbacks")
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at", "-id"]

    def __str__(self):
        return f"{self.component}:{self.slug or 'core'} {self.from_version}→{self.to_version} ({self.status})"


class UpdateLock(models.Model):
    """قفل سراسری ارتقا: یکتایی ``name`` تضمین می‌کند دو ارتقای هم‌زمان ممکن نیست. قفل مانده (کرش) پس از مدتی آزاد می‌شود."""

    name = models.CharField(max_length=20, unique=True, default="global")
    holder = models.CharField(max_length=64)
    operation = models.CharField(max_length=60)
    run = models.ForeignKey(UpdateRun, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    acquired_at = models.DateTimeField(auto_now_add=True)
