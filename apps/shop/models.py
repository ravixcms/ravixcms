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
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children", verbose_name=gettext_lazy("Parent category"))

    MAX_DEPTH = 4  # levels, the top level counts as 1

    class Meta:
        verbose_name = gettext_lazy("Product category")
        verbose_name_plural = gettext_lazy("Product categories")
        ordering = ["order", "name"]
        constraints = [models.UniqueConstraint(fields=["site", "slug"], name="shop_category_unique_slug_per_site")]

    def __str__(self):
        return self.name

    def ancestors(self):
        """Parents from the top level down to the direct parent (loops are impossible: the form refuses them; the guard stops a damaged tree)."""
        chain, node, seen = [], self.parent, {self.pk}
        while node is not None and node.pk not in seen and len(chain) < self.MAX_DEPTH + 2:
            chain.append(node)
            seen.add(node.pk)
            node = node.parent
        return list(reversed(chain))

    @property
    def depth(self) -> int:
        return len(self.ancestors()) + 1

    @property
    def path_name(self) -> str:
        return " › ".join([c.name for c in self.ancestors()] + [self.name])

    def descendant_ids(self) -> list[int]:
        """This category and everything below it (breadth first, bounded)."""
        ids, frontier = [self.pk], [self.pk]
        for _ in range(self.MAX_DEPTH + 1):
            frontier = list(ProductCategory.objects.filter(site_id=self.site_id, parent_id__in=frontier).exclude(pk__in=ids).values_list("pk", flat=True))
            if not frontier:
                break
            ids += frontier
        return ids

    def subtree_height(self) -> int:
        """Levels below this category (0 for a leaf)."""
        height, frontier, seen = 0, [self.pk], {self.pk}
        while True:
            frontier = [c for c in ProductCategory.objects.filter(site_id=self.site_id, parent_id__in=frontier).values_list("pk", flat=True) if c not in seen]
            if not frontier:
                return height
            seen.update(frontier)
            height += 1


class ProductTag(TimeStampedModel):
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="product_tags")
    name = models.CharField(gettext_lazy("Tag"), max_length=60)
    slug = models.SlugField(gettext_lazy("Slug"), max_length=80, allow_unicode=True)

    class Meta:
        verbose_name = gettext_lazy("Product tag")
        verbose_name_plural = gettext_lazy("Product tags")
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["site", "slug"], name="shop_tag_unique_slug_per_site")]

    def __str__(self):
        return self.name


class ProductAttribute(TimeStampedModel):
    """A property that varies (colour, size ...). Its values are ``AttributeValue`` rows; a variable product picks the attributes it varies by."""

    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="product_attributes")
    name = models.CharField(gettext_lazy("Attribute name"), max_length=60)
    slug = models.SlugField(gettext_lazy("Slug"), max_length=80, allow_unicode=True)

    class Meta:
        verbose_name = gettext_lazy("Product attribute")
        verbose_name_plural = gettext_lazy("Product attributes")
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["site", "slug"], name="shop_attribute_unique_slug_per_site")]

    def __str__(self):
        return self.name


class AttributeValue(models.Model):
    attribute = models.ForeignKey(ProductAttribute, on_delete=models.CASCADE, related_name="values")
    value = models.CharField(gettext_lazy("Value"), max_length=80)
    slug = models.SlugField(max_length=100, allow_unicode=True)
    order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ["order", "id"]
        constraints = [models.UniqueConstraint(fields=["attribute", "slug"], name="shop_attrvalue_unique_slug_per_attribute")]

    def __str__(self):
        return self.value


class Product(TimeStampedModel):
    class Kind(models.TextChoices):
        PHYSICAL = "physical", gettext_lazy("Physical")
        DIGITAL = "digital", gettext_lazy("Digital (downloadable)")
        VARIABLE = "variable", gettext_lazy("Variable (colour, size ...)")

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
    sku = models.CharField(gettext_lazy("SKU"), max_length=64, blank=True)
    weight_grams = models.PositiveIntegerField(gettext_lazy("Weight (grams)"), default=0)
    tags = models.ManyToManyField("ProductTag", blank=True, related_name="products", verbose_name=gettext_lazy("Tags"))
    related = models.ManyToManyField("self", symmetrical=False, blank=True, related_name="related_from", verbose_name=gettext_lazy("Related products"))
    low_stock_threshold = models.PositiveIntegerField(gettext_lazy("Low-stock alert at"), null=True, blank=True, help_text=gettext_lazy("Empty = the shop-wide threshold."))
    variation_attributes = models.ManyToManyField("ProductAttribute", blank=True, related_name="+", verbose_name=gettext_lazy("Varies by"))

    class Meta:
        verbose_name = gettext_lazy("Product")
        verbose_name_plural = gettext_lazy("Products")
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["site", "slug"], name="shop_product_unique_slug_per_site"),
                       models.UniqueConstraint(fields=["site", "sku"], condition=~Q(sku=""), name="shop_product_unique_sku_per_site")]
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
    def is_variable(self):
        return self.kind == self.Kind.VARIABLE

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
        if self.is_variable:
            return any(v.in_stock for v in self.active_variants())
        return (self.stock > 0) if self.track_stock else True

    def active_variants(self):
        """Purchasable variants (a prefetch of ``variants`` is reused when present)."""
        return [v for v in self.variants.all() if v.is_active]

    @property
    def price_range(self):
        """``(lowest, highest)`` final price over the active variants; a simple product returns its own price twice."""
        if self.is_variable:
            prices = [v.final_price for v in self.active_variants()]
            if prices:
                return min(prices), max(prices)
        return self.final_price, self.final_price

    @property
    def has_price_range(self):
        low, high = self.price_range
        return low != high

    def sync_variable_price(self):
        """A variable product's own ``price`` follows its cheapest active variant, so sorting and filtering by price keep working."""
        if not self.is_variable:
            return
        prices = [v.price for v in self.active_variants()]
        if prices and self.price != min(prices):
            Product.objects.filter(pk=self.pk).update(price=min(prices))
            self.price = min(prices)

    @property
    def effective_low_stock_threshold(self):
        if self.low_stock_threshold is not None:
            return self.low_stock_threshold
        return ShopSettings.for_site(self.site).low_stock_threshold

    @property
    def is_low_stock(self):
        return self.track_stock and not self.is_variable and 0 < self.stock <= self.effective_low_stock_threshold

    @property
    def is_out_of_stock(self):
        return self.track_stock and not self.is_variable and self.stock <= 0

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


class ProductVariant(TimeStampedModel):
    """One purchasable combination of a variable product (e.g. Red / L) with its own SKU, price, stock, weight and image."""

    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="product_variants")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    options = models.ManyToManyField(AttributeValue, related_name="variants", blank=True)
    options_key = models.CharField(max_length=200, editable=False, default="")  # sorted value ids: one variant per combination
    sku = models.CharField(gettext_lazy("SKU"), max_length=64, blank=True)
    price = models.PositiveBigIntegerField(gettext_lazy("Price"), validators=[MinValueValidator(1)])
    stock = models.PositiveIntegerField(gettext_lazy("Stock"), default=0)
    track_stock = models.BooleanField(gettext_lazy("Stock control"), default=True)
    weight_grams = models.PositiveIntegerField(gettext_lazy("Weight (grams)"), null=True, blank=True, help_text=gettext_lazy("Empty = the product's weight."))
    image = models.ImageField(gettext_lazy("Image"), upload_to=RandomUploadPath("shop"), blank=True, validators=[validate_image_file])
    is_active = models.BooleanField(gettext_lazy("Active (visible and purchasable)"), default=True)

    class Meta:
        verbose_name = gettext_lazy("Variant")
        verbose_name_plural = gettext_lazy("Variants")
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["product", "options_key"], name="shop_variant_unique_combination"),
                       models.UniqueConstraint(fields=["site", "sku"], condition=~Q(sku=""), name="shop_variant_unique_sku_per_site")]

    def __str__(self):
        return self.label or f"#{self.pk}"

    def save(self, *args, **kwargs):
        self.site_id = self.product.site_id
        optimize_image_field(self.image)
        super().save(*args, **kwargs)

    @property
    def label(self) -> str:
        return " / ".join(o.value for o in self.options.all())

    @property
    def discount_amount(self):
        return min(self.price * self.product.discount_percent // 100, self.price - 1)

    @property
    def final_price(self):
        return self.price - self.discount_amount

    @property
    def has_discount(self):
        return self.discount_amount > 0

    @property
    def in_stock(self):
        return (self.stock > 0) if self.track_stock else True

    @property
    def weight(self) -> int:
        return self.weight_grams if self.weight_grams is not None else self.product.weight_grams

    @property
    def effective_low_stock_threshold(self):
        return self.product.effective_low_stock_threshold

    @property
    def is_low_stock(self):
        return self.track_stock and 0 < self.stock <= self.effective_low_stock_threshold

    @property
    def is_out_of_stock(self):
        return self.track_stock and self.stock <= 0

    @staticmethod
    def key_for(value_ids) -> str:
        return "-".join(str(i) for i in sorted(int(v) for v in value_ids))


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
    # Interface language of the customer when the order was placed (guests included). Anything written for the customer later
    # (order history messages, notifications) by a timer, a gateway callback or staff uses it. Empty on old orders: then the
    # customer's profile language, then the site default.
    language = models.CharField(gettext_lazy("Language"), max_length=10, blank=True, default="", editable=False)
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
    variant = models.ForeignKey("ProductVariant", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    variant_label = models.CharField(gettext_lazy("Variant"), max_length=200, blank=True)  # snapshot: survives deleting the variant
    sku = models.CharField(gettext_lazy("SKU"), max_length=64, blank=True)
    weight_grams = models.PositiveIntegerField(default=0)  # snapshot, per unit (shipping cost by weight uses it)
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


class ShopSettings(TimeStampedModel):
    """Ravix Shop settings of one site. The currency is chosen once: it is locked as soon as the site has a product or an order,
    because every stored amount is an integer in that currency's base unit (see ``money.py``)."""

    site = models.OneToOneField("sites.Site", on_delete=models.CASCADE, related_name="shop_settings")
    currency = models.CharField(gettext_lazy("Currency"), max_length=3, default="IRT")
    show_in_toman = models.BooleanField(gettext_lazy("Show prices in toman"), default=False,
                                        help_text=gettext_lazy("Only for a rial shop: prices are shown and typed in toman, while rials stay stored and are what the gateway receives."))
    low_stock_threshold = models.PositiveIntegerField(gettext_lazy("Low-stock alert at"), default=3)
    notify_customers = models.BooleanField(gettext_lazy("Email customers about order status"), default=True)
    # The store's own details (shown on invoices and emails)
    store_name = models.CharField(gettext_lazy("Store name"), max_length=120, blank=True)
    store_address = models.CharField(gettext_lazy("Store address"), max_length=300, blank=True)
    store_phone = models.CharField(gettext_lazy("Store phone"), max_length=30, blank=True)
    store_email = models.EmailField(gettext_lazy("Store email"), blank=True)

    class Meta:
        verbose_name = "Ravix Shop"

    def __str__(self):
        return f"shop-settings@{self.site_id}"

    @classmethod
    def for_site(cls, site):
        """The site's settings; a site that never saved any gets the deployment default (``RAVIX_SHOP_CURRENCY_CODE``) without writing a row."""
        found = cls.objects.filter(site=site).first()
        if found is not None:
            return found
        code = str(getattr(settings, "SHOP_CURRENCY_CODE", "IRT")).upper()
        return cls(site=site, currency=code if code in ("IRR", "IRT", "USD", "EUR") else "IRT")

    @property
    def toman_display(self) -> bool:
        return self.show_in_toman and self.currency == "IRR"

    def currency_locked(self) -> bool:
        return Product.objects.filter(site_id=self.site_id).exists() or Order.objects.filter(site_id=self.site_id).exists()
