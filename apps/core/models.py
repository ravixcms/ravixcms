from django.utils.translation import gettext_lazy
from django.conf import settings
from django.db import models

from .utils import RandomUploadPath
from .validators import validate_image_file


class TimeStampedModel(models.Model):
    """مدل پایه انتزاعی برای تمام مدل‌های آینده (محتوا، رسانه، ...)."""

    created_at = models.DateTimeField(gettext_lazy("Created at"), auto_now_add=True)
    updated_at = models.DateTimeField(gettext_lazy("Last edit"), auto_now=True)

    class Meta:
        abstract = True


class SiteSettings(models.Model):
    """تنظیمات عمومی سایت؛ همیشه فقط یک رکورد (singleton) وجود دارد."""

    site_name = models.CharField(gettext_lazy("Site name"), max_length=120, default=gettext_lazy("Ravix"))
    tagline = models.CharField(gettext_lazy("Site tagline"), max_length=200, blank=True)
    description = models.TextField(gettext_lazy("Site description"), blank=True, max_length=1000)
    logo = models.ImageField(
        gettext_lazy("Logo"), upload_to=RandomUploadPath("site"), blank=True, validators=[validate_image_file]
    )
    favicon = models.ImageField(
        gettext_lazy("Favicon"), upload_to=RandomUploadPath("site"), blank=True, validators=[validate_image_file]
    )
    default_share_image = models.ImageField(
        gettext_lazy("Default sharing image"), upload_to=RandomUploadPath("site"), blank=True, validators=[validate_image_file],
        help_text=gettext_lazy("When a post or page has no featured image, this image is shown in social media previews (og:image). Preferably 1200×630."),
    )
    admin_email = models.EmailField(gettext_lazy("Admin / support email"), blank=True)
    contact_phone = models.CharField(gettext_lazy("Contact phone"), max_length=30, blank=True)
    address = models.CharField(gettext_lazy("URL"), max_length=300, blank=True)
    footer_text = models.CharField(gettext_lazy("Footer text"), max_length=300, blank=True)
    language = models.CharField(gettext_lazy("Language"), max_length=10, default="fa")
    timezone = models.CharField(gettext_lazy("Time zone"), max_length=64, default="Asia/Tehran")
    installed_at = models.DateTimeField(gettext_lazy("Installation completed at"), null=True, blank=True, editable=False)
    onboarding_dismissed = models.BooleanField(gettext_lazy("Getting-started guide dismissed"), default=False, editable=False)
    registration_open = models.BooleanField(
        gettext_lazy("Public registration is enabled"),
        default=settings.REGISTRATION_DEFAULT_OPEN,
        help_text=gettext_lazy("If disabled, new users are created only by administrators."),
    )
    comments_require_approval = models.BooleanField(
        gettext_lazy("Comments require administrator approval before being shown"), default=True,
        help_text=gettext_lazy("If off, new (non-spam) comments are published immediately."))
    comments_notify_admin = models.BooleanField(gettext_lazy("Notify the admin email of new comments"), default=True,
                                                help_text=gettext_lazy("Sent to “Admin / support email”; if empty, no email is sent."))
    comments_blocked_words = models.TextField(
        gettext_lazy("Blocked words in comments"), blank=True, max_length=5000,
        help_text=gettext_lazy("One word or phrase per line. A comment that contains one of them (in the name, email or text) is recorded as “spam”."))
    updated_at = models.DateTimeField(gettext_lazy("Last edit"), auto_now=True)

    class Meta:
        verbose_name = gettext_lazy("Site settings")
        verbose_name_plural = gettext_lazy("Site settings")

    def __str__(self):
        return self.site_name

    def save(self, *args, **kwargs):
        self.pk = 1  # singleton
        kwargs.pop("force_insert", None)  # objects.create() روی رکورد موجود، آن را به‌روز می‌کند
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):  # جلوگیری از حذف تنظیمات
        pass

    @classmethod
    def load(cls) -> "SiteSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ThrottleCacheEntry(models.Model):
    """جدول کش جایگزین (DatabaseCache با نام ``ravin_cache``) برای محدودسازی ورود وقتی کش اصلی خراب است.
    فقط از طریق مهاجرت ساخته می‌شود (بدون نیاز به ``createcachetable``)؛ ستون‌ها دقیقاً همان‌هایی‌اند که Django انتظار دارد."""

    cache_key = models.CharField(max_length=255, primary_key=True)
    value = models.TextField()
    expires = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "ravin_cache"
        verbose_name = gettext_lazy("Fallback cache entry")
        verbose_name_plural = gettext_lazy("Fallback cache entries")
