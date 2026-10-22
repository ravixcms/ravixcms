from django.contrib import messages
from django.shortcuts import render
from django.urls import reverse_lazy
from django.views.generic import UpdateView

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
    return _error(request, 500, "خطای داخلی سرور", "مشکلی در سامانه رخ داد. لطفاً کمی بعد دوباره تلاش کنید.")


def csrf_failure(request, reason=""):
    return _error(
        request,
        403,
        "نشست منقضی شده است",
        "اعتبار فرم به پایان رسیده یا درخواست معتبر نبود. صفحه را بازخوانی کرده و دوباره تلاش کنید.",
    )
