import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel

from .validators import get_extension


def upload_path(instance, filename):
    # نام ذخیره‌شده تصادفی است؛ نام اصلی فقط در original_name نگهداری می‌شود.
    return f"library/{timezone.now():%Y/%m}/{uuid.uuid4().hex}.{get_extension(filename)}"


class Media(TimeStampedModel):
    class Kind(models.TextChoices):
        IMAGE = "image", "تصویر"
        DOCUMENT = "document", "سند"
        AUDIO = "audio", "صوت"
        VIDEO = "video", "ویدئو"

    file = models.FileField("فایل", upload_to=upload_path, max_length=255)
    original_name = models.CharField("نام اصلی فایل", max_length=255)
    title = models.CharField("عنوان", max_length=200, blank=True)
    alt_text = models.CharField("متن جایگزین (Alt Text)", max_length=300, blank=True,
                                help_text="توضیح کوتاه تصویر برای نابینایان و موتورهای جستجو.")
    kind = models.CharField("نوع", max_length=10, choices=Kind.choices, db_index=True)
    mime_type = models.CharField("نوع MIME", max_length=100, blank=True)
    size = models.PositiveBigIntegerField("حجم (بایت)", default=0)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="media_files", verbose_name="بارگذاری‌کننده")

    class Meta:
        verbose_name = "رسانه"
        verbose_name_plural = "رسانه‌ها"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.display_title

    @property
    def display_title(self) -> str:
        return self.title or self.original_name

    @property
    def is_image(self) -> bool:
        return self.kind == self.Kind.IMAGE

    def user_can_manage(self, user) -> bool:
        """مدیران همه را؛ بقیه فقط فایل‌های خودشان را ویرایش/حذف می‌کنند."""
        if not user.is_authenticated:
            return False
        if user.has_cap("media.manage"):
            return True
        return user.has_cap("media.upload") and self.uploaded_by_id == user.pk
