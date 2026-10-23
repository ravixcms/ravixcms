from django.utils.translation import gettext_lazy
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
        IMAGE = "image", gettext_lazy("Image")
        DOCUMENT = "document", gettext_lazy("Document")
        AUDIO = "audio", gettext_lazy("Audio")
        VIDEO = "video", gettext_lazy("Video")

    file = models.FileField(gettext_lazy("File"), upload_to=upload_path, max_length=255)
    original_name = models.CharField(gettext_lazy("Original file name"), max_length=255)
    title = models.CharField(gettext_lazy("Title"), max_length=200, blank=True)
    alt_text = models.CharField(gettext_lazy("Alt text"), max_length=300, blank=True,
                                help_text=gettext_lazy("A short description of the image for visually impaired users and search engines."))
    kind = models.CharField(gettext_lazy("Type"), max_length=10, choices=Kind.choices, db_index=True)
    mime_type = models.CharField(gettext_lazy("MIME type"), max_length=100, blank=True)
    size = models.PositiveBigIntegerField(gettext_lazy("Size (bytes)"), default=0)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="media_files", verbose_name=gettext_lazy("Uploaded by"))

    class Meta:
        verbose_name = gettext_lazy("Media")
        verbose_name_plural = gettext_lazy("Media files")
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
