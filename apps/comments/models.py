from django.utils.translation import gettext_lazy
from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class CommentQuerySet(models.QuerySet):
    def approved(self):
        return self.filter(status=Comment.Status.APPROVED)


class Comment(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", gettext_lazy("Awaiting approval")
        APPROVED = "approved", gettext_lazy("Verified")
        SPAM = "spam", gettext_lazy("Spam")
        TRASH = "trash", gettext_lazy("Trash")

    post = models.ForeignKey("content.Post", on_delete=models.CASCADE, related_name="comments", verbose_name=gettext_lazy("Post"))
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="replies", verbose_name=gettext_lazy("Reply to"))
    depth = models.PositiveSmallIntegerField(gettext_lazy("Depth"), default=0, editable=False)
    author_name = models.CharField(gettext_lazy("Name"), max_length=80)
    author_email = models.EmailField(gettext_lazy("Email"), max_length=254)  # هرگز عمومی نمی‌شود
    body = models.TextField(gettext_lazy("Comment text"), max_length=settings.RAVIX_COMMENT_MAX_LENGTH * 2)
    body_html = models.TextField(editable=False, blank=True)  # خروجی پاک‌سازی‌شده (nh3)؛ تنها همین در قالب‌ها نمایش داده می‌شود
    status = models.CharField(gettext_lazy("Status"), max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    ip_address = models.GenericIPAddressField("IP", null=True, blank=True)

    objects = CommentQuerySet.as_manager()

    class Meta:
        verbose_name = gettext_lazy("Comment")
        verbose_name_plural = gettext_lazy("Comments")
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["post", "status", "created_at"])]

    def __str__(self):
        return f"{self.author_name}: {self.body[:40]}"
