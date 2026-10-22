from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class CommentQuerySet(models.QuerySet):
    def approved(self):
        return self.filter(status=Comment.Status.APPROVED)


class Comment(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "در انتظار تأیید"
        APPROVED = "approved", "تأییدشده"
        SPAM = "spam", "اسپم"
        TRASH = "trash", "زباله"

    post = models.ForeignKey("content.Post", on_delete=models.CASCADE, related_name="comments", verbose_name="نوشته")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="replies", verbose_name="پاسخ به")
    depth = models.PositiveSmallIntegerField("عمق", default=0, editable=False)
    author_name = models.CharField("نام", max_length=80)
    author_email = models.EmailField("ایمیل", max_length=254)  # هرگز عمومی نمی‌شود
    body = models.TextField("متن دیدگاه", max_length=settings.RAVIN_COMMENT_MAX_LENGTH * 2)
    body_html = models.TextField(editable=False, blank=True)  # خروجی پاک‌سازی‌شده (nh3)؛ تنها همین در قالب‌ها نمایش داده می‌شود
    status = models.CharField("وضعیت", max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    ip_address = models.GenericIPAddressField("IP", null=True, blank=True)

    objects = CommentQuerySet.as_manager()

    class Meta:
        verbose_name = "دیدگاه"
        verbose_name_plural = "دیدگاه‌ها"
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["post", "status", "created_at"])]

    def __str__(self):
        return f"{self.author_name}: {self.body[:40]}"
