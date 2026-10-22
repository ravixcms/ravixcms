from django import forms

from apps.builder.schema import is_safe_url

from .models import SeoMeta


class SeoMetaForm(forms.ModelForm):
    class Meta:
        model = SeoMeta
        fields = ["title", "description", "noindex", "canonical", "og_image"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "input", "maxlength": 120}),
            "description": forms.Textarea(attrs={"class": "input", "rows": 3, "maxlength": 320}),
            "noindex": forms.CheckboxInput(attrs={"class": "check"}),
            "canonical": forms.TextInput(attrs={"class": "input ltr", "placeholder": "https://… یا /مسیر/"}),
            "og_image": forms.TextInput(attrs={"class": "input ltr", "placeholder": "https://… یا /media/…"}),
        }

    def _url(self, name):
        value = " ".join(self.cleaned_data.get(name, "").split())
        if value and not is_safe_url(value):
            raise forms.ValidationError("نشانی باید مسیر داخلی یا http/https باشد.")
        return value

    def clean_canonical(self):
        return self._url("canonical")

    def clean_og_image(self):
        return self._url("og_image")

    def clean_title(self):
        return " ".join(self.cleaned_data["title"].split())

    def clean_description(self):
        return " ".join(self.cleaned_data["description"].split())
