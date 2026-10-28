import re
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


class HostUnlockForm(StyledFormMixin, forms.Form):
    code = forms.CharField(label=gettext_lazy("Install code"), max_length=64, strip=True,
                           widget=forms.TextInput(attrs={"dir": "ltr", "autocomplete": "off", "autofocus": True, "placeholder": "XXXX-XXXX-XXXX-XXXX", "spellcheck": "false"}),
                           help_text=gettext_lazy("The code is in the file install-key.txt in your private data folder (next to the application folder, outside public_html). Open it with cPanel ▸ File Manager."))


class HostDatabaseForm(StyledFormMixin, forms.Form):
    ENGINES = [("mysql", gettext_lazy("MySQL / MariaDB (recommended)")), ("sqlite", gettext_lazy("SQLite (small sites)")), ("postgres", "PostgreSQL")]
    engine = forms.ChoiceField(label=gettext_lazy("Database type"), choices=ENGINES, initial="mysql")
    host = forms.CharField(label=gettext_lazy("Database host"), initial="localhost", required=False, max_length=200, widget=forms.TextInput(attrs={"dir": "ltr"}))
    port = forms.IntegerField(label=gettext_lazy("Port"), initial=3306, required=False, min_value=1, max_value=65535, widget=forms.NumberInput(attrs={"dir": "ltr"}))
    name = forms.CharField(label=gettext_lazy("Database name"), required=False, max_length=128, widget=forms.TextInput(attrs={"dir": "ltr", "autocomplete": "off"}),
                           help_text=gettext_lazy("In cPanel the name starts with your account name, for example myaccount_ravix."))
    user = forms.CharField(label=gettext_lazy("Database user"), required=False, max_length=128, widget=forms.TextInput(attrs={"dir": "ltr", "autocomplete": "off"}))
    password = forms.CharField(label=gettext_lazy("Database password"), required=False, strip=False, widget=forms.PasswordInput(attrs={"dir": "ltr", "autocomplete": "off"}, render_value=False))

    def clean(self):
        data = super().clean()
        engine = data.get("engine")
        if engine and engine != "sqlite":
            for field in ("name", "user"):
                if not data.get(field):
                    self.add_error(field, gettext("This field is required."))
            data["host"] = (data.get("host") or "localhost").strip()
            data["port"] = data.get("port") or (3306 if engine == "mysql" else 5432)
            if not re.fullmatch(r"[A-Za-z0-9._-]+", data["host"]):
                self.add_error("host", gettext("Enter a host name such as localhost."))
        return data


class HostSiteForm(InstallForm):
    """The same questions as the VPS installer (site name, language, time zone, administrator); no install-token field and no database lookups."""

    def __init__(self, *args, **kwargs):
        kwargs["db_ready"] = False
        super().__init__(*args, **kwargs)
        self.fields.pop("install_token", None)
