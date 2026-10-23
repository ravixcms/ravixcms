from django.utils.translation import gettext, gettext_lazy
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

BLANK = [("", gettext_lazy("— Select —"))]


class MediaChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.display_title


class PageChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.title if obj.is_live else (gettext("%(title)s (draft)") % {"title": obj.title})


# ----------------------------------------------------------------------------- ظاهر
class AppearanceForm(StyledFormMixin, forms.Form):
    primary = forms.CharField(label=gettext_lazy("Primary color"), widget=forms.TextInput(attrs={"type": "color"}),
                              help_text=gettext_lazy("For buttons, links and emphasis."))
    background = forms.CharField(label=gettext_lazy("Background color"), widget=forms.TextInput(attrs={"type": "color"}))
    text = forms.CharField(label=gettext_lazy("Text color"), widget=forms.TextInput(attrs={"type": "color"}))
    font = forms.ChoiceField(label=gettext_lazy("Font"), choices=[(k, v["label"]) for k, v in FONTS.items()])
    font_size = forms.ChoiceField(label=gettext_lazy("Text size"), choices=[(k, f"{v[0]} ({v[1]}px)") for k, v in FONT_SIZES.items()])
    logo_media = MediaChoice(queryset=Media.objects.filter(kind="image"), required=False, label=gettext_lazy("Site logo"),
                             empty_label=gettext_lazy("— “Site settings” logo (default) —"),
                             help_text=gettext_lazy("Choose from the media library; upload a new image under “Media” first."))
    favicon_media = MediaChoice(queryset=Media.objects.filter(kind="image"), required=False, label=gettext_lazy("Site icon (favicon)"),
                                empty_label=gettext_lazy("— “Site settings” icon (default) —"),
                                help_text=gettext_lazy("A square image (preferably PNG 128×128 or larger)."))

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
    header_show_tagline = forms.BooleanField(label=gettext_lazy("Show the site tagline in the header"), required=False)
    header_sticky = forms.BooleanField(label=gettext_lazy("Keep the header stuck to the top of the page when scrolling"), required=False)
    header_show_search = forms.BooleanField(label=gettext_lazy("Show search in the header"), required=False)
    header_cta_label = forms.CharField(label=gettext_lazy("Header button text"), max_length=30, required=False)
    header_cta_url = forms.CharField(label=gettext_lazy("Header button URL"), max_length=500, required=False,
                                     widget=forms.TextInput(attrs={"dir": "ltr", "placeholder": gettext_lazy("/contact or https://...")}),
                                     help_text=gettext_lazy("The button is shown only when both fields are filled."))
    footer_text = forms.CharField(label=gettext_lazy("Footer text"), max_length=300, required=False,
                                  help_text=gettext_lazy("If left empty, the “Footer text” from the site settings is used."))
    footer_show_menu = forms.BooleanField(label=gettext_lazy("Show the footer menu"), required=False)
    footer_show_contact = forms.BooleanField(label=gettext_lazy("Show contact information (email, phone, address)"), required=False)
    footer_social = forms.CharField(
        label=gettext_lazy("Social media links"), required=False, widget=forms.Textarea(attrs={"rows": 4, "dir": "ltr"}),
        help_text=(gettext_lazy("One link per line, in the form: Title | URL  (at most %(MAX_SOCIAL_LINKS)s items). Example: Instagram | https://instagram.com/example") % {"MAX_SOCIAL_LINKS": MAX_SOCIAL_LINKS}),
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
                raise ValidationError((gettext("Line %(number)s: the correct format is “Title | URL” (title at most 40 characters).") % {"number": number}))
            try:
                url = normalize_link(url, allow_empty=False)
            except ValidationError as exc:
                raise ValidationError((gettext("Line %(number)s: %(messages)s") % {"number": number, "messages": exc.messages[0]})) from exc
            result.append({"label": label, "url": url})
        if len(result) > MAX_SOCIAL_LINKS:
            raise ValidationError((gettext("At most %(MAX_SOCIAL_LINKS)s links are allowed.") % {"MAX_SOCIAL_LINKS": MAX_SOCIAL_LINKS}))
        return result

    def clean(self):
        cleaned = super().clean()
        label, url = cleaned.get("header_cta_label", "").strip(), cleaned.get("header_cta_url", "")
        if bool(label) != bool(url) and "header_cta_url" not in self.errors:
            self.add_error("header_cta_label" if not label else "header_cta_url", gettext("Fill in both the button text and URL, or leave both empty."))
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
    label = forms.CharField(label=gettext_lazy("Title"), max_length=60, required=False)
    kind = forms.ChoiceField(label=gettext_lazy("Link type"), choices=BLANK + list(MENU_KINDS.items()), required=False)
    page = PageChoice(queryset=Page.objects.order_by("title"), required=False, label=gettext_lazy("Page"), empty_label="—")
    category = forms.ModelChoiceField(queryset=Category.objects.order_by("name"), required=False, label=gettext_lazy("Content category"), empty_label="—")
    url = forms.CharField(label=gettext_lazy("URL"), max_length=500, required=False, widget=forms.TextInput(attrs={"dir": "ltr"}))
    new_tab = forms.BooleanField(label=gettext_lazy("In a new tab"), required=False)

    def clean(self):
        cleaned = super().clean()
        kind, label = cleaned.get("kind"), (cleaned.get("label") or "").strip()
        if not kind:
            self.add_error("kind", gettext("Choose the link type."))
            return cleaned
        if not label:
            self.add_error("label", gettext("Enter the menu title."))
        if kind == "page" and not cleaned.get("page"):
            self.add_error("page", gettext("Choose a page."))
        if kind == "category" and not cleaned.get("category"):
            self.add_error("category", gettext("Choose a category."))
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
    type = forms.ChoiceField(label=gettext_lazy("Section type"), choices=BLANK + list(HOME_SECTION_TYPES.items()), required=False)
    hidden = forms.BooleanField(label=gettext_lazy("Temporarily hidden"), required=False)
    title = forms.CharField(label=gettext_lazy("Title"), max_length=120, required=False)
    subtitle = forms.CharField(label=gettext_lazy("Subtitle"), max_length=300, required=False)
    body = forms.CharField(label=gettext_lazy("Body (Markdown)"), max_length=5000, required=False, widget=forms.Textarea(attrs={"rows": 3}))
    button_label = forms.CharField(label=gettext_lazy("Button text"), max_length=40, required=False)
    button_url = forms.CharField(label=gettext_lazy("Button URL"), max_length=500, required=False, widget=forms.TextInput(attrs={"dir": "ltr"}))
    category = forms.ModelChoiceField(queryset=Category.objects.order_by("name"), required=False, label=gettext_lazy("Content category"), empty_label="—")
    page = PageChoice(queryset=Page.objects.order_by("title"), required=False, label=gettext_lazy("Page"), empty_label="—")
    count = forms.IntegerField(label=gettext_lazy("Count"), min_value=1, max_value=24, required=False, widget=forms.NumberInput(attrs={"dir": "ltr"}))

    def clean_button_url(self):
        return normalize_link(self.cleaned_data.get("button_url", ""))

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("type")
        if not kind:
            self.add_error("type", gettext("Choose the section type."))
            return cleaned
        if kind == "category_posts" and not cleaned.get("category"):
            self.add_error("category", gettext("Choose a category."))
        if kind == "page" and not cleaned.get("page"):
            self.add_error("page", gettext("Choose a page."))
        if kind == "text" and not (cleaned.get("body") or "").strip():
            self.add_error("body", gettext("Enter the section text."))
        label, url = (cleaned.get("button_label") or "").strip(), cleaned.get("button_url", "")
        if bool(label) != bool(url) and "button_url" not in self.errors:
            self.add_error("button_label" if not label else "button_url", gettext("Fill in both the button text and URL, or leave both empty."))
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

    theme_zip = forms.FileField(label=gettext_lazy("Theme ZIP file"), allow_empty_file=False, error_messages={"required": gettext_lazy("Choose a ZIP file.")})

