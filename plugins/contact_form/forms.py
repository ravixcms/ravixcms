from django import forms


class ContactForm(forms.Form):
    name = forms.CharField(label="نام", min_length=2, max_length=80, widget=forms.TextInput(attrs={"autocomplete": "name"}))
    email = forms.EmailField(label="ایمیل", max_length=254, widget=forms.EmailInput(attrs={"autocomplete": "email", "dir": "ltr"}))
    subject = forms.CharField(label="موضوع", max_length=120, required=False)
    message = forms.CharField(label="پیام", min_length=5, max_length=2000, widget=forms.Textarea(attrs={"rows": 6}))
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}))  # تله برای ربات‌ها

    def clean_name(self):
        return " ".join(self.cleaned_data["name"].split())

    def clean_subject(self):
        return " ".join(self.cleaned_data["subject"].split())

    def clean_message(self):
        return self.cleaned_data["message"].replace("\r\n", "\n").strip()
