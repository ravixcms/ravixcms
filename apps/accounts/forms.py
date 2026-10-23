from django.utils.translation import gettext, gettext_lazy
import logging

from django import forms
from django.contrib.auth import get_user_model
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, PasswordResetForm, SetPasswordForm, UserCreationForm

from apps.core.forms import StyledFormMixin

from . import emailing, throttle
from .permissions import Role

User = get_user_model()


class LoginForm(StyledFormMixin, AuthenticationForm):
    username = forms.CharField(
        label=gettext_lazy("Username or email"),
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username", "dir": "ltr"}),
    )
    password = forms.CharField(
        label=gettext_lazy("Password"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password", "dir": "ltr"}),
    )
    remember_me = forms.BooleanField(label=gettext_lazy("Remember me"), required=False)

    error_messages = {
        "invalid_login": gettext_lazy("The username/email or password is incorrect."),
        "inactive": gettext_lazy("This account is inactive."),
        "unverified": gettext_lazy("This account’s email has not been verified yet. Open the verification link in your inbox or ask for it to be sent again."),
        "locked": gettext_lazy("Too many failed attempts. Please try again in a few minutes."),
    }

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if settings.RAVIX_REQUIRE_EMAIL_VERIFICATION and not user.email_verified_at:
            raise forms.ValidationError(self.error_messages["unverified"], code="unverified")

    def clean(self):
        identifier = self.cleaned_data.get("username")
        if identifier and throttle.is_locked(self.request, identifier):
            raise forms.ValidationError(self.error_messages["locked"], code="locked")
        try:
            result = super().clean()
        except forms.ValidationError:
            if identifier:
                throttle.register_failure(self.request, identifier)
            raise
        if identifier:
            throttle.reset(self.request, identifier)
        return result


class RegisterForm(StyledFormMixin, UserCreationForm):
    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email")
        widgets = {
            "username": forms.TextInput(attrs={"dir": "ltr", "autocomplete": "username"}),
            "email": forms.EmailInput(attrs={"dir": "ltr", "autocomplete": "email"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True
        self.fields["first_name"].required = True
        self.fields["password1"].widget.attrs.update({"dir": "ltr", "autocomplete": "new-password"})
        self.fields["password2"].widget.attrs.update({"dir": "ltr", "autocomplete": "new-password"})
        self.fields["username"].help_text = gettext("Letters, digits and the symbols @ . + - _ are allowed.")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(gettext("A user with this email has already registered."))
        return email

    def clean_username(self):
        username = self.cleaned_data["username"]
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError(gettext("This username is already taken."))
        return username

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = Role.USER  # ثبت‌نام عمومی همیشه کاربر عادی می‌سازد
        if commit:
            user.save()
        return user


class ProfileForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "phone", "bio", "avatar")
        widgets = {
            "email": forms.EmailInput(attrs={"dir": "ltr"}),
            "phone": forms.TextInput(attrs={"dir": "ltr"}),
            "bio": forms.Textarea(attrs={"rows": 4}),
        }

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(gettext("This email belongs to another user."))
        return email


class StyledPasswordChangeForm(StyledFormMixin, PasswordChangeForm):
    error_messages = {
        **PasswordChangeForm.error_messages,
        "password_incorrect": gettext_lazy("The current password is incorrect."),
        "password_mismatch": gettext_lazy("The two new passwords do not match."),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in self.fields:
            self.fields[name].widget.attrs["dir"] = "ltr"
        self.fields["old_password"].widget.attrs["autocomplete"] = "current-password"
        self.fields["new_password1"].widget.attrs["autocomplete"] = "new-password"
        self.fields["new_password2"].widget.attrs["autocomplete"] = "new-password"


class UserManageForm(StyledFormMixin, forms.ModelForm):
    """ویرایش نقش و وضعیت فعال بودن کاربر توسط مدیران."""

    class Meta:
        model = User
        fields = ("role", "is_active")
        labels = {"is_active": gettext_lazy("Account is active")}

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        allowed = set(actor.assignable_roles())
        self.fields["role"].choices = [(v, l) for v, l in Role.choices if v in allowed]

    def clean_role(self):
        role = self.cleaned_data["role"]
        if role not in self.actor.assignable_roles():
            raise forms.ValidationError(gettext("You do not have permission to grant this role."))
        return role

    def clean(self):
        cleaned = super().clean()
        # حذف آخرین مدیرکل فعال ممکن نیست
        if self.instance.pk and self.instance.role == Role.SUPER_ADMIN:
            demoting = cleaned.get("role") and cleaned["role"] != Role.SUPER_ADMIN
            deactivating = cleaned.get("is_active") is False
            if demoting or deactivating:
                others = User.objects.filter(role=Role.SUPER_ADMIN, is_active=True).exclude(pk=self.instance.pk)
                if not others.exists():
                    raise forms.ValidationError(gettext("At least one active superadmin must exist in the system."))
        return cleaned


class PasswordResetRequestForm(StyledFormMixin, PasswordResetForm):
    """درخواست بازیابی رمز. پاسخ برای ایمیل موجود و ناموجود دقیقاً یکی است (کاربران قابل‌شناسایی نیستند)؛
    خطای ارسال و محدودیت نرخ هم فقط لاگ می‌شوند، نه نمایش."""

    email = forms.EmailField(label=gettext_lazy("Email"), max_length=254, widget=forms.EmailInput(attrs={"autocomplete": "email", "dir": "ltr", "autofocus": True}))

    def save(self, *args, request=None, **kwargs):
        email = self.cleaned_data["email"]
        if request is not None and not throttle.allow_send(request, email, "reset"):
            return
        kwargs.update(emailing.site_domain_args())
        try:
            super().save(*args, request=request, **kwargs)
        except Exception:  # noqa: BLE001 - SMTP خراب نباید فاش کند ایمیل ثبت شده است یا نه
            logging.getLogger(__name__).exception(gettext("Sending the password reset email failed."))


class StyledSetPasswordForm(StyledFormMixin, SetPasswordForm):
    error_messages = {"password_mismatch": gettext_lazy("The passwords entered do not match.")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_password1"].label = gettext("New password")
        self.fields["new_password2"].label = gettext("Repeat new password")
        for name in self.fields:
            self.fields[name].widget.attrs.update({"dir": "ltr", "autocomplete": "new-password"})


class ResendVerificationForm(StyledFormMixin, forms.Form):
    email = forms.EmailField(label=gettext_lazy("Account email"), max_length=254, widget=forms.EmailInput(attrs={"autocomplete": "email", "dir": "ltr", "autofocus": True}))
