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
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import FormView, ListView, TemplateView, UpdateView

from apps.audit.services import log_event
from apps.core.models import SiteSettings

from . import emailing
from .forms import (
    LoginForm,
    PasswordResetRequestForm,
    ProfileForm,
    RegisterForm,
    ResendVerificationForm,
    StyledPasswordChangeForm,
    StyledSetPasswordForm,
    UserManageForm,
)
from .mixins import CapabilityRequiredMixin
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

    def form_valid(self, form):
        response = super().form_valid(form)
        if not form.cleaned_data.get("remember_me"):
            self.request.session.set_expiry(0)  # با بستن مرورگر منقضی شود
        return response


class LogoutView(DjangoLogoutView):
    """خروج فقط با POST (محافظت‌شده با CSRF)."""

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        messages.info(request, "با موفقیت از حساب خود خارج شدید.")
        return response


class RegisterView(RedirectAuthenticatedMixin, FormView):
    template_name = "accounts/register.html"
    form_class = RegisterForm
    success_url = reverse_lazy("dashboard:home")

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated and not SiteSettings.load().registration_open:
            return self.registration_closed(request)
        return super().dispatch(request, *args, **kwargs)

    def registration_closed(self, request):
        messages.warning(request, "ثبت‌نام عمومی در حال حاضر غیرفعال است.")
        return redirect("accounts:login")

    def form_valid(self, form):
        user = form.save()
        log_event(self.request, "create", user, f"کاربر جدید «{user.username}» ثبت‌نام کرد.", actor=user)
        emailing.send_verification_email(self.request, user)
        if settings.RAVIN_REQUIRE_EMAIL_VERIFICATION:
            messages.success(self.request, "حساب شما ساخته شد. برای ورود، پیوند تأییدی را که به ایمیل شما فرستادیم باز کنید.")
            return redirect("accounts:login")
        login(self.request, user, backend="apps.accounts.backends.EmailOrUsernameBackend")
        messages.success(self.request, "حساب شما با موفقیت ساخته شد. خوش آمدید! برای تأیید ایمیل، پیوند ارسال‌شده به صندوق ایمیل خود را باز کنید.")
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, "ثبت‌نام انجام نشد. لطفاً خطاهای فرم را بررسی کنید.")
        return super().form_invalid(form)


class ProfileView(LoginRequiredMixin, UpdateView):
    template_name = "accounts/profile.html"
    form_class = ProfileForm
    success_url = reverse_lazy("accounts:profile")

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        email_changed = "email" in form.changed_data
        response = super().form_valid(form)
        if email_changed:
            user = self.object
            user.email_verified_at = None  # ایمیل تازه باید دوباره تأیید شود
            user.save(update_fields=["email_verified_at"])
            log_event(self.request, "update", user, "ایمیل حساب تغییر کرد؛ تأیید دوباره لازم است.")
            emailing.send_verification_email(self.request, user)
            messages.info(self.request, "ایمیل تازه ثبت شد. پیوند تأیید به آن فرستاده شد.")
        messages.success(self.request, "پروفایل شما با موفقیت به‌روزرسانی شد.")
        return response

    def form_invalid(self, form):
        messages.error(self.request, "ذخیره انجام نشد. لطفاً خطاهای فرم را بررسی کنید.")
        return super().form_invalid(form)


# --- بازیابی رمز با ایمیل (viewهای داخلی Django، قالب فارسی) ---
class PasswordResetView(RedirectAuthenticatedMixin, DjangoPasswordResetView):
    template_name = "accounts/password_reset_form.html"
    form_class = PasswordResetRequestForm
    email_template_name = "accounts/email/password_reset.txt"
    subject_template_name = "accounts/email/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")


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
        log_event(self.request, "update", user, "رمز عبور با پیوند ایمیلی بازیابی شد.", actor=user)
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
            log_event(request, "update", user, "ایمیل حساب تأیید شد.", actor=user)
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
        messages.success(self.request, "رمز عبور شما با موفقیت تغییر کرد.")
        return super().form_valid(form)  # نشست فعلی با update_session_auth_hash حفظ می‌شود

    def form_invalid(self, form):
        messages.error(self.request, "رمز عبور تغییر نکرد. لطفاً خطاها را بررسی کنید.")
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
            changes.append(f"نقش از «{Role(old_role).label}» به «{Role(self.object.role).label}»")
        if old_active != self.object.is_active:
            changes.append("حساب " + ("فعال شد" if self.object.is_active else "غیرفعال شد"))
        if changes:
            log_event(self.request, "update", self.object, f"کاربر «{self.object.username}»: " + "؛ ".join(changes) + ".")
        messages.success(self.request, f"تغییرات کاربر «{self.object.display_name}» ذخیره شد.")
        return response

    def form_invalid(self, form):
        messages.error(self.request, "ذخیره انجام نشد. لطفاً خطاها را بررسی کنید.")
        return super().form_invalid(form)
