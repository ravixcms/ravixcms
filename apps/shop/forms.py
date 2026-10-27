from django.utils.translation import gettext, gettext_lazy
import re

from django import forms

from django.conf import settings
from django.core.exceptions import ValidationError

from . import money
from .models import DIGITAL_EXTENSIONS, Product, ProductCategory, ProductImage, ShopSettings

PHONE_RE = re.compile(r"^(\+98|0098|0)?9\d{9}$|^0\d{2,3}\d{7,8}$")
_FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


class CheckoutForm(forms.Form):
    """نشانی فقط وقتی الزامی است که سبد کالای فیزیکی داشته باشد (needs_shipping)."""

    customer_name = forms.CharField(label=gettext_lazy("Full name"), min_length=3, max_length=100)
    phone = forms.CharField(label=gettext_lazy("Mobile phone"), max_length=20, widget=forms.TextInput(attrs={"dir": "ltr", "inputmode": "tel", "autocomplete": "tel"}))
    email = forms.EmailField(label=gettext_lazy("Email (optional)"), required=False)
    address = forms.CharField(label=gettext_lazy("URL"), required=False, max_length=500, widget=forms.Textarea(attrs={"rows": 3}))
    note = forms.CharField(label=gettext_lazy("Notes (optional)"), required=False, max_length=300)

    def __init__(self, *args, needs_shipping=True, gateway_choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.needs_shipping = needs_shipping
        if len(gateway_choices) > 1:  # با یک درگاه، انتخابی لازم نیست؛ مقدار هم هرگز از ورودی آزاد نمی‌آید
            self.fields["gateway"] = forms.ChoiceField(label=gettext("Payment gateway"), choices=list(gateway_choices), widget=forms.RadioSelect)
        if not needs_shipping:
            del self.fields["address"]

    def clean_customer_name(self):
        return " ".join(self.cleaned_data["customer_name"].split())

    def clean_phone(self):
        raw = re.sub(r"[\s\-()]", "", self.cleaned_data["phone"].translate(_FA))
        if not PHONE_RE.match(raw):
            raise forms.ValidationError(gettext("The phone number is not valid."))
        return raw

    def clean_address(self):
        value = self.cleaned_data["address"].replace("\r\n", "\n").strip()
        if self.needs_shipping and len(value) < 10:
            raise forms.ValidationError(gettext("A shipping address is required (at least 10 characters)."))
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
        self.shop = ShopSettings.for_site(site)
        # The price is typed in the unit the panel shows (toman for a rial shop in toman mode, dollars for USD ...) and stored in the base unit.
        shown = "IRT" if self.shop.toman_display else self.shop.currency
        self.fields["price"] = forms.CharField(label=gettext("Price") + " (" + str(money.CURRENCIES[shown].name) + ")", widget=forms.TextInput(attrs={"dir": "ltr", "inputmode": "decimal"}))
        if self.instance.pk:
            self.initial["price"] = money.input_value(self.instance.price, self.shop.currency, show_toman=self.shop.toman_display)
        self.fields["slug"].required = False
        self.fields["slug"].help_text = gettext("If left empty, it is generated from the name.")
        self.fields["category"].queryset = ProductCategory.objects.filter(site=site)  # دستهٔ سایت دیگر قابل انتخاب نیست
        self.fields["category"].required = False
        self.fields["kind"].required = False  # پیش‌فرض: فیزیکی
        self.fields["discount_percent"].required = False
        self.fields["stock"].required = False  # برای محصول بدون پیگیری موجودی لازم نیست
        self.fields["digital_file"].help_text = gettext("For digital products only. The file is stored in a private location and can be downloaded only after payment.")

    def clean_price(self):
        raw = str(self.cleaned_data["price"]).translate(_FA).replace("\u066c", "").replace(",", "").replace("\u066b", ".")
        try:
            value = money.parse_amount(raw, self.shop.currency, show_toman=self.shop.toman_display)
        except ValueError as exc:
            raise ValidationError(str(exc)) from None
        if value < 1:
            raise ValidationError(gettext("The price must be greater than zero."))
        return value

    def clean(self):
        data = super().clean()
        from django.utils.text import slugify

        slug = data.get("slug") or slugify(data.get("name", ""), allow_unicode=True)
        if not slug:
            self.add_error("name", gettext("The name must contain at least one letter or digit."))
            return data
        qs = Product.objects.filter(site=self.site, slug=slug).exclude(pk=self.instance.pk)
        if qs.exists():
            self.add_error("slug", gettext("This slug is already used on this site."))
        data["slug"] = slug
        data["kind"] = data.get("kind") or Product.Kind.PHYSICAL
        data["discount_percent"] = data.get("discount_percent") or 0
        data["stock"] = data.get("stock") or 0
        if data.get("kind") == Product.Kind.DIGITAL:
            data["track_stock"] = False  # محصول دیجیتال محدودیت موجودی ندارد
            has_file = data.get("digital_file") or (self.instance.pk and self.instance.digital_file)
            if data.get("is_active") and not has_file:
                self.add_error("digital_file", gettext("To activate a digital product, upload its file."))
        return data

    def clean_digital_file(self):
        f = self.cleaned_data.get("digital_file")
        if f and hasattr(f, "size"):
            if f.size > settings.SHOP_DIGITAL_MAX_BYTES:
                raise ValidationError(gettext("The file is larger than the allowed size."))
            ext = f.name.rsplit(".", 1)[-1].lower() if "." in f.name else ""
            if ext not in DIGITAL_EXTENSIONS:
                raise ValidationError(gettext("The file extension is not allowed."))
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
            self.add_error("name", gettext("The name must contain at least one letter or digit."))
            return data
        if ProductCategory.objects.filter(site=self.site, slug=slug).exclude(pk=self.instance.pk).exists():
            self.add_error("slug", gettext("This slug is already used on this site."))
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

    enabled = forms.BooleanField(label=gettext_lazy("Gateway is enabled"), required=False)
    test_mode = forms.BooleanField(label=gettext_lazy("Test mode (provider sandbox)"), required=False)

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
                note = gettext("The current value is stored; leave empty to keep it.") if has_secret.get(f.key) else ""
                field = forms.CharField(label=f.label, required=False, max_length=f.max_len, strip=True, help_text=(f.help + " " + note).strip(),
                                        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password", "dir": "ltr"}))
                self.fields[f"clear_{f.key}"] = forms.BooleanField(label=(gettext("Delete the value of “%(label)s”") % {"label": f.label}), required=False)
            else:
                field = forms.CharField(label=f.label, required=False, max_length=f.max_len, strip=True, help_text=f.help,
                                        widget=forms.TextInput(attrs={"dir": "ltr"}) if f.type == "url" else forms.TextInput())
            if f.key in self.env_keys:
                field.help_text = ((gettext("%(help_text)s (It comes from an environment variable, which takes precedence over this value.)") % {"help_text": field.help_text})).strip()
            self.fields[f"cfg_{f.key}"] = field

    def clean(self):
        data = super().clean()
        for f in self.gateway_cls.config_fields:
            value = data.get(f"cfg_{f.key}")
            if value and f.type == "url" and not str(value).lower().startswith("https://"):
                self.add_error(f"cfg_{f.key}", gettext("The address must start with https://."))
            if value and f.type == "choice" and value not in {c[0] for c in f.choices}:
                self.add_error(f"cfg_{f.key}", gettext("The value is not allowed."))
        return data


class ShopSettingsForm(forms.ModelForm):
    """Currency can be chosen until the first product or order exists; after that it is read-only (stored amounts would change meaning)."""

    class Meta:
        model = ShopSettings
        fields = ["store_name", "store_address", "store_phone", "store_email", "currency", "show_in_toman", "low_stock_threshold", "notify_customers"]

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.site = site
        self.locked = self.instance.currency_locked() if self.instance.site_id else False
        self.fields["currency"] = forms.ChoiceField(label=gettext("Currency"), choices=money.CHOICES, disabled=self.locked,
                                                    help_text=gettext("Fixed once the shop has a product or an order.") if self.locked else gettext("Rial is recommended for Iran; USD and EUR are stored in cents."))
        self.fields["show_in_toman"].required = False

    def clean(self):
        data = super().clean()
        currency = self.instance.currency if self.locked else data.get("currency")
        if data.get("show_in_toman") and currency != "IRR":
            self.add_error("show_in_toman", gettext("Showing prices in toman is only possible for a rial shop."))
        return data

    def save(self, commit=True):
        self.instance.site = self.site
        return super().save(commit)
