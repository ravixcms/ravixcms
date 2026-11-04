from django.conf import settings
from django.utils.translation import gettext, gettext_lazy
from django import forms

from .models import MailSettings, SiteSettings
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

    identity_alternate_names = forms.CharField(
        label=gettext_lazy("Alternative names"), required=False, widget=forms.Textarea(attrs={"rows": 3, "dir": "auto"}),
        help_text=gettext_lazy("Other names people use for it, for example the name in another script. One per line."))
    identity_same_as = forms.CharField(
        label=gettext_lazy("Social profiles and other official pages"), required=False, widget=forms.Textarea(attrs={"rows": 4, "dir": "ltr"}),
        help_text=gettext_lazy("Addresses of the same organization or person elsewhere (sameAs). One https address per line."))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.media_library.models import Media

        self.initial["identity_alternate_names"] = "\n".join(self.instance.identity_alternate_names or [])
        self.initial["identity_same_as"] = "\n".join(self.instance.identity_same_as or [])
        ids = list(Media.objects.filter(kind=Media.Kind.IMAGE).order_by("-created_at", "-id").values_list("pk", flat=True)[:300])
        if self.instance.identity_logo_id:
            ids.append(self.instance.identity_logo_id)  # the saved choice stays selectable even when it is older than the list
        self.fields["identity_logo"].queryset = Media.objects.filter(pk__in=ids).order_by("-created_at", "-id")
        self.fields["identity_logo"].required = False
        self.fields["identity_type"].required = False  # a form that does not send it (older scripts, tests) keeps the saved type
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
            "comments_require_approval",
            "comments_notify_admin",
            "comments_blocked_words",
            "content_per_language",
            "identity_type",
            "identity_name",
            "identity_logo",
            "identity_alternate_names",
            "identity_same_as",
            "identity_description",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "comments_blocked_words": forms.Textarea(attrs={"rows": 4}),
            "admin_email": forms.EmailInput(attrs={"dir": "ltr"}),
            "contact_phone": forms.TextInput(attrs={"dir": "ltr"}),
        }

    def clean_identity_type(self):
        return self.cleaned_data.get("identity_type") or self.instance.identity_type or "organization"

    def clean_identity_alternate_names(self):
        from .identity import clean_names

        return clean_names(self.cleaned_data.get("identity_alternate_names", ""))

    def clean_identity_same_as(self):
        from .identity import clean_profiles

        return clean_profiles(self.cleaned_data.get("identity_same_as", ""))

    def clean_site_name(self):
        name = self.cleaned_data["site_name"].strip()
        if not name:
            raise forms.ValidationError(gettext("The site name cannot be empty."))
        return name


class SpeedSettingsForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = ("page_cache", "keep_alive")
        widgets = {"page_cache": forms.RadioSelect}


class EmailSettingsForm(StyledFormMixin, forms.ModelForm):
    password = forms.CharField(label=gettext_lazy("Password"), required=False, strip=False, widget=forms.PasswordInput(render_value=False, attrs={"dir": "ltr", "autocomplete": "new-password"}),
                               help_text=gettext_lazy("Leave empty to keep the saved password."))
    clear_password = forms.BooleanField(label=gettext_lazy("Remove the saved password"), required=False)

    class Meta:
        model = MailSettings
        fields = ("enabled", "host", "port", "security", "username", "from_email")
        widgets = {"host": forms.TextInput(attrs={"dir": "ltr"}), "username": forms.TextInput(attrs={"dir": "ltr", "autocomplete": "off"}),
                   "from_email": forms.TextInput(attrs={"dir": "ltr"})}

    def clean(self):
        data = super().clean()
        if data.get("enabled") and not (data.get("host") or "").strip():
            self.add_error("host", gettext("Enter the SMTP server name."))
        return data

    def save(self, commit=True):
        from . import secretbox

        obj = super().save(commit=False)
        if self.cleaned_data.get("clear_password"):
            obj.password_encrypted = ""
        elif self.cleaned_data.get("password"):
            obj.password_encrypted = secretbox.encrypt(self.cleaned_data["password"])
        if commit:
            obj.save()
        return obj
