from django import forms
from django.contrib.auth import get_user_model

from .models import Category, HubPlugin


class UploadForm(forms.Form):
    package = forms.FileField(label="بستهٔ افزونه (zip)")
    changelog = forms.CharField(label="تغییرات این نسخه", required=False, max_length=5000, widget=forms.Textarea(attrs={"rows": 4}))
    publish = forms.BooleanField(label="بلافاصله منتشر شود", required=False)

    def clean_package(self):
        f = self.cleaned_data["package"]
        if not f.name.lower().endswith(".zip"):
            raise forms.ValidationError("فقط فایل zip پذیرفته می‌شود.")
        return f


class PluginMetaForm(forms.ModelForm):
    class Meta:
        model = HubPlugin
        fields = ["summary", "description", "category", "homepage"]
        widgets = {"description": forms.Textarea(attrs={"rows": 6})}


class TokenForm(forms.Form):
    name = forms.CharField(label="نام توکن (مثلاً CI)", max_length=60)


class PublisherForm(forms.Form):
    username = forms.CharField(label="نام کاربری", max_length=150)
    display_name = forms.CharField(label="نام ناشر", max_length=80)
    website = forms.URLField(label="وب‌سایت", required=False)

    def clean_username(self):
        u = get_user_model().objects.filter(username=self.cleaned_data["username"].strip(), is_active=True).first()
        if u is None:
            raise forms.ValidationError("کاربر فعال با این نام پیدا نشد.")
        self.user = u
        return u.username


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["slug", "name", "order"]
