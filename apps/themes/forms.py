from django.utils.translation import gettext, gettext_lazy
from django import forms
from django.core.exceptions import ValidationError
from django.utils.functional import lazy

from apps.content.models import Category, Page
from apps.core.forms import StyledFormMixin
from apps.media_library.models import Media

from .colors import palette_errors
from .constants import (
    FEATURE_ICONS,
    FONT_SIZES,
    FONTS,
    HOME_SECTION_TYPES,
    MAX_FEATURE_ITEMS,
    MAX_MENU_ITEMS,
    MAX_SECTION_BUTTONS,
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
    translations = forms.CharField(
        label=gettext_lazy("Other languages"), max_length=3000, required=False, widget=forms.Textarea(attrs={"rows": 2, "dir": "auto"}),
        help_text=gettext_lazy("One language per line: language code | title | destination (the slug of a page or category, or a link). "
                               "The destination may be left empty. Example: en | About | about-ravix"))

    def clean(self):
        cleaned = super().clean()
        kind, label = cleaned.get("kind"), (cleaned.get("label") or "").strip()
        if cleaned.get("kind"):
            cleaned["translations_parsed"] = self._parse_translations(cleaned["kind"], cleaned.get("translations", ""))
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

    def _parse_translations(self, kind: str, text: str) -> dict:
        from apps.core.regional import normalize_language

        result = {}
        for number, line in enumerate((text or "").splitlines(), 1):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split("|", 2)]
            code = normalize_language(parts[0])
            label = parts[1] if len(parts) > 1 else ""
            dest = parts[2] if len(parts) > 2 else ""
            if not code:
                self.add_error("translations", gettext("Line %(line)s: unknown language “%(code)s”.") % {"line": number, "code": parts[0]})
                continue
            if len(label) > 60:
                self.add_error("translations", gettext("Line %(line)s: the title may have up to 60 characters.") % {"line": number})
                continue
            entry = {"label": label}
            if dest and kind in ("page", "category"):
                model = Page if kind == "page" else Category
                obj = model.objects.filter(slug=dest).first()
                if obj is None:
                    self.add_error("translations", gettext("Line %(line)s: there is no page or category with the slug “%(slug)s”.") % {"line": number, "slug": dest})
                    continue
                entry["target"] = obj.pk
            elif dest and kind == "url":
                try:
                    entry["url"] = normalize_link(dest, allow_empty=False)
                except ValidationError as exc:
                    self.add_error("translations", exc)
                    continue
            if entry["label"] or len(entry) > 1:
                result[code] = entry
        return result

    def to_item(self) -> dict:
        d = self.cleaned_data
        kind = d["kind"]
        return {
            "label": d["label"].strip(), "kind": kind,
            "target": d["page"].pk if kind == "page" else d["category"].pk if kind == "category" else None,
            "url": d["url"] if kind == "url" else "", "new_tab": d["new_tab"],
            "translations": d.get("translations_parsed", {}),
        }


def menu_translations_text(item: dict) -> str:
    """The "Other languages" box of a menu item: ``code | title | destination`` per line (the destination as a slug or link)."""
    kind = item["kind"]
    ids = {t.get("target") for t in item.get("translations", {}).values() if t.get("target")}
    model = Page if kind == "page" else Category if kind == "category" else None
    slugs = dict(model.objects.filter(pk__in=ids).values_list("pk", "slug")) if model and ids else {}
    lines = []
    for code, t in item.get("translations", {}).items():
        dest = slugs.get(t.get("target"), "") if model else t.get("url", "")
        lines.append(f"{code} | {t.get('label', '')} | {dest}")
    return "\n".join(lines)


def menu_initial(items: list[dict]) -> list[dict]:
    return [
        {
            "label": i["label"], "kind": i["kind"], "page": i["target"] if i["kind"] == "page" else None,
            "category": i["target"] if i["kind"] == "category" else None, "url": i["url"], "new_tab": i["new_tab"],
            "translations": menu_translations_text(i),
        }
        for i in items
    ]


MenuFormSet = forms.formset_factory(
    MenuItemForm, extra=2, can_order=True, can_delete=True, max_num=MAX_MENU_ITEMS, validate_max=True,
)


def menu_from_formset(formset) -> list[dict]:
    return [form.to_item() for form in formset.ordered_forms]


# ----------------------------------------------------------------------------- بخش‌های صفحه اصلی
def parse_items(text: str) -> tuple[list[dict], str]:
    """One item per line: ``icon | title | short text`` (``title | text`` and ``title`` also work). → (items, error message)."""
    items = []
    for number, line in enumerate((text or "").splitlines(), 1):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split("|", 2)]
        icon = ""
        if len(parts) == 3:
            icon, title, body = parts
            if icon and icon not in FEATURE_ICONS:
                return [], (gettext("Line %(line)s: unknown icon “%(icon)s”. Available icons: %(icons)s.") % {"line": number, "icon": icon, "icons": ", ".join(FEATURE_ICONS)})
        elif len(parts) == 2:
            title, body = parts
        else:
            title, body = parts[0], ""
        if len(title) > 60 or len(body) > 300:
            return [], (gettext("Line %(line)s is too long: the title may have up to 60 and the text up to 300 characters.") % {"line": number})
        if title or body:
            items.append({"title": title, "icon": icon, "text": body})
    if len(items) > MAX_FEATURE_ITEMS:
        return [], (gettext("At most %(count)s items are allowed.") % {"count": MAX_FEATURE_ITEMS})
    return items, ""


def items_to_text(items) -> str:
    return "\n".join(f"{i.get('icon', '')} | {i.get('title', '')} | {i.get('text', '')}" for i in items or [])


class SectionForm(StyledFormMixin, forms.Form):
    type = forms.ChoiceField(label=gettext_lazy("Section type"), choices=BLANK + list(HOME_SECTION_TYPES.items()), required=False)
    hidden = forms.BooleanField(label=gettext_lazy("Temporarily hidden"), required=False)
    title = forms.CharField(label=gettext_lazy("Title"), max_length=120, required=False)
    subtitle = forms.CharField(label=gettext_lazy("Subtitle"), max_length=300, required=False)
    body = forms.CharField(label=gettext_lazy("Body (Markdown)"), max_length=5000, required=False, widget=forms.Textarea(attrs={"rows": 3}))
    items = forms.CharField(
        label=gettext_lazy("Feature items"), max_length=6000, required=False, widget=forms.Textarea(attrs={"rows": 4, "dir": "auto"}),
        help_text=lazy(lambda: gettext("One item per line: icon | title | short text. The icon may be left empty. Icons: %(icons)s.") % {"icons": ", ".join(FEATURE_ICONS)}, str)())
    button_label = forms.CharField(label=gettext_lazy("Button text"), max_length=40, required=False)
    button_url = forms.CharField(label=gettext_lazy("Button URL"), max_length=500, required=False, widget=forms.TextInput(attrs={"dir": "ltr"}))
    button2_label = forms.CharField(label=gettext_lazy("Second button text"), max_length=40, required=False)
    button2_url = forms.CharField(label=gettext_lazy("Second button URL"), max_length=500, required=False, widget=forms.TextInput(attrs={"dir": "ltr"}))
    button3_label = forms.CharField(label=gettext_lazy("Third button text"), max_length=40, required=False)
    button3_url = forms.CharField(label=gettext_lazy("Third button URL"), max_length=500, required=False, widget=forms.TextInput(attrs={"dir": "ltr"}))
    category = forms.ModelChoiceField(queryset=Category.objects.order_by("name"), required=False, label=gettext_lazy("Content category"), empty_label="—")
    page = PageChoice(queryset=Page.objects.order_by("title"), required=False, label=gettext_lazy("Page"), empty_label="—")
    count = forms.IntegerField(label=gettext_lazy("Count"), min_value=1, max_value=24, required=False, widget=forms.NumberInput(attrs={"dir": "ltr"}))

    # position of this section in the saved document (hidden): lets the section keep its translations when rows are reordered
    origin = forms.IntegerField(required=False, min_value=0, widget=forms.HiddenInput)

    BUTTON_FIELDS = (("button_label", "button_url"), ("button2_label", "button2_url"), ("button3_label", "button3_url"))

    def clean_button_url(self):
        return normalize_link(self.cleaned_data.get("button_url", ""))

    def clean_button2_url(self):
        return normalize_link(self.cleaned_data.get("button2_url", ""))

    def clean_button3_url(self):
        return normalize_link(self.cleaned_data.get("button3_url", ""))

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
        if kind == "features":
            items, error = parse_items(cleaned.get("items", ""))
            if error:
                self.add_error("items", error)
            elif not items:
                self.add_error("items", gettext("Enter at least one item."))
        for label_name, url_name in self.BUTTON_FIELDS:
            label, url = (cleaned.get(label_name) or "").strip(), cleaned.get(url_name, "")
            if bool(label) != bool(url) and url_name not in self.errors:
                self.add_error(label_name if not label else url_name, gettext("Fill in both the button text and URL, or leave both empty."))
        return cleaned

    def to_section(self) -> dict:
        d = self.cleaned_data
        buttons = [
            {"label": d[l].strip(), "url": d[u]} for l, u in self.BUTTON_FIELDS[:MAX_SECTION_BUTTONS] if d.get(l, "").strip() and d.get(u)
        ]
        return {
            "type": d["type"], "enabled": not d["hidden"], "title": d["title"].strip(), "subtitle": d["subtitle"].strip(),
            "body": d["body"].strip(), "button_label": buttons[0]["label"] if buttons else "", "button_url": buttons[0]["url"] if buttons else "",
            "buttons": buttons, "items": parse_items(d.get("items", ""))[0] if d["type"] == "features" else [],
            "category": d["category"].pk if d.get("category") else None, "page": d["page"].pk if d.get("page") else None,
            "count": d.get("count") or 6,
        }


def section_initial(sections: list[dict]) -> list[dict]:
    out = []
    for index, s in enumerate(sections):
        row = {**s, "hidden": not s["enabled"], "items": items_to_text(s.get("items")), "origin": index}
        for number, (label_name, url_name) in enumerate(SectionForm.BUTTON_FIELDS):
            button = s["buttons"][number] if number < len(s.get("buttons", [])) else {}
            row[label_name], row[url_name] = button.get("label", ""), button.get("url", "")
        out.append(row)
    return out


SectionFormSet = forms.formset_factory(
    SectionForm, extra=1, can_order=True, can_delete=True, max_num=MAX_SECTIONS, validate_max=True,
)


def sections_from_formset(formset, previous: list[dict] | None = None) -> list[dict]:
    """Sections from the posted forms. ``translations`` are not part of this form (they have their own language tabs), so the
    translations of a section are carried over from ``previous`` through the hidden ``origin`` field (so they follow the section
    when rows are reordered; a new row has none)."""
    result = []
    for form in formset.ordered_forms:
        section = form.to_section()
        origin = form.cleaned_data.get("origin")
        section["translations"] = previous[origin].get("translations", {}) if previous and origin is not None and origin < len(previous) else {}
        result.append(section)
    return result


class SectionTextForm(StyledFormMixin, forms.Form):
    """The texts of one home section in one (non-default) language. Empty field = use the text of the site's default language."""
    title = forms.CharField(label=gettext_lazy("Title"), max_length=120, required=False, widget=forms.TextInput(attrs={"dir": "auto"}))
    subtitle = forms.CharField(label=gettext_lazy("Subtitle"), max_length=300, required=False, widget=forms.TextInput(attrs={"dir": "auto"}))
    body = forms.CharField(label=gettext_lazy("Body (Markdown)"), max_length=5000, required=False, widget=forms.Textarea(attrs={"rows": 3, "dir": "auto"}))
    button_label = forms.CharField(label=gettext_lazy("Button text"), max_length=40, required=False, widget=forms.TextInput(attrs={"dir": "auto"}))
    button2_label = forms.CharField(label=gettext_lazy("Second button text"), max_length=40, required=False, widget=forms.TextInput(attrs={"dir": "auto"}))
    button3_label = forms.CharField(label=gettext_lazy("Third button text"), max_length=40, required=False, widget=forms.TextInput(attrs={"dir": "auto"}))
    items = forms.CharField(
        label=gettext_lazy("Feature items"), max_length=6000, required=False, widget=forms.Textarea(attrs={"rows": 4, "dir": "auto"}),
        help_text=gettext_lazy("One item per line, in the same order as the default language: title | short text."))

    def clean_items(self):
        lines = []
        for number, line in enumerate((self.cleaned_data.get("items") or "").splitlines(), 1):
            parts = [p.strip() for p in line.split("|", 1)]
            if len(parts[0]) > 60 or (len(parts) == 2 and len(parts[1]) > 300):
                raise ValidationError(gettext("Line %(line)s is too long: the title may have up to 60 and the text up to 300 characters.") % {"line": number})
            lines.append({"title": parts[0], "text": parts[1] if len(parts) == 2 else ""})
        if len(lines) > MAX_FEATURE_ITEMS:
            raise ValidationError(gettext("At most %(count)s items are allowed.") % {"count": MAX_FEATURE_ITEMS})
        return lines

    def to_translation(self) -> dict:
        d = self.cleaned_data  # an untouched extra form has no cleaned values: nothing to store
        return {
            "title": d.get("title", "").strip(), "subtitle": d.get("subtitle", "").strip(), "body": d.get("body", "").strip(),
            "button_labels": [d.get(k, "").strip() for k in ("button_label", "button2_label", "button3_label")],
            "items": d.get("items", []),
        }


def text_initial(sections: list[dict], language: str) -> list[dict]:
    rows = []
    for s in sections:
        tr = s.get("translations", {}).get(language, {})
        labels = tr.get("button_labels", [])
        rows.append({
            "title": tr.get("title", ""), "subtitle": tr.get("subtitle", ""), "body": tr.get("body", ""),
            "button_label": labels[0] if len(labels) > 0 else "", "button2_label": labels[1] if len(labels) > 1 else "",
            "button3_label": labels[2] if len(labels) > 2 else "",
            "items": "\n".join(f"{i['title']} | {i['text']}" for i in tr.get("items", [])),
        })
    return rows


SectionTextFormSet = forms.formset_factory(SectionTextForm, extra=0, max_num=MAX_SECTIONS, validate_max=True)


class ThemeUploadForm(forms.Form):
    """فرم بارگذاری ZIP؛ بررسی واقعی (امضای ZIP، سقف‌ها، ساختار) در apps.themes.installer انجام می‌شود، نه اینجا."""

    theme_zip = forms.FileField(label=gettext_lazy("Theme ZIP file"), allow_empty_file=False, error_messages={"required": gettext_lazy("Choose a ZIP file.")})

