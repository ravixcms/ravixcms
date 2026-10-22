import re

from django import forms

from django.conf import settings
from django.core.exceptions import ValidationError

from .models import DIGITAL_EXTENSIONS, Product, ProductCategory, ProductImage

PHONE_RE = re.compile(r"^(\+98|0098|0)?9\d{9}$|^0\d{2,3}\d{7,8}$")
_FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


class CheckoutForm(forms.Form):
    """نشانی فقط وقتی الزامی است که سبد کالای فیزیکی داشته باشد (needs_shipping)."""

    customer_name = forms.CharField(label="نام و نام‌خانوادگی", min_length=3, max_length=100)
    phone = forms.CharField(label="تلفن همراه", max_length=20, widget=forms.TextInput(attrs={"dir": "ltr", "inputmode": "tel", "autocomplete": "tel"}))
    email = forms.EmailField(label="ایمیل (اختیاری)", required=False)
    address = forms.CharField(label="نشانی", required=False, max_length=500, widget=forms.Textarea(attrs={"rows": 3}))
    note = forms.CharField(label="توضیحات (اختیاری)", required=False, max_length=300)

    def __init__(self, *args, needs_shipping=True, gateway_choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.needs_shipping = needs_shipping
        if len(gateway_choices) > 1:  # با یک درگاه، انتخابی لازم نیست؛ مقدار هم هرگز از ورودی آزاد نمی‌آید
            self.fields["gateway"] = forms.ChoiceField(label="درگاه پرداخت", choices=list(gateway_choices), widget=forms.RadioSelect)
        if not needs_shipping:
            del self.fields["address"]

    def clean_customer_name(self):
        return " ".join(self.cleaned_data["customer_name"].split())

    def clean_phone(self):
        raw = re.sub(r"[\s\-()]", "", self.cleaned_data["phone"].translate(_FA))
        if not PHONE_RE.match(raw):
            raise forms.ValidationError("شمارهٔ تلفن معتبر نیست.")
        return raw

    def clean_address(self):
        value = self.cleaned_data["address"].replace("\r\n", "\n").strip()
        if self.needs_shipping and len(value) < 10:
            raise forms.ValidationError("نشانی برای ارسال کالا الزامی است (حداقل ۱۰ نویسه).")
        return value

    def clean_note(self):
        return " ".join(self.cleaned_data["note"].split())


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["name", "slug", "kind", "category", "description", "price", "discount_percent", "track_stock", "stock", "is_active", "image", "digital_file"]

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.site = site
        self.fields["slug"].required = False
        self.fields["slug"].help_text = "اگر خالی بماند از نام ساخته می‌شود."
        self.fields["category"].queryset = ProductCategory.objects.filter(site=site)  # دستهٔ سایت دیگر قابل انتخاب نیست
        self.fields["category"].required = False
        self.fields["kind"].required = False  # پیش‌فرض: فیزیکی
        self.fields["discount_percent"].required = False
        self.fields["stock"].required = False  # برای محصول بدون پیگیری موجودی لازم نیست
        self.fields["digital_file"].help_text = "فقط برای محصول دیجیتال. فایل در مسیر خصوصی ذخیره می‌شود و فقط پس از پرداخت قابل دانلود است."

    def clean(self):
        data = super().clean()
        from django.utils.text import slugify

        slug = data.get("slug") or slugify(data.get("name", ""), allow_unicode=True)
        if not slug:
            self.add_error("name", "نام باید حداقل یک حرف یا عدد داشته باشد.")
            return data
        qs = Product.objects.filter(site=self.site, slug=slug).exclude(pk=self.instance.pk)
        if qs.exists():
            self.add_error("slug", "این نامک در این سایت قبلاً استفاده شده است.")
        data["slug"] = slug
        data["kind"] = data.get("kind") or Product.Kind.PHYSICAL
        data["discount_percent"] = data.get("discount_percent") or 0
        data["stock"] = data.get("stock") or 0
        if data.get("kind") == Product.Kind.DIGITAL:
            data["track_stock"] = False  # محصول دیجیتال محدودیت موجودی ندارد
            has_file = data.get("digital_file") or (self.instance.pk and self.instance.digital_file)
            if data.get("is_active") and not has_file:
                self.add_error("digital_file", "برای فعال‌کردن محصول دیجیتال، فایل را بارگذاری کنید.")
        return data

    def clean_digital_file(self):
        f = self.cleaned_data.get("digital_file")
        if f and hasattr(f, "size"):
            if f.size > settings.SHOP_DIGITAL_MAX_BYTES:
                raise ValidationError("حجم فایل بیشتر از حد مجاز است.")
            ext = f.name.rsplit(".", 1)[-1].lower() if "." in f.name else ""
            if ext not in DIGITAL_EXTENSIONS:
                raise ValidationError("پسوند فایل مجاز نیست.")
        return f

    def save(self, commit=True):
        self.instance.site = self.site  # سایت هرگز از ورودی کاربر نمی‌آید
        self.instance.slug = self.cleaned_data["slug"]
        return super().save(commit)


class CategoryForm(forms.ModelForm):
    class Meta:
        model = ProductCategory
        fields = ["name", "slug", "order"]

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.site = site
        self.fields["slug"].required = False

    def clean(self):
        data = super().clean()
        from django.utils.text import slugify

        slug = data.get("slug") or slugify(data.get("name", ""), allow_unicode=True)
        if not slug:
            self.add_error("name", "نام باید حداقل یک حرف یا عدد داشته باشد.")
            return data
        if ProductCategory.objects.filter(site=self.site, slug=slug).exclude(pk=self.instance.pk).exists():
            self.add_error("slug", "این نامک در این سایت قبلاً استفاده شده است.")
        data["slug"] = slug
        return data

    def save(self, commit=True):
        self.instance.site = self.site
        self.instance.slug = self.cleaned_data["slug"]
        return super().save(commit)


class ProductImageForm(forms.ModelForm):
    class Meta:
        model = ProductImage
        fields = ["image", "alt", "order"]


class GatewayConfigForm(forms.Form):
    """فرم پویا از ``config_fields`` درگاه. فیلد محرمانه هرگز با مقدار پر نمی‌شود؛ خالی = بدون تغییر، «حذف» = پاک‌کردن."""

    enabled = forms.BooleanField(label="درگاه فعال باشد", required=False)
    test_mode = forms.BooleanField(label="حالت آزمایشی (sandbox ارائه‌دهنده)", required=False)

    def __init__(self, *args, gateway_cls, has_secret, env_keys=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.gateway_cls = gateway_cls
        self.has_secret = has_secret  # {key: bool}
        self.env_keys = set(env_keys)
        for f in gateway_cls.config_fields:
            if f.type == "bool":
                field = forms.BooleanField(label=f.label, required=False, help_text=f.help)
            elif f.type == "choice":
                field = forms.ChoiceField(label=f.label, choices=list(f.choices), required=False, help_text=f.help)
            elif f.secret:
                note = "مقدار فعلی ذخیره شده است؛ برای حفظ آن خالی بگذارید." if has_secret.get(f.key) else ""
                field = forms.CharField(label=f.label, required=False, max_length=f.max_len, strip=True, help_text=(f.help + " " + note).strip(),
                                        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password", "dir": "ltr"}))
                self.fields[f"clear_{f.key}"] = forms.BooleanField(label=f"حذف مقدار «{f.label}»", required=False)
            else:
                field = forms.CharField(label=f.label, required=False, max_length=f.max_len, strip=True, help_text=f.help,
                                        widget=forms.TextInput(attrs={"dir": "ltr"}) if f.type == "url" else forms.TextInput())
            if f.key in self.env_keys:
                field.help_text = (field.help_text + " (از متغیر محیطی تأمین می‌شود و روی این مقدار مقدم است.)").strip()
            self.fields[f"cfg_{f.key}"] = field

    def clean(self):
        data = super().clean()
        for f in self.gateway_cls.config_fields:
            value = data.get(f"cfg_{f.key}")
            if value and f.type == "url" and not str(value).lower().startswith("https://"):
                self.add_error(f"cfg_{f.key}", "آدرس باید با https:// شروع شود.")
            if value and f.type == "choice" and value not in {c[0] for c in f.choices}:
                self.add_error(f"cfg_{f.key}", "مقدار مجاز نیست.")
        return data
