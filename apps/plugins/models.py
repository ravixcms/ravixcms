from django.conf import settings
from django.db import models


class PluginRecord(models.Model):
    """وضعیت یک افزونهٔ **نصب‌شده**. اثرانگشت (fingerprint) همان کدی است که مدیر تأیید کرده؛ فقط همان کد بارگذاری می‌شود."""

    slug = models.SlugField("نامک", max_length=40, unique=True)
    name = models.CharField("نام", max_length=80)
    version = models.CharField("نسخه", max_length=20)
    fingerprint = models.CharField("اثرانگشت تأییدشده", max_length=64)
    manifest = models.JSONField("manifest تأییدشده", default=dict)
    is_active = models.BooleanField("فعال", default=False)
    auto_disabled = models.BooleanField("غیرفعال‌شدهٔ خودکار", default=False)
    config = models.JSONField("تنظیمات", default=dict, blank=True)
    installed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    installed_at = models.DateTimeField("زمان نصب", auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "افزونه"
        verbose_name_plural = "افزونه‌ها"
        ordering = ["name"]

    def __str__(self):
        return self.name


class PluginEvent(models.Model):
    """گزارش رویدادها و خطاهای افزونه‌ها (با حذف افزونه می‌ماند، پس FK ندارد)."""

    class Level(models.TextChoices):
        INFO = "info", "اطلاع"
        WARNING = "warning", "هشدار"
        ERROR = "error", "خطا"

    slug = models.CharField("افزونه", max_length=40, db_index=True)
    level = models.CharField("سطح", max_length=10, choices=Level.choices, default=Level.INFO)
    kind = models.CharField("نوع", max_length=30)
    message = models.CharField("پیام", max_length=400)
    detail = models.TextField("جزئیات", blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "رویداد افزونه"
        verbose_name_plural = "رویدادهای افزونه‌ها"
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["slug", "level", "-created_at"])]


class TrustedKey(models.Model):
    """کلید عمومی مورد اعتماد برای امضای بسته‌ها. فقط کلید عمومی؛ مدیریت فقط توسط مدیرکل (قابلیت plugins.trust)."""

    key_id = models.CharField(max_length=16, unique=True)
    public_key = models.CharField(max_length=64)
    label = models.CharField("برچسب", max_length=80)
    is_active = models.BooleanField("فعال", default=True)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["label"]


class PluginPackage(models.Model):
    """سابقهٔ منشأ (provenance) هر بسته‌ای که از طریق نصب‌کننده روی دیسک قرار گرفته است."""

    class Source(models.TextChoices):
        REGISTRY = "registry", "مخزن"
        LOCAL = "local", "فایل محلی"

    slug = models.CharField(max_length=40, db_index=True)
    version = models.CharField(max_length=20)
    source = models.CharField(max_length=10, choices=Source.choices)
    source_ref = models.CharField("مرجع منبع", max_length=200, blank=True)
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
        UPGRADE = "upgrade", "پیش از ارتقا"
        UNINSTALL = "uninstall", "پیش از حذف فایل‌ها"

    slug = models.CharField(max_length=40, db_index=True)
    version = models.CharField(max_length=20)
    reason = models.CharField(max_length=10, choices=Reason.choices)
    fingerprint = models.CharField(max_length=64)
    manifest = models.JSONField(default=dict)
    last_migration = models.CharField("آخرین مهاجرت اعمال‌شده", max_length=255, blank=True)
    had_migrations = models.BooleanField(default=False)
    was_active = models.BooleanField(default=False)
    config = models.JSONField(default=dict, blank=True)
    archive = models.CharField("نام فایل آرشیو", max_length=200)
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
