from django.utils.translation import gettext
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.shortcuts import render
from django.urls import translate_url
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
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
        changed = gettext(", ").join(str(form.fields[f].label) for f in form.changed_data) or gettext("No changes")
        log_event(self.request, "update", self.object, (gettext("Site settings were edited (%(changed)s).") % {"changed": changed}), object_type=gettext("Site settings"))
        messages.success(self.request, gettext("Site settings were saved successfully."))
        return response

    def form_invalid(self, form):
        messages.error(self.request, gettext("The settings were not saved. Please check the form errors."))
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
            messages.error(request, gettext("Your account has no email; add an email in your profile first."))
            return redirect("core:site_health")
        key = f"health-test-email:{request.user.pk}"
        if not cache.add(key, 1, 30):
            messages.warning(request, gettext("Wait a few seconds and try again."))
            return redirect("core:site_health")
        try:
            sent = EmailMessage(gettext("Ravix test email"), gettext("This is a test message and shows that sending email from your site works."), to=[to]).send(fail_silently=False)
        except Exception as exc:  # noqa: BLE001
            messages.error(request, (gettext("Sending failed (%(name)s). Check the SMTP settings.") % {"name": type(exc).__name__}))
        else:
            if sent:
                messages.success(request, (gettext("The test email was handed to the mail server at %(to)s; check yourself that it reaches the inbox.") % {"to": to}))
            else:
                messages.error(request, gettext("Sending failed (the mail service did not accept the message)."))
        return redirect("core:site_health")


# ---------------------------------------------------------------- صفحه‌های خطا
def _error(request, status, title, message):
    return render(request, "errors/error.html", {"status": status, "title": title, "message": message}, status=status)


def bad_request(request, exception=None):
    return _error(request, 400, gettext("Invalid request"), gettext("Your request could not be processed."))


def permission_denied(request, exception=None):
    return _error(request, 403, gettext("Access denied"), gettext("You do not have permission to access this section."))


def not_found(request, exception=None):
    from apps.public.views import render_not_found  # import تنبل: جلوگیری از چرخه

    response = render_not_found(request)  # مسیرهای سایت عمومی با ظاهر قالب فعال؛ پنل/API → None
    if response is not None:
        return response
    return _error(request, 404, gettext("Page not found"), gettext("The page you are looking for does not exist or has been moved."))


def server_error(request):
    from .errorpages import standalone_response  # مستقل از دیتابیس/static/context processor

    return standalone_response(request, 500, gettext("Something went wrong"), gettext("A server error occurred and it was logged. Please try again a little later."))


def csrf_failure(request, reason=""):
    return _error(
        request,
        403,
        gettext("Session expired"),
        gettext("The form has expired or the request was not valid. Reload the page and try again."),
    )


@csrf_exempt  # only changes the visitor's own UI-language preference; public pages carry no CSRF token (they may be cached)
def set_language(request):
    """POST ``language`` (+ optional ``next``): remember the UI language in a cookie and, for a signed-in user, in the
    profile; then redirect back (with the URL translated to the new language prefix on the public site)."""
    from .regional import normalize_language, set_language_cookie

    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    code = normalize_language(request.POST.get("language"))
    next_url = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = request.META.get("HTTP_REFERER", "")
        if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            next_url = "/"
    if code:
        next_url = translate_url(next_url, code)
    response = HttpResponseRedirect(next_url)
    if code:
        set_language_cookie(response, code)
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.language != code:
            get_user_model().objects.filter(pk=user.pk).update(language=code)  # no save(): no side effects, nothing else changes
    return response
