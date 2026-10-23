from django.utils.translation import gettext, gettext_lazy
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
            "canonical": forms.TextInput(attrs={"class": "input ltr", "placeholder": gettext_lazy("https://… or /path/")}),
            "og_image": forms.TextInput(attrs={"class": "input ltr", "placeholder": gettext_lazy("https://… or /media/…")}),
        }

    def _url(self, name):
        value = " ".join(self.cleaned_data.get(name, "").split())
        if value and not is_safe_url(value):
            raise forms.ValidationError(gettext("The address must be an internal path or http/https."))
        return value

    def clean_canonical(self):
        return self._url("canonical")

    def clean_og_image(self):
        return self._url("og_image")

    def clean_title(self):
        return " ".join(self.cleaned_data["title"].split())

    def clean_description(self):
        return " ".join(self.cleaned_data["description"].split())
