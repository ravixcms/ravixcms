from django.utils.translation import gettext, gettext_lazy
import re

from django import forms

from django.conf import settings
from django.core.exceptions import ValidationError

from . import money
from django.utils.text import slugify

from .models import DIGITAL_EXTENSIONS, AttributeValue, Product, ProductAttribute, ProductCategory, ProductImage, ProductTag, ProductVariant, ShopSettings

PHONE_RE = re.compile(r"^(\+98|0098|0)?9\d{9}$|^0\d{2,3}\d{7,8}$")
_FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def price_input(shop, label=None, required=True):
    """A price typed in the unit the panel shows (toman for a rial shop in toman mode, dollars for USD ...); ``read_price`` turns it into the stored integer."""
    shown = "IRT" if shop.toman_display else shop.currency
    return forms.CharField(label=(label or gettext("Price")) + " (" + str(money.CURRENCIES[shown].name) + ")", required=required, widget=forms.TextInput(attrs={"dir": "ltr", "inputmode": "decimal"}))


def read_price(raw, shop) -> int:
    raw = str(raw).translate(_FA).replace("\u066c", "").replace(",", "").replace("\u066b", ".")
    try:
        value = money.parse_amount(raw, shop.currency, show_toman=shop.toman_display)
    except ValueError as exc:
        raise ValidationError(str(exc)) from None
    if value < 1:
        raise ValidationError(gettext("The price must be greater than zero."))
    return value


def sku_taken(site, sku, *, product=None, variant=None) -> bool:
    """SKUs are unique across the products and variants of a site."""
    if not sku:
        return False
    products = Product.objects.filter(site=site, sku=sku)
    variants = ProductVariant.objects.filter(site=site, sku=sku)
    if product is not None and product.pk:
        products = products.exclude(pk=product.pk)
    if variant is not None and variant.pk:
        variants = variants.exclude(pk=variant.pk)
    return products.exists() or variants.exists()


class CategoryChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.path_name


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
        fields = ["name", "slug", "kind", "sku", "category", "description", "price", "discount_percent", "weight_grams", "track_stock", "stock", "low_stock_threshold",
                  "variation_attributes", "related", "is_active", "image", "digital_file"]

    tags_text = forms.CharField(label=gettext_lazy("Tags"), required=False, max_length=400, help_text=gettext_lazy("Separate tags with a comma."))

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.site = site
        self.shop = ShopSettings.for_site(site)
        # The price is typed in the unit the panel shows (toman for a rial shop in toman mode, dollars for USD ...) and stored in the base unit.
        self.fields["price"] = price_input(self.shop)
        if self.instance.pk:
            self.initial["price"] = money.input_value(self.instance.price, self.shop.currency, show_toman=self.shop.toman_display)
            self.initial["tags_text"] = ", ".join(self.instance.tags.values_list("name", flat=True))
        self.fields["category"] = CategoryChoice(queryset=ProductCategory.objects.filter(site=site), required=False, label=gettext("Category"))
        self.fields["variation_attributes"].queryset = ProductAttribute.objects.filter(site=site)
        self.fields["variation_attributes"].required = False
        self.fields["variation_attributes"].help_text = gettext("For a variable product: the attributes its variants differ by (e.g. colour, size).")
        self.fields["related"].queryset = Product.objects.filter(site=site).exclude(pk=self.instance.pk or 0)
        self.fields["related"].required = False
        self.fields["sku"].required = False
        self.fields["weight_grams"].required = False
        self.fields["low_stock_threshold"].required = False
        self.fields["slug"].required = False
        self.fields["slug"].help_text = gettext("If left empty, it is generated from the name.")
        self.fields["kind"].required = False  # پیش‌فرض: فیزیکی
        self.fields["discount_percent"].required = False
        self.fields["stock"].required = False  # برای محصول بدون پیگیری موجودی لازم نیست
        self.fields["digital_file"].help_text = gettext("For digital products only. The file is stored in a private location and can be downloaded only after payment.")

    def clean_price(self):
        return read_price(self.cleaned_data["price"], self.shop)

    def clean_sku(self):
        sku = (self.cleaned_data.get("sku") or "").strip()
        if sku_taken(self.site, sku, product=self.instance):
            raise ValidationError(gettext("This SKU is already used on this site."))
        return sku

    def clean_weight_grams(self):
        return self.cleaned_data.get("weight_grams") or 0

    def clean_related(self):
        return self.cleaned_data["related"].exclude(pk=self.instance.pk or 0)

    def clean_tags_text(self):
        names, seen = [], set()
        for part in re.split(r"[,\u060c;\n]", self.cleaned_data.get("tags_text") or ""):
            name = " ".join(part.split())[:60]
            slug = slugify(name, allow_unicode=True)
            if name and slug and slug not in seen:
                seen.add(slug)
                names.append((name, slug))
        if len(names) > 20:
            raise ValidationError(gettext("At most 20 tags per product."))
        return names

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
        if data.get("kind") == Product.Kind.VARIABLE:
            data["track_stock"] = False  # stock is kept per variant
            if not data.get("variation_attributes"):
                self.add_error("variation_attributes", gettext("Choose at least one attribute for a variable product."))
        else:
            data["variation_attributes"] = ProductAttribute.objects.none()
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
        product = super().save(commit)
        if commit:
            tags = []
            for name, slug in self.cleaned_data.get("tags_text", []):
                tag = ProductTag.objects.filter(site=self.site, slug=slug).first() or ProductTag.objects.create(site=self.site, name=name, slug=slug)
                tags.append(tag)
            product.tags.set(tags)
            product.sync_variable_price()
        return product


class CategoryForm(forms.ModelForm):
    class Meta:
        model = ProductCategory
        fields = ["name", "slug", "parent", "order"]

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.site = site
        self.fields["slug"].required = False
        blocked = self.instance.descendant_ids() if self.instance.pk else []  # a category cannot move under itself or its own children
        self.fields["parent"] = CategoryChoice(queryset=ProductCategory.objects.filter(site=site).exclude(pk__in=blocked), required=False, label=gettext("Parent category"))

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
        parent = data.get("parent")
        if parent is not None:
            if parent.site_id != self.site.pk or (self.instance.pk and parent.pk in self.instance.descendant_ids()):
                self.add_error("parent", gettext("A category cannot be placed under itself or one of its own sub-categories."))
            else:
                height = self.instance.subtree_height() if self.instance.pk else 0
                if parent.depth + 1 + height > ProductCategory.MAX_DEPTH:
                    self.add_error("parent", gettext("Categories can be nested at most %(levels)s levels deep.") % {"levels": ProductCategory.MAX_DEPTH})
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



class VariantForm(forms.ModelForm):
    class Meta:
        model = ProductVariant
        fields = ["sku", "price", "stock", "track_stock", "weight_grams", "image", "is_active"]

    def __init__(self, *args, product=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.product = product or self.instance.product
        self.site = self.product.site
        self.shop = ShopSettings.for_site(self.site)
        self.fields["price"] = price_input(self.shop)
        if self.instance.pk:
            self.initial["price"] = money.input_value(self.instance.price, self.shop.currency, show_toman=self.shop.toman_display)
        for name in ("sku", "stock", "weight_grams", "image"):
            self.fields[name].required = False

    def clean_price(self):
        return read_price(self.cleaned_data["price"], self.shop)

    def clean_sku(self):
        sku = (self.cleaned_data.get("sku") or "").strip()
        if sku_taken(self.site, sku, variant=self.instance):
            raise ValidationError(gettext("This SKU is already used on this site."))
        return sku

    def clean_stock(self):
        return self.cleaned_data.get("stock") or 0


class BaseVariantFormSet(forms.BaseModelFormSet):
    def clean(self):
        super().clean()
        seen = {}
        for form in self.forms:
            sku = (form.cleaned_data.get("sku") or "") if hasattr(form, "cleaned_data") else ""
            if sku and not form.cleaned_data.get("DELETE"):
                if sku in seen:
                    form.add_error("sku", gettext("This SKU is already used on this site."))
                seen[sku] = True


class AttributeForm(forms.Form):
    """An attribute and its values (one per line). A value that variants still use cannot be removed."""

    name = forms.CharField(label=gettext_lazy("Attribute name"), max_length=60)
    slug = forms.CharField(label=gettext_lazy("Slug"), required=False, max_length=80)
    values = forms.CharField(label=gettext_lazy("Values"), widget=forms.Textarea(attrs={"rows": 6}), help_text=gettext_lazy("One value per line, e.g. Red, Green, Blue."))

    def __init__(self, *args, site, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.site, self.instance = site, instance
        if instance is not None and not self.is_bound:
            self.initial = {"name": instance.name, "slug": instance.slug, "values": "\n".join(instance.values.values_list("value", flat=True))}

    def clean(self):
        data = super().clean()
        name = data.get("name", "")
        slug = data.get("slug") or slugify(name, allow_unicode=True)
        if not slug:
            self.add_error("name", gettext("The name must contain at least one letter or digit."))
            return data
        if ProductAttribute.objects.filter(site=self.site, slug=slug).exclude(pk=self.instance.pk if self.instance else 0).exists():
            self.add_error("slug", gettext("This slug is already used on this site."))
        values, seen = [], set()
        for line in (data.get("values") or "").splitlines():
            value = " ".join(line.split())[:80]
            vslug = slugify(value, allow_unicode=True)
            if value and vslug and vslug not in seen:
                seen.add(vslug)
                values.append((value, vslug))
        if not values:
            self.add_error("values", gettext("Enter at least one value."))
        if len(values) > 50:
            self.add_error("values", gettext("At most 50 values per attribute."))
        if self.instance is not None and values:
            keep = {v for _, v in values}
            used = AttributeValue.objects.filter(attribute=self.instance, variants__isnull=False).exclude(slug__in=keep).distinct()
            if used.exists():
                self.add_error("values", gettext("These values are used by variants and cannot be removed: %(values)s") % {"values": ", ".join(v.value for v in used)})
        data["slug"], data["parsed_values"] = slug, values
        return data

    def save(self):
        data = self.cleaned_data
        attr = self.instance or ProductAttribute(site=self.site)
        attr.name, attr.slug = data["name"], data["slug"]
        attr.save()
        existing = {v.slug: v for v in attr.values.all()}
        keep = set()
        for order, (value, vslug) in enumerate(data["parsed_values"]):
            obj = existing.get(vslug) or AttributeValue(attribute=attr, slug=vslug)
            obj.value, obj.order = value, order
            obj.save()
            keep.add(vslug)
        attr.values.exclude(slug__in=keep).delete()
        return attr
