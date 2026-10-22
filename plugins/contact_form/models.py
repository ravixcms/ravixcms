from django.db import models


class ContactMessage(models.Model):
    name = models.CharField("نام", max_length=80)
    email = models.EmailField("ایمیل")
    subject = models.CharField("موضوع", max_length=120, blank=True)
    message = models.TextField("پیام", max_length=2000)
    is_read = models.BooleanField("خوانده‌شده", default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "پیام تماس"
        verbose_name_plural = "پیام‌های تماس"

    def __str__(self):
        return f"{self.name}: {self.subject or self.message[:30]}"
