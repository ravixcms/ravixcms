from django.contrib import messages
from django.shortcuts import render
from django.urls import reverse_lazy
from django.core.cache import cache
from django.shortcuts import redirect
from django.views import View
from django.views.generic import TemplateView, UpdateView

from apps.accounts.mixins import CapabilityRequiredMixin
from apps.audit.services import log_event

from .forms import SiteSettingsForm
from .models import SiteSettings


class SiteSettingsView(CapabilityRequiredMixin, UpdateView):
    required_capability = "settings.manage"
    template_name = "core/site_settings.html"
    form_class = SiteSettingsForm
    success_url = reverse_lazy("core:site_settings")

    def get_object(self, queryset=None):
        return SiteSettings.load()

    def form_valid(self, form):
        response = super().form_valid(form)
        changed = "، ".join(str(form.fields[f].label) for f in form.changed_data) or "بدون تغییر"
        log_event(self.request, "update", self.object, f"تنظیمات سایت ویرایش شد ({changed}).", object_type="تنظیمات سایت")
        messages.success(self.request, "تنظیمات سایت با موفقیت ذخیره شد.")
        return response

    def form_invalid(self, form):
        messages.error(self.request, "تنظیمات ذخیره نشد. لطفاً خطاهای فرم را بررسی کنید.")
        return super().form_invalid(form)


class SiteHealthView(CapabilityRequiredMixin, TemplateView):
    required_capability = "health.view"
    template_name = "core/site_health.html"

    def get_context_data(self, **kwargs):
        from . import sitehealth

        ctx = super().get_context_data(**kwargs)
        checks = sitehealth.run_all(self.request)
        ctx["summary"] = sitehealth.summarize(checks)
        problems = [c for c in checks if c.status in (sitehealth.CRITICAL, sitehealth.RECOMMENDED, sitehealth.UNKNOWN)]
        order = {sitehealth.CRITICAL: 0, sitehealth.RECOMMENDED: 1, sitehealth.UNKNOWN: 2}
        ctx["problems"] = sorted(problems, key=lambda c: order[c.status])
        ctx["passed"] = [c for c in checks if c.status == sitehealth.GOOD]
        ctx["test_email_to"] = self.request.user.email
        return ctx


class SiteHealthTestEmailView(CapabilityRequiredMixin, View):
    """ارسال ایمیل آزمایشی به ایمیل خود کاربر (نه نشانی دلخواه؛ تا ابزار سوءاستفادهٔ ارسال نشود) با محدودیت زمانی."""

    required_capability = "health.view"
    http_method_names = ["post"]

    def post(self, request):
        from django.core.mail import EmailMessage

        to = request.user.email
        if not to:
            messages.error(request, "حساب شما ایمیل ندارد؛ ابتدا در پروفایل ایمیل ثبت کنید.")
            return redirect("core:site_health")
        key = f"health-test-email:{request.user.pk}"
        if not cache.add(key, 1, 30):
            messages.warning(request, "چند ثانیه صبر کنید و دوباره تلاش کنید.")
            return redirect("core:site_health")
        try:
            sent = EmailMessage("ایمیل آزمایشی راویکس", "این پیام آزمایشی است و نشان می‌دهد ارسال ایمیل از سایت شما کار می‌کند.", to=[to]).send(fail_silently=False)
        except Exception as exc:  # noqa: BLE001
            messages.error(request, f"ارسال ناموفق بود ({type(exc).__name__}). تنظیمات SMTP را بررسی کنید.")
        else:
            if sent:
                messages.success(request, f"ایمیل آزمایشی به {to} تحویل سرور ایمیل شد؛ رسیدن به صندوق را خودتان بررسی کنید.")
            else:
                messages.error(request, "ارسال ناموفق بود (سرویس ایمیل پیامی نپذیرفت).")
        return redirect("core:site_health")


# ---------------------------------------------------------------- صفحه‌های خطا
def _error(request, status, title, message):
    return render(request, "errors/error.html", {"status": status, "title": title, "message": message}, status=status)


def bad_request(request, exception=None):
    return _error(request, 400, "درخواست نامعتبر", "درخواست شما قابل پردازش نبود.")


def permission_denied(request, exception=None):
    return _error(request, 403, "دسترسی غیرمجاز", "شما اجازه دسترسی به این بخش را ندارید.")


def not_found(request, exception=None):
    from apps.public.views import render_not_found  # import تنبل: جلوگیری از چرخه

    response = render_not_found(request)  # مسیرهای سایت عمومی با ظاهر قالب فعال؛ پنل/API → None
    if response is not None:
        return response
    return _error(request, 404, "صفحه پیدا نشد", "صفحه‌ای که دنبال آن هستید وجود ندارد یا جابه‌جا شده است.")


def server_error(request):
    from .errorpages import standalone_response  # مستقل از دیتابیس/static/context processor

    return standalone_response(request, 500, "مشکلی پیش آمد", "خطایی در سمت سرور رخ داد و گزارش آن ثبت شد. لطفاً کمی بعد دوباره تلاش کنید.")


def csrf_failure(request, reason=""):
    return _error(
        request,
        403,
        "نشست منقضی شده است",
        "اعتبار فرم به پایان رسیده یا درخواست معتبر نبود. صفحه را بازخوانی کرده و دوباره تلاش کنید.",
    )
