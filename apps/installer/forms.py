from django.utils.translation import gettext, gettext_lazy
from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.core.forms import StyledFormMixin
from apps.core.regional import language_choices, timezone_choices

from . import services, token


class InstallForm(StyledFormMixin, forms.Form):
    install_token = forms.CharField(label=gettext_lazy("Install token"), required=False, strip=True,
                                    widget=forms.TextInput(attrs={"dir": "ltr", "autocomplete": "off"}),
                                    help_text=gettext_lazy("It is printed in the service log; or run: python manage.py ravix_install_token"))
    site_name = forms.CharField(label=gettext_lazy("Site name"), max_length=120)
    language = forms.ChoiceField(label=gettext_lazy("Language"), choices=language_choices)
    timezone = forms.ChoiceField(label=gettext_lazy("Time zone"), choices=timezone_choices)
    username = forms.CharField(label=gettext_lazy("Superadmin username"), max_length=150,
                               widget=forms.TextInput(attrs={"dir": "ltr", "autocomplete": "username"}),
                               help_text=gettext_lazy("English letters, digits, and the symbols @ . + - _"))
    email = forms.EmailField(label=gettext_lazy("Superadmin email"), widget=forms.EmailInput(attrs={"dir": "ltr", "autocomplete": "email"}))
    password1 = forms.CharField(label=gettext_lazy("Password"), strip=False,
                                widget=forms.PasswordInput(attrs={"dir": "ltr", "autocomplete": "new-password"}))
    password2 = forms.CharField(label=gettext_lazy("Repeat password"), strip=False,
                                widget=forms.PasswordInput(attrs={"dir": "ltr", "autocomplete": "new-password"}))

    def __init__(self, *args, db_ready=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.db_ready = db_ready
        if not token.required():
            del self.fields["install_token"]
        # Start from the language the installer page is shown in (chosen with the language selector), and suggest that
        # language's name for the site.
        from django.utils.translation import get_language

        from apps.core.regional import normalize_language

        language = normalize_language(get_language()) or "fa"
        self.fields["language"].initial = language
        self.fields["timezone"].initial = "Asia/Tehran" if language == "fa" else "UTC"
        self.fields["site_name"].initial = services.default_site_name(language)

    def clean_install_token(self):
        value = self.cleaned_data.get("install_token", "")
        if not token.check_token(value):
            raise ValidationError(gettext("The install token is incorrect."))
        return value

    def clean_username(self):
        value = self.cleaned_data["username"]
        try:
            User._meta.get_field("username").run_validators(value)
        except ValidationError as exc:
            raise ValidationError(exc.messages) from exc
        if self.db_ready and User.objects.filter(username__iexact=value).exists():
            raise ValidationError(gettext("This username is already taken."))
        return value

    def clean_email(self):
        value = self.cleaned_data["email"].strip().lower()
        if self.db_ready and User.objects.filter(email__iexact=value).exists():
            raise ValidationError(gettext("A user with this email already exists."))
        return value

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", gettext("The two passwords do not match."))
        elif p1:
            try:
                validate_password(p1, user=User(username=data.get("username", ""), email=data.get("email", "")))
            except ValidationError as exc:
                self.add_error("password1", exc)
        return data
