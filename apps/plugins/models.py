from django.utils.translation import gettext_lazy
from django.conf import settings
from django.db import models


class PluginRecord(models.Model):
    """وضعیت یک افزونهٔ **نصب‌شده**. اثرانگشت (fingerprint) همان کدی است که مدیر تأیید کرده؛ فقط همان کد بارگذاری می‌شود."""

    slug = models.SlugField(gettext_lazy("Slug"), max_length=40, unique=True)
    name = models.CharField(gettext_lazy("Name"), max_length=80)
    version = models.CharField(gettext_lazy("Version"), max_length=20)
    fingerprint = models.CharField(gettext_lazy("Approved fingerprint"), max_length=64)
    manifest = models.JSONField(gettext_lazy("Approved manifest"), default=dict)
    is_active = models.BooleanField(gettext_lazy("Active"), default=False)
    auto_disabled = models.BooleanField(gettext_lazy("Disabled automatically"), default=False)
    config = models.JSONField(gettext_lazy("Settings"), default=dict, blank=True)
    installed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    installed_at = models.DateTimeField(gettext_lazy("Installed at"), auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = gettext_lazy("Plugin")
        verbose_name_plural = gettext_lazy("Plugins")
        ordering = ["name"]

    def __str__(self):
        return self.name


class PluginEvent(models.Model):
    """گزارش رویدادها و خطاهای افزونه‌ها (با حذف افزونه می‌ماند، پس FK ندارد)."""

    class Level(models.TextChoices):
        INFO = "info", gettext_lazy("Info")
        WARNING = "warning", gettext_lazy("Warning")
        ERROR = "error", gettext_lazy("Error")

    slug = models.CharField(gettext_lazy("Plugin"), max_length=40, db_index=True)
    level = models.CharField(gettext_lazy("Level"), max_length=10, choices=Level.choices, default=Level.INFO)
    kind = models.CharField(gettext_lazy("Type"), max_length=30)
    message = models.CharField(gettext_lazy("Message"), max_length=400)
    detail = models.TextField(gettext_lazy("Details"), blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = gettext_lazy("Plugin event")
        verbose_name_plural = gettext_lazy("Plugin events")
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["slug", "level", "-created_at"])]


class TrustedKey(models.Model):
    """کلید عمومی مورد اعتماد برای امضای بسته‌ها. فقط کلید عمومی؛ مدیریت فقط توسط مدیرکل (قابلیت plugins.trust)."""

    key_id = models.CharField(max_length=16, unique=True)
    public_key = models.CharField(max_length=64)
    label = models.CharField(gettext_lazy("Tag"), max_length=80)
    is_active = models.BooleanField(gettext_lazy("Active"), default=True)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["label"]


class PluginPackage(models.Model):
    """سابقهٔ منشأ (provenance) هر بسته‌ای که از طریق نصب‌کننده روی دیسک قرار گرفته است."""

    class Source(models.TextChoices):
        REGISTRY = "registry", gettext_lazy("Registry")
        LOCAL = "local", gettext_lazy("Local file")

    slug = models.CharField(max_length=40, db_index=True)
    version = models.CharField(max_length=20)
    source = models.CharField(max_length=10, choices=Source.choices)
    source_ref = models.CharField(gettext_lazy("Source reference"), max_length=200, blank=True)
    sha256 = models.CharField(max_length=64)
    fingerprint = models.CharField(max_length=64)
    signature_state = models.CharField(max_length=10)  # verified | unsigned
    signer_key_id = models.CharField(max_length=16, blank=True)
    signer_label = models.CharField(max_length=80, blank=True)
    installed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class PluginBackup(models.Model):
    """پشتیبان نسخهٔ قبلی (آرشیو zip + وضعیت ثبت‌شده) پیش از ارتقا یا حذف فایل‌ها؛ پایهٔ بازگشت."""

    class Reason(models.TextChoices):
        UPGRADE = "upgrade", gettext_lazy("Before upgrade")
        UNINSTALL = "uninstall", gettext_lazy("Before file removal")

    slug = models.CharField(max_length=40, db_index=True)
    version = models.CharField(max_length=20)
    reason = models.CharField(max_length=10, choices=Reason.choices)
    fingerprint = models.CharField(max_length=64)
    manifest = models.JSONField(default=dict)
    last_migration = models.CharField(gettext_lazy("Last applied migration"), max_length=255, blank=True)
    had_migrations = models.BooleanField(default=False)
    was_active = models.BooleanField(default=False)
    config = models.JSONField(default=dict, blank=True)
    archive = models.CharField(gettext_lazy("Archive file name"), max_length=200)
    archive_sha256 = models.CharField(max_length=64)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    restored_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class PluginOperationLock(models.Model):
    """قفل عملیات روی یک افزونه (نصب/ارتقا/بازگشت/حذف). یکتایی slug تضمین می‌کند دو درخواست هم‌زمان هم‌زمان اجرا نشوند."""

    slug = models.CharField(max_length=40, unique=True)
    operation = models.CharField(max_length=20)
    holder = models.CharField(max_length=64)
    acquired_at = models.DateTimeField(auto_now_add=True)
