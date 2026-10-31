from django.utils.translation import gettext
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView as DjangoLoginView
from django.contrib.auth.views import LogoutView as DjangoLogoutView
from django.contrib.auth.views import PasswordChangeView as DjangoPasswordChangeView
from django.contrib.auth.views import PasswordResetCompleteView as DjangoPasswordResetCompleteView
from django.contrib.auth.views import PasswordResetConfirmView as DjangoPasswordResetConfirmView
from django.contrib.auth.views import PasswordResetDoneView as DjangoPasswordResetDoneView
from django.contrib.auth.views import PasswordResetView as DjangoPasswordResetView
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import FormView, ListView, TemplateView, UpdateView

from apps.audit.services import log_event
from apps.core.models import SiteSettings

from . import emailing, throttle
from .forms import (
    AccountPolicyForm,
    InvitationForm,
    InviteAcceptForm,
    LoginForm,
    PasswordResetRequestForm,
    ProfileForm,
    RegisterForm,
    ResendVerificationForm,
    StyledPasswordChangeForm,
    StyledSetPasswordForm,
    UserManageForm,
)
from . import policy as account_policy
from .mixins import CapabilityRequiredMixin
from .models import AccountPolicy, Invitation, SecurityProfile
from .permissions import Role

User = get_user_model()


class RedirectAuthenticatedMixin:
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("dashboard:home")
        return super().dispatch(request, *args, **kwargs)


class LoginView(RedirectAuthenticatedMixin, DjangoLoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Django puts a ``RequestSite`` under "site"; the templates (and the context processor) mean the SiteSettings: name, logo, favicon.
        context.pop("site", None)
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        if form.cleaned_data.get("remember_me"):
            self.request.session.set_expiry(AccountPolicy.load().remember_days * 86400)  # the administrator's choice (default 30 days)
        else:
            self.request.session.set_expiry(0)  # ends when the browser is closed
        return response


class LogoutView(DjangoLogoutView):
    """خروج فقط با POST (محافظت‌شده با CSRF)."""

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        messages.info(request, gettext("You have been signed out successfully."))
        return response


class RegisterView(RedirectAuthenticatedMixin, FormView):
    template_name = "accounts/register.html"
    form_class = RegisterForm
    success_url = reverse_lazy("dashboard:home")

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated and not SiteSettings.load().registration_open:
            return self.registration_closed(request)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "request": self.request}

    def post(self, request, *args, **kwargs):
        if not throttle.allow_action(request, "register", settings.RAVIX_REGISTER_LIMIT_PER_IP):
            messages.error(request, gettext("Too many sign-up attempts from your network. Please try again later."))
            return self.get(request, *args, **kwargs)
        return super().post(request, *args, **kwargs)

    def registration_closed(self, request):
        messages.warning(request, gettext("Public registration is currently disabled."))
        return redirect("accounts:login")

    def form_valid(self, form):
        """The answer is the same page whether the address was new or already registered; nobody is signed in by registering."""
        email = form.cleaned_data["email"]
        existing = User.objects.filter(email__iexact=email).first()
        if existing is None:
            try:
                with transaction.atomic():
                    user = form.save()
            except IntegrityError:  # two sign-ups with the same address at once: the other one won
                existing = User.objects.filter(email__iexact=email).first()
            else:
                if form.cleaned_data.get("accept_terms"):
                    SecurityProfile.objects.update_or_create(user=user, defaults={"terms_accepted_at": timezone.now()})
                log_event(self.request, "create", user, (gettext("New user “%(username)s” registered.") % {"username": user.username}), actor=user)
                emailing.send_verification_email(self.request, user)
        if existing is not None:
            User().set_password(form.cleaned_data["password1"])  # the same hashing work as a real sign-up (no timing difference)
            emailing.send_registration_notice(self.request, existing)
        return redirect("accounts:register_done")

    def form_invalid(self, form):
        messages.error(self.request, gettext("Registration did not complete. Please check the form errors."))
        return super().form_invalid(form)


class RegisterDoneView(TemplateView):
    template_name = "accounts/register_done.html"


class ProfileView(LoginRequiredMixin, UpdateView):
    template_name = "accounts/profile.html"
    form_class = ProfileForm
    success_url = reverse_lazy("accounts:profile")

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        email_changed = "email" in form.changed_data
        response = super().form_valid(form)
        if "language" in form.changed_data:  # the language cookie outranks the profile, so keep them in step
            from apps.core.regional import normalize_language, set_language_cookie

            code = normalize_language(self.object.language)
            if code:
                set_language_cookie(response, code)
            else:
                response.delete_cookie(settings.LANGUAGE_COOKIE_NAME, path=settings.LANGUAGE_COOKIE_PATH)
        if email_changed:
            user = self.object
            user.email_verified_at = None  # ایمیل تازه باید دوباره تأیید شود
            user.save(update_fields=["email_verified_at"])
            log_event(self.request, "update", user, gettext("The account email was changed; verification is required again."))
            emailing.send_verification_email(self.request, user)
            messages.info(self.request, gettext("The new email was saved. A verification link was sent to it."))
        messages.success(self.request, gettext("Your profile was updated successfully."))
        return response

    def form_invalid(self, form):
        messages.error(self.request, gettext("Could not save. Please check the form errors."))
        return super().form_invalid(form)


# --- بازیابی رمز با ایمیل (viewهای داخلی Django، قالب فارسی) ---
class PasswordResetView(RedirectAuthenticatedMixin, DjangoPasswordResetView):
    template_name = "accounts/password_reset_form.html"
    form_class = PasswordResetRequestForm
    email_template_name = "accounts/email/password_reset.txt"
    subject_template_name = "accounts/email/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "request": self.request}


class PasswordResetDoneView(DjangoPasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class PasswordResetConfirmView(DjangoPasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    form_class = StyledSetPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")

    def form_valid(self, form):
        response = super().form_valid(form)
        user = self.user
        if user.email_verified_at is None:  # رسیدن به این صفحه یعنی صندوق ایمیل در اختیار اوست
            user.email_verified_at = timezone.now()
            user.save(update_fields=["email_verified_at"])
        log_event(self.request, "update", user, gettext("The password was reset with the emailed link."), actor=user)
        return response


class PasswordResetCompleteView(DjangoPasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


# --- تأیید ایمیل ---
class VerifyEmailView(View):
    """GET فقط صفحهٔ تأیید را نشان می‌دهد (اسکنر ایمیل پیوند را باز می‌کند و نباید مصرفش کند)؛ تأیید با POST."""

    template_name = "accounts/email_verify.html"

    def get(self, request, token):
        _, state = emailing.read_verify_token(token)
        return render(request, self.template_name, {"state": state})

    def post(self, request, token):
        user, state = emailing.read_verify_token(token)
        if state == "ok":
            user.email_verified_at = timezone.now()
            user.save(update_fields=["email_verified_at"])
            log_event(request, "update", user, gettext("The account email was verified."), actor=user)
            state = "verified"
        return render(request, self.template_name, {"state": state})


class ResendVerificationView(FormView):
    """پاسخ همیشه یکسان است؛ ایمیل فقط برای حساب فعالِ تأییدنشده ارسال می‌شود."""

    template_name = "accounts/email_verify_resend.html"
    form_class = ResendVerificationForm
    success_url = reverse_lazy("accounts:verify_resend_done")

    def get_initial(self):
        user = self.request.user
        return {"email": user.email} if user.is_authenticated else {}

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "request": self.request}

    def form_valid(self, form):
        email = form.cleaned_data["email"].strip().lower()
        user = User.objects.filter(email__iexact=email, is_active=True, email_verified_at__isnull=True).first()
        if user is not None:
            emailing.send_verification_email(self.request, user)
        return super().form_valid(form)


class ResendVerificationDoneView(TemplateView):
    template_name = "accounts/email_verify_resend_done.html"


class PasswordChangeView(LoginRequiredMixin, DjangoPasswordChangeView):
    template_name = "accounts/password_change.html"
    form_class = StyledPasswordChangeForm
    success_url = reverse_lazy("accounts:password_change")

    def form_valid(self, form):
        messages.success(self.request, gettext("Your password was changed successfully."))
        return super().form_valid(form)  # نشست فعلی با update_session_auth_hash حفظ می‌شود

    def form_invalid(self, form):
        messages.error(self.request, gettext("The password was not changed. Please check the errors."))
        return super().form_invalid(form)


class UserListView(CapabilityRequiredMixin, ListView):
    required_capability = "users.view"
    template_name = "accounts/user_list.html"
    context_object_name = "users"
    paginate_by = 15

    def get_queryset(self):
        qs = User.objects.all()
        q = self.request.GET.get("q", "").strip()
        role = self.request.GET.get("role", "")
        if q:
            qs = qs.filter(
                Q(username__icontains=q)
                | Q(email__icontains=q)
                | Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
            )
        if role in Role.values:
            qs = qs.filter(role=role)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["roles"] = Role.choices
        ctx["q"] = self.request.GET.get("q", "")
        ctx["selected_role"] = self.request.GET.get("role", "")
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx["query_string"] = params.urlencode()
        manageable = {u.pk for u in ctx["users"] if self.request.user.can_manage(u)}
        ctx["manageable_ids"] = manageable
        return ctx


class UserManageView(CapabilityRequiredMixin, UpdateView):
    required_capability = "users.manage"
    template_name = "accounts/user_form.html"
    form_class = UserManageForm
    success_url = reverse_lazy("accounts:user_list")

    def get_object(self, queryset=None):
        target = get_object_or_404(User, pk=self.kwargs["pk"])
        if not self.request.user.can_manage(target):
            raise PermissionDenied
        return target

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["actor"] = self.request.user
        return kwargs

    def form_valid(self, form):
        old_role, old_active = form.initial.get("role"), form.initial.get("is_active")
        response = super().form_valid(form)
        changes = []
        if old_role != self.object.role:
            changes.append((gettext("Role from “%(label)s” to “%(label2)s”") % {"label": Role(old_role).label, "label2": Role(self.object.role).label}))
        if old_active != self.object.is_active:
            changes.append((gettext("Account %(value)s") % {"value": (gettext("was enabled") if self.object.is_active else gettext("was disabled"))}))
        if changes:
            log_event(self.request, "update", self.object, (gettext("User “%(username)s”: %(changes)s.") % {"username": self.object.username, "changes": gettext("; ").join(changes)}))
        messages.success(self.request, (gettext("Changes to user “%(display_name)s” were saved.") % {"display_name": self.object.display_name}))
        return response

    def form_invalid(self, form):
        messages.error(self.request, gettext("Could not save. Please check the errors."))
        return super().form_invalid(form)


# --- account settings and invitations (administrators) ---
class AccountPolicyView(CapabilityRequiredMixin, UpdateView):
    required_capability = "settings.manage"
    template_name = "accounts/policy_form.html"
    form_class = AccountPolicyForm
    success_url = reverse_lazy("accounts:policy")

    def get_object(self, queryset=None):
        return AccountPolicy.load()

    def form_valid(self, form):
        response = super().form_valid(form)
        changed = gettext(", ").join(str(form.fields[f].label) for f in form.changed_data) or gettext("No changes")
        log_event(self.request, "update", self.object, (gettext("Account settings were edited (%(changed)s).") % {"changed": changed}), object_type=gettext("Account settings"))
        messages.success(self.request, gettext("Account settings were saved."))
        return response

    def form_invalid(self, form):
        messages.error(self.request, gettext("The settings were not saved. Please check the form errors."))
        return super().form_invalid(form)


class InvitationListView(CapabilityRequiredMixin, FormView):
    """Pending and past invitations, and a form to send a new one."""

    required_capability = "users.manage"
    template_name = "accounts/invitations.html"
    form_class = InvitationForm
    success_url = reverse_lazy("accounts:invitations")

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "actor": self.request.user}

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["invitations"] = Invitation.objects.select_related("invited_by")[:100]
        ctx["enabled"] = AccountPolicy.load().invitations_enabled
        return ctx

    def form_valid(self, form):
        import secrets
        from datetime import timedelta

        policy = AccountPolicy.load()
        if not policy.invitations_enabled:
            messages.error(self.request, gettext("Invitations are turned off in the account settings."))
            return redirect(self.success_url)
        email = form.cleaned_data["email"]
        token = secrets.token_urlsafe(32)
        with transaction.atomic():
            Invitation.objects.filter(email__iexact=email, used_at__isnull=True, revoked_at__isnull=True).update(revoked_at=timezone.now())  # a new one replaces the old
            invitation = Invitation.objects.create(email=email, role=form.cleaned_data["role"], token_hash=Invitation.hash_token(token), invited_by=self.request.user,
                                                   expires_at=timezone.now() + timedelta(days=policy.invitation_days))
        log_event(self.request, "create", invitation, gettext("An invitation was sent to %(email)s.") % {"email": email}, object_type=gettext("Invitation"))
        if emailing.send_invitation(self.request, invitation, token, self.request.user):
            messages.success(self.request, gettext("The invitation was sent."))
        else:
            messages.error(self.request, gettext("The invitation was saved but the email could not be sent. Check the email settings and send it again."))
        return redirect(self.success_url)


class InvitationRevokeView(CapabilityRequiredMixin, View):
    required_capability = "users.manage"
    http_method_names = ["post"]

    def post(self, request, pk):
        invitation = get_object_or_404(Invitation, pk=pk)
        if invitation.state == "pending":
            invitation.revoked_at = timezone.now()
            invitation.save(update_fields=["revoked_at"])
            log_event(request, "update", invitation, gettext("The invitation to %(email)s was withdrawn.") % {"email": invitation.email}, object_type=gettext("Invitation"))
            messages.success(request, gettext("The invitation was withdrawn."))
        return redirect("accounts:invitations")


class InviteAcceptView(FormView):
    """The page behind the emailed link. Every link that cannot be used (unknown, used, withdrawn, expired, invitations turned off) shows the same page."""

    template_name = "accounts/invite_accept.html"
    form_class = InviteAcceptForm

    def dispatch(self, request, *args, **kwargs):
        self.invitation = Invitation.objects.filter(token_hash=Invitation.hash_token(kwargs["token"])).first()
        self.usable = bool(self.invitation and self.invitation.state == "pending" and AccountPolicy.load().invitations_enabled
                           and not User.objects.filter(email__iexact=self.invitation.email).exists())
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "invitation": self.invitation}

    def get_form(self, form_class=None):
        return super().get_form(form_class) if self.usable else None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs) if self.usable else {}
        ctx["usable"] = self.usable
        if self.usable:
            ctx["invited_email"] = self.invitation.email
        return ctx

    def post(self, request, *args, **kwargs):
        if not self.usable:
            return self.render_to_response(self.get_context_data())
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            with transaction.atomic():
                invitation = Invitation.objects.select_for_update().get(pk=self.invitation.pk)
                if invitation.state != "pending":  # used a moment ago from another tab
                    self.usable = False
                    return self.render_to_response(self.get_context_data())
                user = form.save()
                invitation.used_at = timezone.now()
                invitation.save(update_fields=["used_at"])
                if form.cleaned_data.get("accept_terms"):
                    SecurityProfile.objects.update_or_create(user=user, defaults={"terms_accepted_at": timezone.now()})
        except IntegrityError:
            self.usable = False
            return self.render_to_response(self.get_context_data())
        log_event(self.request, "create", user, gettext("New user “%(username)s” accepted an invitation.") % {"username": user.username}, actor=user)
        messages.success(self.request, gettext("Your account is ready. You can sign in now."))
        return redirect("accounts:login")
