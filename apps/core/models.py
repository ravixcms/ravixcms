from django.conf import settings
from django.db import models

from .utils import RandomUploadPath
from .validators import validate_image_file


class TimeStampedModel(models.Model):
    """مدل پایه انتزاعی برای تمام مدل‌های آینده (محتوا، رسانه، ...)."""

    created_at = models.DateTimeField("تاریخ ایجاد", auto_now_add=True)
    updated_at = models.DateTimeField("آخرین ویرایش", auto_now=True)

    class Meta:
        abstract = True


class SiteSettings(models.Model):
    """تنظیمات عمومی سایت؛ همیشه فقط یک رکورد (singleton) وجود دارد."""

    site_name = models.CharField("نام سایت", max_length=120, default="راویکس")
    tagline = models.CharField("شعار سایت", max_length=200, blank=True)
    description = models.TextField("توضیحات سایت", blank=True, max_length=1000)
    logo = models.ImageField(
        "لوگو", upload_to=RandomUploadPath("site"), blank=True, validators=[validate_image_file]
    )
    favicon = models.ImageField(
        "فاوآیکون", upload_to=RandomUploadPath("site"), blank=True, validators=[validate_image_file]
    )
    default_share_image = models.ImageField(
        "تصویر پیش‌فرض اشتراک‌گذاری", upload_to=RandomUploadPath("site"), blank=True, validators=[validate_image_file],
        help_text="وقتی نوشته یا صفحه تصویر شاخص ندارد، این تصویر در پیش‌نمایش شبکه‌های اجتماعی (og:image) نمایش داده می‌شود. ترجیحاً ۱۲۰۰×۶۳۰.",
    )
    admin_email = models.EmailField("ایمیل مدیر / پشتیبانی", blank=True)
    contact_phone = models.CharField("تلفن تماس", max_length=30, blank=True)
    address = models.CharField("نشانی", max_length=300, blank=True)
    footer_text = models.CharField("متن پانویس", max_length=300, blank=True)
    registration_open = models.BooleanField(
        "ثبت‌نام عمومی فعال باشد",
        default=settings.REGISTRATION_DEFAULT_OPEN,
        help_text="در صورت غیرفعال بودن، کاربران جدید فقط توسط مدیران ایجاد می‌شوند.",
    )
    comments_require_approval = models.BooleanField(
        "دیدگاه‌ها پیش از نمایش نیاز به تأیید مدیر دارند", default=True,
        help_text="اگر خاموش باشد، دیدگاه‌های تازه (غیر اسپم) بلافاصله منتشر می‌شوند.")
    comments_notify_admin = models.BooleanField("دیدگاه جدید به ایمیل مدیر اطلاع داده شود", default=True,
                                                help_text="به «ایمیل مدیر / پشتیبانی» ارسال می‌شود؛ اگر خالی باشد ایمیلی نمی‌رود.")
    comments_blocked_words = models.TextField(
        "کلمات مسدود در دیدگاه", blank=True, max_length=5000,
        help_text="هر خط یک کلمه یا عبارت. دیدگاهی که (در نام، ایمیل یا متن) شامل یکی از آن‌ها باشد به‌صورت «اسپم» ثبت می‌شود.")
    updated_at = models.DateTimeField("آخرین ویرایش", auto_now=True)

    class Meta:
        verbose_name = "تنظیمات سایت"
        verbose_name_plural = "تنظیمات سایت"

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
        verbose_name = "ورودی کش جایگزین"
        verbose_name_plural = "ورودی‌های کش جایگزین"
