from django.utils.translation import gettext, gettext_lazy
from django import forms
from django.contrib.auth import get_user_model

from .models import Category, HubPlugin


class UploadForm(forms.Form):
    package = forms.FileField(label=gettext_lazy("Plugin package (zip)"))
    changelog = forms.CharField(label=gettext_lazy("Changes in this version"), required=False, max_length=5000, widget=forms.Textarea(attrs={"rows": 4}))
    publish = forms.BooleanField(label=gettext_lazy("Publish immediately"), required=False)

    def clean_package(self):
        f = self.cleaned_data["package"]
        if not f.name.lower().endswith(".zip"):
            raise forms.ValidationError(gettext("Only zip files are accepted."))
        return f


class PluginMetaForm(forms.ModelForm):
    class Meta:
        model = HubPlugin
        fields = ["summary", "description", "category", "homepage"]
        widgets = {"description": forms.Textarea(attrs={"rows": 6})}


class TokenForm(forms.Form):
    name = forms.CharField(label=gettext_lazy("Token name (e.g. CI)"), max_length=60)


class PublisherForm(forms.Form):
    username = forms.CharField(label=gettext_lazy("Username"), max_length=150)
    display_name = forms.CharField(label=gettext_lazy("Publisher name"), max_length=80)
    website = forms.URLField(label=gettext_lazy("Website"), required=False)

    def clean_username(self):
        u = get_user_model().objects.filter(username=self.cleaned_data["username"].strip(), is_active=True).first()
        if u is None:
            raise forms.ValidationError(gettext("No active user with this name was found."))
        self.user = u
        return u.username


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["slug", "name", "order"]
