from django import forms

from .models import SiteSettings


class StyledFormMixin:
    """کلاس‌های CSS رابط Ravin را به ویجت‌ها اضافه می‌کند."""

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
    class Meta:
        model = SiteSettings
        fields = (
            "site_name",
            "tagline",
            "description",
            "logo",
            "favicon",
            "default_share_image",
            "admin_email",
            "contact_phone",
            "address",
            "footer_text",
            "registration_open",
            "comments_require_approval",
            "comments_notify_admin",
            "comments_blocked_words",
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
            raise forms.ValidationError("نام سایت نمی‌تواند خالی باشد.")
        return name
