from django.conf import settings
from django.db import models

from apps.content.models import Page
from apps.core.models import TimeStampedModel

from .schema import empty_document


class PageDesign(TimeStampedModel):
    """طرح صفحه‌ساز یک «صفحه ثابت». پیش‌نویس همیشه همین‌جاست؛ نسخه‌ی منتشرشده یک اشاره‌گر به DesignVersion است."""

    page = models.OneToOneField(Page, on_delete=models.CASCADE, related_name="design", verbose_name="صفحه")
    draft = models.JSONField("پیش‌نویس طرح", default=empty_document)
    # شمارنده‌ی قفل خوش‌بینانه: هر ذخیره آن را یکی زیاد می‌کند؛ ذخیره با شماره‌ی کهنه ۴۰۹ می‌گیرد (دو ویرایشگر هم‌زمان).
    revision = models.PositiveIntegerField("شماره‌ی ویرایش", default=1)
    published_version = models.ForeignKey("DesignVersion", null=True, blank=True, on_delete=models.SET_NULL,
                                          related_name="+", verbose_name="نسخه‌ی منتشرشده")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", verbose_name="آخرین ویرایشگر")

    class Meta:
        verbose_name = "طرح صفحه‌ساز"
        verbose_name_plural = "طرح‌های صفحه‌ساز"

    def __str__(self):
        return f"طرح «{self.page}»"


class DesignVersion(models.Model):
    """تصویر ثابت (snapshot) از یک سند؛ هرگز ویرایش نمی‌شود (فقط وضعیت بازبینی‌اش عوض می‌شود)."""

    class State(models.TextChoices):
        SAVED = "saved", "ذخیره‌شده"
        PENDING = "pending", "در انتظار تأیید"
        APPROVED = "approved", "تأییدشده / منتشرشده"
        REJECTED = "rejected", "ردشده"

    design = models.ForeignKey(PageDesign, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField("شماره‌ی نسخه")
    document = models.JSONField("سند")
    state = models.CharField("وضعیت", max_length=10, choices=State.choices, default=State.SAVED, db_index=True)
    is_auto = models.BooleanField("ذخیره‌ی خودکار سیستم", default=False)
    note = models.CharField("یادداشت", max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", verbose_name="سازنده")
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+", verbose_name="بازبین")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField("یادداشت بازبین", max_length=200, blank=True)

    class Meta:
        verbose_name = "نسخه‌ی طرح"
        verbose_name_plural = "نسخه‌های طرح"
        ordering = ["-number"]
        constraints = [models.UniqueConstraint(fields=["design", "number"], name="uniq_design_version_number")]

    def __str__(self):
        return f"نسخه {self.number} — {self.design.page}"
