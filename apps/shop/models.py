from django.utils.translation import gettext, gettext_lazy
import secrets

from django.conf import settings
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.urls import reverse

from apps.core.images import optimize_image_field
from apps.core.models import TimeStampedModel
from apps.core.utils import RandomUploadPath
from apps.core.validators import validate_image_file
from apps.sites.models import Site

from .storage import private_storage

DIGITAL_EXTENSIONS = ["pdf", "zip", "epub", "mp3", "mp4", "txt", "csv", "jpg", "jpeg", "png", "docx", "xlsx", "pptx"]


class ProductCategory(TimeStampedModel):
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="product_categories")
    name = models.CharField(gettext_lazy("Category name"), max_length=80)
    slug = models.SlugField(gettext_lazy("Slug"), max_length=90, allow_unicode=True)
    order = models.PositiveSmallIntegerField(gettext_lazy("Sort order"), default=100)

    class Meta:
        verbose_name = gettext_lazy("Product category")
        verbose_name_plural = gettext_lazy("Product categories")
        ordering = ["order", "name"]
        constraints = [models.UniqueConstraint(fields=["site", "slug"], name="shop_category_unique_slug_per_site")]

    def __str__(self):
        return self.name


class Product(TimeStampedModel):
    class Kind(models.TextChoices):
        PHYSICAL = "physical", gettext_lazy("Physical")
        DIGITAL = "digital", gettext_lazy("Digital (downloadable)")

    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="products")
    kind = models.CharField(gettext_lazy("Type"), max_length=10, choices=Kind.choices, default=Kind.PHYSICAL, db_index=True)
    category = models.ForeignKey("ProductCategory", null=True, blank=True, on_delete=models.SET_NULL, related_name="products")
    name = models.CharField(gettext_lazy("Product name"), max_length=200)
    slug = models.SlugField(gettext_lazy("Slug"), max_length=220, allow_unicode=True)
    description = models.TextField(gettext_lazy("Description"), blank=True, max_length=5000)
    price = models.PositiveBigIntegerField(gettext_lazy("Price"), validators=[MinValueValidator(1)], help_text=gettext_lazy("In the site currency (default toman), a whole number."))
    discount_percent = models.PositiveSmallIntegerField(gettext_lazy("Discount percentage"), default=0, validators=[MaxValueValidator(90)], help_text=gettext_lazy("0 to 90; the final price is always calculated on the server."))
    stock = models.PositiveIntegerField(gettext_lazy("Stock"), default=0)
    track_stock = models.BooleanField(gettext_lazy("Stock control"), default=True, help_text=gettext_lazy("Turn it off for an unlimited digital product."))
    digital_file = models.FileField(gettext_lazy("Digital file"), upload_to=RandomUploadPath("digital"), storage=private_storage, blank=True, max_length=200,
                                    validators=[FileExtensionValidator(DIGITAL_EXTENSIONS)])
    is_active = models.BooleanField(gettext_lazy("Active (visible and purchasable)"), default=True)
    image = models.ImageField(gettext_lazy("Image"), upload_to=RandomUploadPath("shop"), blank=True, validators=[validate_image_file])

    class Meta:
        verbose_name = gettext_lazy("Product")
        verbose_name_plural = gettext_lazy("Products")
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["site", "slug"], name="shop_product_unique_slug_per_site")]
        indexes = [models.Index(fields=["site", "is_active", "-created_at"])]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        optimize_image_field(self.image)
        super().save(*args, **kwargs)

    @property
    def is_digital(self):
        return self.kind == self.Kind.DIGITAL

    @property
    def needs_shipping(self):
        return not self.is_digital

    @property
    def discount_amount(self):
        """تخفیف هر واحد (عدد صحیح، رو به پایین)؛ هرگز قیمت نهایی را زیر ۱ نمی‌برد."""
        return min(self.price * self.discount_percent // 100, self.price - 1)

    @property
    def final_price(self):
        return self.price - self.discount_amount

    @property
    def has_discount(self):
        return self.discount_amount > 0

    @property
    def in_stock(self):
        return (self.stock > 0) if self.track_stock else True

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.is_digital and self.pk and self.is_active and not self.digital_file:
            raise ValidationError({"digital_file": gettext("An active digital product needs a file.")})

    def get_absolute_url(self):
        return reverse("shop:product", args=[self.slug])


class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="gallery")
    image = models.ImageField(gettext_lazy("Image"), upload_to=RandomUploadPath("shop"), validators=[validate_image_file])
    alt = models.CharField(gettext_lazy("Alternative text"), max_length=150, blank=True)
    order = models.PositiveSmallIntegerField(gettext_lazy("Sort order"), default=100)

    class Meta:
        ordering = ["order", "id"]

    def save(self, *args, **kwargs):
        optimize_image_field(self.image)
        super().save(*args, **kwargs)


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending_payment", gettext_lazy("Awaiting payment")
        PAID = "paid", gettext_lazy("Paid")
        PROCESSING = "processing", gettext_lazy("Processing")
        SHIPPED = "shipped", gettext_lazy("Shipped")
        COMPLETED = "completed", gettext_lazy("Completed")
        CANCELLED = "cancelled", gettext_lazy("Cancelled")
        REFUNDED = "refunded", gettext_lazy("Refunded")

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="orders")
    number = models.CharField(gettext_lazy("Order number"), max_length=20)
    token = models.CharField(max_length=40, unique=True, editable=False)  # کلید غیرقابل‌حدس برای مشاهدهٔ سفارش توسط مشتری
    status = models.CharField(gettext_lazy("Status"), max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    customer_name = models.CharField(gettext_lazy("Full name"), max_length=100)
    phone = models.CharField(gettext_lazy("Phone"), max_length=20)
    email = models.EmailField(gettext_lazy("Email"), blank=True)
    address = models.TextField(gettext_lazy("URL"), max_length=500, blank=True)  # فقط برای سفارش دارای کالای فیزیکی الزامی است
    note = models.CharField(gettext_lazy("Customer note"), max_length=300, blank=True)
    admin_note = models.CharField(gettext_lazy("Admin note"), max_length=500, blank=True)
    subtotal = models.PositiveBigIntegerField(gettext_lazy("Subtotal before discount"), default=0)
    discount_total = models.PositiveBigIntegerField(gettext_lazy("Total discount"), default=0)
    total = models.PositiveBigIntegerField(gettext_lazy("Grand total"))
    currency = models.CharField(gettext_lazy("Currency"), max_length=5, default="IRT")
    needs_shipping = models.BooleanField(gettext_lazy("Requires shipping"), default=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = gettext_lazy("Order")
        verbose_name_plural = gettext_lazy("Orders")
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["site", "number"], name="shop_order_unique_number_per_site")]
        indexes = [models.Index(fields=["site", "status", "-created_at"])]

    def __str__(self):
        return self.number

    @staticmethod
    def new_number():
        return "RV-" + secrets.token_hex(4).upper()

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_hex(20)
        if not self.number:
            self.number = self.new_number()
        super().save(*args, **kwargs)

    def get_public_url(self):
        return reverse("shop:order", args=[self.number, self.token])

    @property
    def is_paid_state(self):
        """پرداخت پذیرفته شده و سفارش لغو/مسترد نشده (دسترسی به دانلود دیجیتال)."""
        return self.status in (self.Status.PAID, self.Status.PROCESSING, self.Status.SHIPPED, self.Status.COMPLETED)


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    name = models.CharField(max_length=200)  # عکس لحظهٔ خرید؛ با تغییر/حذف محصول عوض نمی‌شود
    kind = models.CharField(max_length=10, choices=Product.Kind.choices, default=Product.Kind.PHYSICAL)
    list_price = models.PositiveBigIntegerField(gettext_lazy("Price before discount"), default=0)
    unit_discount = models.PositiveBigIntegerField(gettext_lazy("Discount per unit"), default=0)
    download_count = models.PositiveSmallIntegerField(default=0)
    tracked = models.BooleanField(gettext_lazy("Its stock has been deducted"), default=True)  # برای برگرداندن دقیق موجودی هنگام لغو/استرداد
    unit_price = models.PositiveBigIntegerField()
    quantity = models.PositiveIntegerField()
    line_total = models.PositiveBigIntegerField()


class Payment(models.Model):
    class Status(models.TextChoices):
        INITIATED = "initiated", gettext_lazy("Started")
        SUCCEEDED = "succeeded", gettext_lazy("Succeeded")
        FAILED = "failed", gettext_lazy("Failed")
        REVIEW = "needs_review", gettext_lazy("Needs review")  # پرداخت موفق برای سفارشی که دیگر قابل پذیرش نبود

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    gateway = models.CharField(max_length=30)
    amount = models.PositiveBigIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INITIATED, db_index=True)
    reference = models.CharField(max_length=40, unique=True, editable=False)
    currency = models.CharField(max_length=5, default="IRT")
    is_test = models.BooleanField(default=False)
    gateway_ref = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # حداکثر یک پرداخت موفق برای هر سفارش و یک شناسهٔ ارائه‌دهنده فقط برای یک پرداخت: دفاع نهایی در برابر ثبت چندباره
            models.UniqueConstraint(fields=["order"], condition=models.Q(status="succeeded"), name="shop_payment_one_success_per_order"),
            models.UniqueConstraint(fields=["gateway", "gateway_ref"], condition=~models.Q(gateway_ref=""), name="shop_payment_unique_gateway_ref"),
        ]

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = secrets.token_hex(16)
        super().save(*args, **kwargs)


class SandboxTransaction(models.Model):
    """سمت «بانک» در درگاه آزمایشی. فقط صفحهٔ آزمایشی پرداخت وضعیت را تغییر می‌دهد؛ تأیید سرور همین را می‌خواند، نه پارامترهای مرورگر."""

    class State(models.TextChoices):
        PENDING = "pending", gettext_lazy("Pending")
        PAID = "paid", gettext_lazy("Paid")
        FAILED = "failed", gettext_lazy("Failed")

    reference = models.CharField(max_length=40, unique=True)
    amount = models.PositiveBigIntegerField()
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)


class OrderEvent(models.Model):
    """تاریخچهٔ تغییرات مهم سفارش (فقط افزودنی). برای حسابرسی؛ از طریق رابط کاربری ویرایش/حذف نمی‌شود."""

    class Kind(models.TextChoices):
        CREATED = "created", gettext_lazy("Order placed")
        STATUS = "status", gettext_lazy("Status change")
        PAYMENT = "payment", gettext_lazy("Payment")
        NOTE = "note", gettext_lazy("Note")
        DOWNLOAD = "download", gettext_lazy("Download")

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=10, choices=Kind.choices, db_index=True)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    message = models.CharField(max_length=300, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]


class PaymentEvent(models.Model):
    """گزارش رخدادهای تراکنش. داده‌ها پیش از ذخیره پاک‌سازی می‌شوند (بدون کلید/توکن/اطلاعات کارت)."""

    class Kind(models.TextChoices):
        CREATED = "created", gettext_lazy("Transaction created")
        REDIRECT = "redirect", gettext_lazy("Redirect to gateway")
        CALLBACK = "callback", gettext_lazy("Customer return")
        VERIFY = "verify", gettext_lazy("Approve")
        INQUIRE = "inquire", gettext_lazy("Status inquiry")
        STATUS = "status", gettext_lazy("Status change")
        DUPLICATE = "duplicate", gettext_lazy("Duplicate request")
        ERROR = "error", gettext_lazy("Error")

    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    message = models.CharField(max_length=300, blank=True)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]


class GatewayConfig(TimeStampedModel):
    """تنظیمات و وضعیت فعال/غیرفعال یک درگاه برای یک سایت. مقادیر محرمانه فقط رمزنگاری‌شده ذخیره می‌شوند."""

    site = models.ForeignKey("sites.Site", on_delete=models.CASCADE, related_name="gateway_configs")
    gateway = models.CharField(max_length=30)
    enabled = models.BooleanField(gettext_lazy("Active"), default=False)
    test_mode = models.BooleanField(gettext_lazy("Test mode"), default=True)
    public_config = models.JSONField(default=dict, blank=True)  # فقط فیلدهای غیرمحرمانه
    secrets_encrypted = models.TextField(blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["site", "gateway"], name="shop_gateway_unique_per_site")]

    def __str__(self):
        return f"{self.gateway}@{self.site_id}"
