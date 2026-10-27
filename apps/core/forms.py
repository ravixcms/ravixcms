from django.conf import settings
from django.utils.translation import gettext, gettext_lazy
from django import forms

from .models import SiteSettings
from .regional import language_choices, timezone_choices


class StyledFormMixin:
    """کلاس‌های CSS رابط Ravix را به ویجت‌ها اضافه می‌کند."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                css = "check"
            elif isinstance(widget, (forms.CheckboxSelectMultiple, forms.RadioSelect)):
                css = "check-list"
            elif isinstance(widget, forms.FileInput):
                css = "file"
            else:
                css = "input"
            widget.attrs["class"] = f"{widget.attrs.get('class', '')} {css}".strip()


class SiteSettingsForm(StyledFormMixin, forms.ModelForm):
    # اختیاری: فرم‌ها/کلاینت‌های قدیمی که این دو را نمی‌فرستند، مقدار فعلی را نگه می‌دارند (نه خطا).
    language = forms.ChoiceField(label=gettext_lazy("Default language"), choices=language_choices, required=False,
                                 help_text=gettext_lazy("Used for visitors and users who have not chosen a language themselves."))
    timezone = forms.ChoiceField(label=gettext_lazy("Time zone"), choices=timezone_choices, required=False,
                                 help_text=gettext_lazy("Dates and times in the panel and on the site are shown in this time zone."))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Per-language texts (name, tagline, description, footer): one optional field per language and text. An empty field
        # means "use the default-language text".
        saved = self.instance.translations if isinstance(self.instance.translations, dict) else {}
        self.translation_groups = []
        for code, native in settings.LANGUAGES:
            bound = []
            for name in SiteSettings.TRANSLATABLE:
                model_field = SiteSettings._meta.get_field(name)
                widget = forms.Textarea(attrs={"rows": 3, "class": "input"}) if name == "description" else forms.TextInput(attrs={"class": "input"})
                key = self.translation_field_name(code, name)
                self.fields[key] = forms.CharField(label=model_field.verbose_name, max_length=model_field.max_length or 1000,
                                                   required=False, widget=widget,
                                                   initial=(saved.get(code) or {}).get(name, ""))
                bound.append(key)
            self.translation_groups.append((code, native, bound))

    @staticmethod
    def translation_field_name(code, name):
        return "tr_%s__%s" % (code.replace("-", "_"), name)

    def translation_sections(self):
        """[(code, native name, [bound fields])] for the template."""
        return [(code, native, [self[k] for k in keys]) for code, native, keys in self.translation_groups]

    def save(self, commit=True):
        instance = super().save(commit=False)
        default = instance.language
        translations = {}
        for code, _native, keys in self.translation_groups:
            if code == default:
                continue  # the plain fields are the default language's text
            values = {name: (self.cleaned_data.get(self.translation_field_name(code, name)) or "").strip()
                      for name in SiteSettings.TRANSLATABLE}
            values = {k: v for k, v in values.items() if v}
            if values:
                translations[code] = values
        instance.translations = translations
        if commit:
            instance.save()
            self.save_m2m()
        return instance

    def clean_language(self):
        return self.cleaned_data.get("language") or self.instance.language

    def clean_timezone(self):
        return self.cleaned_data.get("timezone") or self.instance.timezone

    class Meta:
        model = SiteSettings
        fields = (
            "site_name",
            "tagline",
            "description",
            "logo",
            "favicon",
            "default_share_image",
            "language",
            "timezone",
            "admin_email",
            "contact_phone",
            "address",
            "footer_text",
            "registration_open",
            "comments_require_approval",
            "comments_notify_admin",
            "comments_blocked_words",
            "content_per_language",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "comments_blocked_words": forms.Textarea(attrs={"rows": 4}),
            "admin_email": forms.EmailInput(attrs={"dir": "ltr"}),
            "contact_phone": forms.TextInput(attrs={"dir": "ltr"}),
        }

    def clean_site_name(self):
        name = self.cleaned_data["site_name"].strip()
        if not name:
            raise forms.ValidationError(gettext("The site name cannot be empty."))
        return name
