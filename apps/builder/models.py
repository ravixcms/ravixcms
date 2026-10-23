from django.utils.translation import gettext, gettext_lazy
from django.conf import settings
from django.db import models

from apps.content.models import Page
from apps.core.models import TimeStampedModel

from .schema import empty_document


class PageDesign(TimeStampedModel):
    """طرح صفحه‌ساز یک «صفحه ثابت». پیش‌نویس همیشه همین‌جاست؛ نسخه‌ی منتشرشده یک اشاره‌گر به DesignVersion است."""

    page = models.OneToOneField(Page, on_delete=models.CASCADE, related_name="design", verbose_name=gettext_lazy("Page"))
    draft = models.JSONField(gettext_lazy("Layout draft"), default=empty_document)
    # شمارنده‌ی قفل خوش‌بینانه: هر ذخیره آن را یکی زیاد می‌کند؛ ذخیره با شماره‌ی کهنه ۴۰۹ می‌گیرد (دو ویرایشگر هم‌زمان).
    revision = models.PositiveIntegerField(gettext_lazy("Revision number"), default=1)
    published_version = models.ForeignKey("DesignVersion", null=True, blank=True, on_delete=models.SET_NULL,
                                          related_name="+", verbose_name=gettext_lazy("Published version"))
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", verbose_name=gettext_lazy("Last editor"))

    class Meta:
        verbose_name = gettext_lazy("Page builder layout")
        verbose_name_plural = gettext_lazy("Page builder layouts")

    def __str__(self):
        return (gettext("Layout of “%(page)s”") % {"page": self.page})


class DesignVersion(models.Model):
    """تصویر ثابت (snapshot) از یک سند؛ هرگز ویرایش نمی‌شود (فقط وضعیت بازبینی‌اش عوض می‌شود)."""

    class State(models.TextChoices):
        SAVED = "saved", gettext_lazy("Saved")
        PENDING = "pending", gettext_lazy("Awaiting approval")
        APPROVED = "approved", gettext_lazy("Approved / published")
        REJECTED = "rejected", gettext_lazy("Rejected")

    design = models.ForeignKey(PageDesign, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField(gettext_lazy("Version number"))
    document = models.JSONField(gettext_lazy("Document"))
    state = models.CharField(gettext_lazy("Status"), max_length=10, choices=State.choices, default=State.SAVED, db_index=True)
    is_auto = models.BooleanField(gettext_lazy("System autosave"), default=False)
    note = models.CharField(gettext_lazy("Note"), max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", verbose_name=gettext_lazy("Creator"))
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+", verbose_name=gettext_lazy("Reviewer"))
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(gettext_lazy("Reviewer note"), max_length=200, blank=True)

    class Meta:
        verbose_name = gettext_lazy("Layout version")
        verbose_name_plural = gettext_lazy("Layout versions")
        ordering = ["-number"]
        constraints = [models.UniqueConstraint(fields=["design", "number"], name="uniq_design_version_number")]

    def __str__(self):
        return (gettext("Version %(number)s — %(page)s") % {"number": self.number, "page": self.design.page})
