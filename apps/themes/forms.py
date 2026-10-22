from django import forms
from django.core.exceptions import ValidationError

from apps.content.models import Category, Page
from apps.core.forms import StyledFormMixin
from apps.media_library.models import Media

from .colors import palette_errors
from .constants import (
    FONT_SIZES,
    FONTS,
    HOME_SECTION_TYPES,
    MAX_MENU_ITEMS,
    MAX_SECTIONS,
    MAX_SOCIAL_LINKS,
    MENU_KINDS,
)
from .validators import normalize_color, normalize_link

BLANK = [("", "— انتخاب کنید —")]


class MediaChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.display_title


class PageChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.title if obj.is_live else f"{obj.title} (پیش‌نویس)"


# ----------------------------------------------------------------------------- ظاهر
class AppearanceForm(StyledFormMixin, forms.Form):
    primary = forms.CharField(label="رنگ اصلی", widget=forms.TextInput(attrs={"type": "color"}),
                              help_text="برای دکمه‌ها، پیوندها و تأکیدها.")
    background = forms.CharField(label="رنگ پس‌زمینه", widget=forms.TextInput(attrs={"type": "color"}))
    text = forms.CharField(label="رنگ متن", widget=forms.TextInput(attrs={"type": "color"}))
    font = forms.ChoiceField(label="فونت", choices=[(k, v["label"]) for k, v in FONTS.items()])
    font_size = forms.ChoiceField(label="اندازه متن", choices=[(k, f"{v[0]} ({v[1]}px)") for k, v in FONT_SIZES.items()])
    logo_media = MediaChoice(queryset=Media.objects.filter(kind="image"), required=False, label="لوگوی سایت",
                             empty_label="— لوگوی «تنظیمات سایت» (پیش‌فرض) —",
                             help_text="از کتابخانه رسانه انتخاب کنید؛ تصویر جدید را ابتدا در «رسانه‌ها» بارگذاری کنید.")
    favicon_media = MediaChoice(queryset=Media.objects.filter(kind="image"), required=False, label="آیکون سایت (Favicon)",
                                empty_label="— آیکون «تنظیمات سایت» (پیش‌فرض) —",
                                help_text="تصویر مربعی (ترجیحاً PNG ۱۲۸×۱۲۸ یا بزرگ‌تر).")

    @classmethod
    def from_doc(cls, doc: dict, **kwargs):
        a = doc["appearance"]
        return cls(initial={**a}, **kwargs)

    def _color(self, name):
        if name in self.errors:
            return None
        try:
            return normalize_color(self.cleaned_data.get(name, ""))
        except ValidationError as exc:
            self.add_error(name, exc)
            return None

    def clean(self):
        cleaned = super().clean()
        colors = {n: self._color(n) for n in ("primary", "background", "text")}
        if all(colors.values()):
            for message in palette_errors(colors["primary"], colors["background"], colors["text"]):
                self.add_error(None, message)
            cleaned.update(colors)
        return cleaned

    def to_doc(self) -> dict:
        d = self.cleaned_data
        return {
            "primary": d["primary"], "background": d["background"], "text": d["text"],
            "font": d["font"], "font_size": d["font_size"],
            "logo_media": d["logo_media"].pk if d.get("logo_media") else None,
            "favicon_media": d["favicon_media"].pk if d.get("favicon_media") else None,
        }


# ----------------------------------------------------------------------------- سربرگ و پانویس
class LayoutForm(StyledFormMixin, forms.Form):
    header_show_tagline = forms.BooleanField(label="نمایش شعار سایت در سربرگ", required=False)
    header_sticky = forms.BooleanField(label="چسبیدن سربرگ به بالای صفحه هنگام اسکرول", required=False)
    header_show_search = forms.BooleanField(label="نمایش جست‌وجو در سربرگ", required=False)
    header_cta_label = forms.CharField(label="متن دکمه سربرگ", max_length=30, required=False)
    header_cta_url = forms.CharField(label="نشانی دکمه سربرگ", max_length=500, required=False,
                                     widget=forms.TextInput(attrs={"dir": "ltr", "placeholder": "/contact یا https://..."}),
                                     help_text="دکمه فقط وقتی نمایش داده می‌شود که هر دو بخش پر باشند.")
    footer_text = forms.CharField(label="متن پانویس", max_length=300, required=False,
                                  help_text="در صورت خالی بودن، «متن پانویس» تنظیمات سایت استفاده می‌شود.")
    footer_show_menu = forms.BooleanField(label="نمایش منوی پانویس", required=False)
    footer_show_contact = forms.BooleanField(label="نمایش اطلاعات تماس (ایمیل، تلفن، نشانی)", required=False)
    footer_social = forms.CharField(
        label="پیوندهای شبکه‌های اجتماعی", required=False, widget=forms.Textarea(attrs={"rows": 4, "dir": "ltr"}),
        help_text=f"هر خط یک پیوند، به شکل: عنوان | نشانی  (حداکثر {MAX_SOCIAL_LINKS} مورد). مثال: Instagram | https://instagram.com/example",
    )

    @classmethod
    def from_doc(cls, doc: dict, **kwargs):
        h, f = doc["header"], doc["footer"]
        initial = {
            "header_show_tagline": h["show_tagline"], "header_sticky": h["sticky"], "header_show_search": h["show_search"],
            "header_cta_label": h["cta_label"], "header_cta_url": h["cta_url"],
            "footer_text": f["text"], "footer_show_menu": f["show_menu"], "footer_show_contact": f["show_contact"],
            "footer_social": "\n".join(f"{s['label']} | {s['url']}" for s in f["social"]),
        }
        return cls(initial=initial, **kwargs)

    def clean_header_cta_url(self):
        return normalize_link(self.cleaned_data.get("header_cta_url", ""))

    def clean_footer_social(self):
        result = []
        for number, line in enumerate(self.cleaned_data.get("footer_social", "").splitlines(), start=1):
            if not line.strip():
                continue
            label, sep, url = line.partition("|")
            label = label.strip()
            if not sep or not label or len(label) > 40:
                raise ValidationError(f"خط {number}: قالب درست «عنوان | نشانی» است (عنوان حداکثر ۴۰ نویسه).")
            try:
                url = normalize_link(url, allow_empty=False)
            except ValidationError as exc:
                raise ValidationError(f"خط {number}: {exc.messages[0]}") from exc
            result.append({"label": label, "url": url})
        if len(result) > MAX_SOCIAL_LINKS:
            raise ValidationError(f"حداکثر {MAX_SOCIAL_LINKS} پیوند مجاز است.")
        return result

    def clean(self):
        cleaned = super().clean()
        label, url = cleaned.get("header_cta_label", "").strip(), cleaned.get("header_cta_url", "")
        if bool(label) != bool(url) and "header_cta_url" not in self.errors:
            self.add_error("header_cta_label" if not label else "header_cta_url", "متن و نشانی دکمه را هم‌زمان پر کنید یا هر دو را خالی بگذارید.")
        return cleaned

    def to_docs(self) -> tuple[dict, dict]:
        d = self.cleaned_data
        header = {
            "show_tagline": d["header_show_tagline"], "sticky": d["header_sticky"], "show_search": d["header_show_search"],
            "cta_label": d["header_cta_label"].strip(), "cta_url": d["header_cta_url"],
        }
        footer = {
            "text": d["footer_text"].strip(), "show_menu": d["footer_show_menu"],
            "show_contact": d["footer_show_contact"], "social": d["footer_social"],
        }
        return header, footer


# ----------------------------------------------------------------------------- منوها
class MenuItemForm(StyledFormMixin, forms.Form):
    label = forms.CharField(label="عنوان", max_length=60, required=False)
    kind = forms.ChoiceField(label="نوع پیوند", choices=BLANK + list(MENU_KINDS.items()), required=False)
    page = PageChoice(queryset=Page.objects.order_by("title"), required=False, label="صفحه", empty_label="—")
    category = forms.ModelChoiceField(queryset=Category.objects.order_by("name"), required=False, label="دسته‌بندی", empty_label="—")
    url = forms.CharField(label="نشانی", max_length=500, required=False, widget=forms.TextInput(attrs={"dir": "ltr"}))
    new_tab = forms.BooleanField(label="در زبانه جدید", required=False)

    def clean(self):
        cleaned = super().clean()
        kind, label = cleaned.get("kind"), (cleaned.get("label") or "").strip()
        if not kind:
            self.add_error("kind", "نوع پیوند را انتخاب کنید.")
            return cleaned
        if not label:
            self.add_error("label", "عنوان منو را وارد کنید.")
        if kind == "page" and not cleaned.get("page"):
            self.add_error("page", "یک صفحه انتخاب کنید.")
        if kind == "category" and not cleaned.get("category"):
            self.add_error("category", "یک دسته‌بندی انتخاب کنید.")
        if kind == "url":
            try:
                cleaned["url"] = normalize_link(cleaned.get("url", ""), allow_empty=False)
            except ValidationError as exc:
                self.add_error("url", exc)
        return cleaned

    def to_item(self) -> dict:
        d = self.cleaned_data
        kind = d["kind"]
        return {
            "label": d["label"].strip(), "kind": kind,
            "target": d["page"].pk if kind == "page" else d["category"].pk if kind == "category" else None,
            "url": d["url"] if kind == "url" else "", "new_tab": d["new_tab"],
        }


def menu_initial(items: list[dict]) -> list[dict]:
    return [
        {
            "label": i["label"], "kind": i["kind"], "page": i["target"] if i["kind"] == "page" else None,
            "category": i["target"] if i["kind"] == "category" else None, "url": i["url"], "new_tab": i["new_tab"],
        }
        for i in items
    ]


MenuFormSet = forms.formset_factory(
    MenuItemForm, extra=2, can_order=True, can_delete=True, max_num=MAX_MENU_ITEMS, validate_max=True,
)


def menu_from_formset(formset) -> list[dict]:
    return [form.to_item() for form in formset.ordered_forms]


# ----------------------------------------------------------------------------- بخش‌های صفحه اصلی
class SectionForm(StyledFormMixin, forms.Form):
    type = forms.ChoiceField(label="نوع بخش", choices=BLANK + list(HOME_SECTION_TYPES.items()), required=False)
    hidden = forms.BooleanField(label="موقتاً پنهان باشد", required=False)
    title = forms.CharField(label="عنوان", max_length=120, required=False)
    subtitle = forms.CharField(label="زیرعنوان", max_length=300, required=False)
    body = forms.CharField(label="متن (Markdown)", max_length=5000, required=False, widget=forms.Textarea(attrs={"rows": 3}))
    button_label = forms.CharField(label="متن دکمه", max_length=40, required=False)
    button_url = forms.CharField(label="نشانی دکمه", max_length=500, required=False, widget=forms.TextInput(attrs={"dir": "ltr"}))
    category = forms.ModelChoiceField(queryset=Category.objects.order_by("name"), required=False, label="دسته‌بندی", empty_label="—")
    page = PageChoice(queryset=Page.objects.order_by("title"), required=False, label="صفحه", empty_label="—")
    count = forms.IntegerField(label="تعداد", min_value=1, max_value=24, required=False, widget=forms.NumberInput(attrs={"dir": "ltr"}))

    def clean_button_url(self):
        return normalize_link(self.cleaned_data.get("button_url", ""))

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("type")
        if not kind:
            self.add_error("type", "نوع بخش را انتخاب کنید.")
            return cleaned
        if kind == "category_posts" and not cleaned.get("category"):
            self.add_error("category", "یک دسته‌بندی انتخاب کنید.")
        if kind == "page" and not cleaned.get("page"):
            self.add_error("page", "یک صفحه انتخاب کنید.")
        if kind == "text" and not (cleaned.get("body") or "").strip():
            self.add_error("body", "متن بخش را وارد کنید.")
        label, url = (cleaned.get("button_label") or "").strip(), cleaned.get("button_url", "")
        if bool(label) != bool(url) and "button_url" not in self.errors:
            self.add_error("button_label" if not label else "button_url", "متن و نشانی دکمه را هم‌زمان پر کنید یا هر دو را خالی بگذارید.")
        return cleaned

    def to_section(self) -> dict:
        d = self.cleaned_data
        return {
            "type": d["type"], "enabled": not d["hidden"], "title": d["title"].strip(), "subtitle": d["subtitle"].strip(),
            "body": d["body"].strip(), "button_label": d["button_label"].strip(), "button_url": d["button_url"],
            "category": d["category"].pk if d.get("category") else None, "page": d["page"].pk if d.get("page") else None,
            "count": d.get("count") or 6,
        }


def section_initial(sections: list[dict]) -> list[dict]:
    return [{**s, "hidden": not s["enabled"]} for s in sections]


SectionFormSet = forms.formset_factory(
    SectionForm, extra=1, can_order=True, can_delete=True, max_num=MAX_SECTIONS, validate_max=True,
)


def sections_from_formset(formset) -> list[dict]:
    return [form.to_section() for form in formset.ordered_forms]


class ThemeUploadForm(forms.Form):
    """فرم بارگذاری ZIP؛ بررسی واقعی (امضای ZIP، سقف‌ها، ساختار) در apps.themes.installer انجام می‌شود، نه اینجا."""

    theme_zip = forms.FileField(label="فایل ZIP قالب", allow_empty_file=False, error_messages={"required": "یک فایل ZIP انتخاب کنید."})

