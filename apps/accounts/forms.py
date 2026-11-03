from django.utils import timezone
from django.utils.translation import gettext, gettext_lazy
import logging

from django import forms
from django.contrib.auth import get_user_model
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, PasswordResetForm, SetPasswordForm, UserCreationForm

from apps.core.forms import StyledFormMixin

from . import captcha, emailing, policy as account_policy, throttle
from .models import AccountPolicy, Invitation
from .permissions import Role

User = get_user_model()


class CaptchaMixin:
    """Asks for the proof-of-work (see ``captcha.py``) when ``captcha_needed()`` says so. The two hidden inputs are read from the submitted
    data (they are not form fields); the page shows a new challenge every time it is drawn. Needs ``self.request``."""

    captcha_purpose = "form"
    captcha_always = False
    captcha_messages = {
        "missing": gettext_lazy("The automatic human check did not finish. Wait a moment for it to complete (JavaScript is required) and try again."),
        "expired": gettext_lazy("The human check expired. Please try again."),
        "invalid": gettext_lazy("The human check was not accepted. Please try again."),
        "weak": gettext_lazy("The human check was not accepted. Please try again."),
        "replay": gettext_lazy("The human check was not accepted. Please try again."),
    }

    def captcha_needed(self) -> bool:
        return captcha.enabled() and self.captcha_always

    def captcha_bits(self) -> int:
        return settings.RAVIX_CAPTCHA_BITS

    @property
    def captcha_widget(self):
        """The challenge to draw on the page, or ``None`` when no check is asked for. One per form instance."""
        if not hasattr(self, "_captcha_widget"):
            self._captcha_widget = captcha.new_challenge(self.captcha_purpose, self.captcha_bits()) if self.captcha_needed() else None
        return self._captcha_widget

    def check_captcha(self) -> None:
        """Raises ValidationError (code ``captcha``) unless no check is needed or a fresh correct answer came with the form."""
        if not self.captcha_needed():
            return
        result = captcha.verify(self.captcha_purpose, self.data.get("captcha_challenge", ""), self.data.get("captcha_solution", ""), min_bits=self.captcha_bits())
        if result != "ok":
            raise forms.ValidationError(self.captcha_messages[result], code="captcha")


class LoginForm(CaptchaMixin, StyledFormMixin, AuthenticationForm):
    captcha_purpose = "login"
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
        if AccountPolicy.load().verification_required and not user.email_verified_at:
            raise forms.ValidationError(self.error_messages["unverified"], code="unverified")

    def _identifier(self) -> str:
        return (self.data.get("username") or "").strip()

    def captcha_needed(self) -> bool:
        """Only after a few failures: for this IP + account, for this IP, or against this account from anywhere."""
        if not captcha.enabled():
            return False
        pair, ip, account = throttle.failure_counts(self.request, self._identifier())
        return pair >= settings.RAVIX_CAPTCHA_LOGIN_AFTER or ip >= throttle.IP_CAPTCHA_AFTER or account >= throttle.ACCOUNT_CAPTCHA_AFTER

    def captcha_bits(self) -> int:
        """More failures, harder puzzle (one bit more per five failures, up to four)."""
        pair, ip, account = throttle.failure_counts(self.request, self._identifier())
        return settings.RAVIX_CAPTCHA_BITS + min(4, max(pair, account // 2, ip // 2) // 5)

    def clean(self):
        identifier = self.cleaned_data.get("username")
        if identifier and throttle.is_locked(self.request, identifier):
            raise forms.ValidationError(self.error_messages["locked"], code="locked")
        if self.is_bound and self.captcha_needed():
            self.check_captcha()  # a visitor who skipped the check never gets a password tried (so cannot guess)
        try:
            result = super().clean()
        except forms.ValidationError:
            if identifier:
                throttle.register_failure(self.request, identifier)
            raise
        if identifier:
            throttle.reset(self.request, identifier)
        return result


class RegisterForm(CaptchaMixin, StyledFormMixin, UserCreationForm):
    """Public sign-up. It never tells whether an email address is already registered: uniqueness is NOT validated here
    (the view handles an existing address silently), and there is no username field (a free one is generated), so no error can reveal an account."""

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")
        widgets = {
            "email": forms.EmailInput(attrs={"dir": "ltr", "autocomplete": "email"}),
        }

    captcha_purpose = "register"
    captcha_always = True

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.fields["email"].required = True
        self.fields["first_name"].required = True
        self.fields["password1"].widget.attrs.update({"dir": "ltr", "autocomplete": "new-password"})
        self.fields["password2"].widget.attrs.update({"dir": "ltr", "autocomplete": "new-password"})
        self.policy = AccountPolicy.load()
        add_terms_field(self, self.policy)

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean(self):
        cleaned = super().clean()
        self.check_captcha()
        return cleaned

    def validate_unique(self):
        """The model's unique-email check would answer "already registered"; the view decides what to do with a known address instead."""

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = unique_username(self.cleaned_data["email"])
        user.role = self.policy.new_user_role  # the role the administrator chose for self sign-ups (a member by default)
        if commit:
            user.save()
        return user


def add_terms_field(form, policy) -> None:
    """When the site requires it, a checkbox "I accept the terms" (required) and the links to read them (``form.terms_url`` / ``form.privacy_url``)."""
    from apps.public.builders import page_url

    form.terms_url = form.privacy_url = ""
    if not policy.require_terms:
        return
    for attr, page in (("terms_url", policy.terms_page), ("privacy_url", policy.privacy_page)):
        if page is not None and page.is_live:
            setattr(form, attr, page_url(page))
    form.fields["accept_terms"] = forms.BooleanField(label=gettext_lazy("I have read and accept the terms of use"), required=True,
                                                     error_messages={"required": gettext_lazy("You must accept the terms to continue.")})


def unique_username(email: str) -> str:
    """A free username made from the part of the address before the @ (letters, digits and . _ - only), with a random suffix on a clash."""
    import re
    import secrets

    base = re.sub(r"[^A-Za-z0-9._-]", "", (email or "").split("@")[0])[:30] or "member"
    name = base
    while User.objects.filter(username__iexact=name).exists():
        name = f"{base}-{secrets.token_hex(3)}"
    return name


def profile_language_choices():
    return [("", gettext("Site default"))] + list(settings.LANGUAGES)


class ProfileForm(StyledFormMixin, forms.ModelForm):
    language = forms.ChoiceField(label=gettext_lazy("Language"), required=False, choices=profile_language_choices,
                                 help_text=gettext_lazy("Your preferred interface language. Leave empty to use the site default."))

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "phone", "language", "bio", "avatar")
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


class PasswordChosenMixin:
    """After a new password is saved: the "must change" flag is cleared and the time is kept."""

    def save(self, commit=True):
        user = super().save(commit=False)
        user.must_change_password = False
        user.password_changed_at = timezone.now()
        if commit:
            user.save()
        return user


class StyledPasswordChangeForm(PasswordChosenMixin, StyledFormMixin, PasswordChangeForm):
    error_messages = {
        **PasswordChangeForm.error_messages,
        "password_same": gettext_lazy("Choose a password that is different from the current one."),
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

    def clean_new_password1(self):
        new = self.cleaned_data.get("new_password1")
        old = self.data.get("old_password")
        if new and old and new == old:
            raise forms.ValidationError(self.error_messages["password_same"], code="password_same")
        return new


class UserManageForm(StyledFormMixin, forms.ModelForm):
    """ویرایش نقش و وضعیت فعال بودن کاربر توسط مدیران."""

    class Meta:
        model = User
        fields = ("role", "is_active", "must_change_password")
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


class PasswordResetRequestForm(CaptchaMixin, StyledFormMixin, PasswordResetForm):
    """درخواست بازیابی رمز. پاسخ برای ایمیل موجود و ناموجود دقیقاً یکی است (کاربران قابل‌شناسایی نیستند)؛
    خطای ارسال و محدودیت نرخ هم فقط لاگ می‌شوند، نه نمایش."""

    captcha_purpose = "reset"
    captcha_always = True
    email = forms.EmailField(label=gettext_lazy("Email"), max_length=254, widget=forms.EmailInput(attrs={"autocomplete": "email", "dir": "ltr", "autofocus": True}))

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request

    def clean(self):
        cleaned = super().clean()
        self.check_captcha()
        return cleaned

    def send_mail(self, subject_template_name, email_template_name, context, from_email, to_email, html_email_template_name=None):
        """The message is written in the recipient's language (profile, else the language the visitor is using now)."""
        from apps.core.recipient import current_language, language_for_user, use_language

        from django.template import loader

        from apps.core.mailhtml import build_message

        with use_language(language_for_user(context.get("user"), fallback=current_language())):
            subject = "".join(loader.render_to_string(subject_template_name, context).splitlines())
            body = loader.render_to_string(email_template_name, context)
            message = build_message(subject, body, [to_email], from_email)  # text + HTML alternative with the Ravix logo
        try:
            message.send()
        except Exception:  # noqa: BLE001 - the same behaviour and logger as Django's PasswordResetForm.send_mail
            from django.contrib.auth.forms import logger as auth_logger

            auth_logger.exception("Failed to send password reset email to %s", context["user"].pk)

    def save(self, *args, request=None, **kwargs):
        email = self.cleaned_data["email"]
        if request is not None and not throttle.allow_send(request, email, "reset"):
            return
        kwargs.update(emailing.site_domain_args())
        try:
            super().save(*args, request=request, **kwargs)
        except Exception:  # noqa: BLE001 - SMTP خراب نباید فاش کند ایمیل ثبت شده است یا نه
            logging.getLogger(__name__).exception(gettext("Sending the password reset email failed."))


class StyledSetPasswordForm(PasswordChosenMixin, StyledFormMixin, SetPasswordForm):
    error_messages = {"password_mismatch": gettext_lazy("The passwords entered do not match.")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_password1"].label = gettext("New password")
        self.fields["new_password2"].label = gettext("Repeat new password")
        for name in self.fields:
            self.fields[name].widget.attrs.update({"dir": "ltr", "autocomplete": "new-password"})


class ResendVerificationForm(CaptchaMixin, StyledFormMixin, forms.Form):
    captcha_purpose = "resend"
    captcha_always = True

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request

    def clean(self):
        cleaned = super().clean()
        self.check_captcha()
        return cleaned

    email = forms.EmailField(label=gettext_lazy("Account email"), max_length=254, widget=forms.EmailInput(attrs={"autocomplete": "email", "dir": "ltr", "autofocus": True}))


class AccountPolicyForm(StyledFormMixin, forms.ModelForm):
    registration_mode = forms.ChoiceField(
        label=gettext_lazy("Who can create an account"), widget=forms.RadioSelect,
        choices=[(account_policy.OPEN, gettext_lazy("Anyone (public sign-up)")),
                 (account_policy.INVITE, gettext_lazy("Only people an administrator invites")),
                 (account_policy.CLOSED, gettext_lazy("Nobody signs up; administrators create accounts"))])

    class Meta:
        model = AccountPolicy
        fields = ("default_role", "require_verified_email", "remember_days", "invitation_days", "require_terms", "terms_page", "privacy_page",
                  "password_min_length", "password_check_common", "password_check_personal")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.content.models import Page, PublishStatus

        self.fields["registration_mode"].initial = account_policy.registration_mode()
        pages = Page.objects.filter(status=PublishStatus.PUBLISHED).order_by("title")
        for name in ("terms_page", "privacy_page"):
            self.fields[name].queryset = pages
            self.fields[name].empty_label = gettext("— none —")
        self.order_fields(["registration_mode"] + list(self.Meta.fields))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("require_terms") and not cleaned.get("terms_page"):
            self.add_error("terms_page", gettext("Choose the published page that holds the terms."))
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=commit)
        if commit:
            account_policy.set_registration_mode(self.cleaned_data["registration_mode"])
        return obj


class InvitationForm(StyledFormMixin, forms.Form):
    email = forms.EmailField(label=gettext_lazy("Email"), max_length=254, widget=forms.EmailInput(attrs={"dir": "ltr", "autocomplete": "off"}))
    role = forms.ChoiceField(label=gettext_lazy("Role"), choices=[])

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        allowed = set(actor.assignable_roles())
        self.fields["role"].choices = [(v, l) for v, l in Role.choices if v in allowed]

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(gettext("A user with this email already exists."))  # shown to an administrator only
        return email

    def clean_role(self):
        role = self.cleaned_data["role"]
        if role not in self.actor.assignable_roles():
            raise forms.ValidationError(gettext("You do not have permission to grant this role."))
        return role


class InviteAcceptForm(StyledFormMixin, UserCreationForm):
    """The person who opens an invitation chooses their name and password; the email and role come from the invitation."""

    class Meta:
        model = User
        fields = ("first_name", "last_name")

    def __init__(self, *args, invitation, **kwargs):
        super().__init__(*args, **kwargs)
        self.invitation = invitation
        self.fields["first_name"].required = True
        self.fields["password1"].widget.attrs.update({"dir": "ltr", "autocomplete": "new-password"})
        self.fields["password2"].widget.attrs.update({"dir": "ltr", "autocomplete": "new-password"})
        self.policy = AccountPolicy.load()
        add_terms_field(self, self.policy)

    def validate_unique(self):
        pass

    def save(self, commit=True):
        from django.utils import timezone

        user = super().save(commit=False)
        user.email = self.invitation.email
        user.username = unique_username(self.invitation.email)
        user.role = self.invitation.role
        user.email_verified_at = timezone.now()  # they received the invitation at this address
        if commit:
            user.save()
        return user
