from django.utils.translation import gettext_lazy
from django.db import models


class ContactMessage(models.Model):
    name = models.CharField(gettext_lazy("Name"), max_length=80)
    email = models.EmailField(gettext_lazy("Email"))
    subject = models.CharField(gettext_lazy("Subject"), max_length=120, blank=True)
    message = models.TextField(gettext_lazy("Message"), max_length=2000)
    is_read = models.BooleanField(gettext_lazy("Read"), default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = gettext_lazy("Contact message")
        verbose_name_plural = gettext_lazy("Contact messages")

    def __str__(self):
        return f"{self.name}: {self.subject or self.message[:30]}"
