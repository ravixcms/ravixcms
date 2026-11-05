from django.utils.translation import gettext
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.views import View
from django.views.generic import TemplateView

from apps.accounts.mixins import CapabilityRequiredMixin
from apps.audit.models import AuditLog
from apps.audit.services import log_event
from apps.content import samples
from apps.core.models import SiteSettings

from . import onboarding, services


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/home.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.has_cap("dashboard.view"):
            return redirect("accounts:profile")  # a member has no panel; their account page is theirs
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        if user.has_cap("users.manage"):
            ctx["members_notice_count"] = (getattr(self.request, "site_settings", None) or SiteSettings.load()).members_notice_count  # the request already holds the one shared read
        ctx["profile_completeness"] = services.profile_completeness(user)
        if user.has_cap("content.write"):
            ctx["content_stats"] = services.content_stats(user)
        if user.has_cap("dashboard.stats"):
            ctx["stats"] = services.site_stats()
            ctx["latest_users"] = services.latest_users()
        if user.has_cap("updates.view"):
            from django.utils.translation import get_language

            from apps.updater import sources, versions

            ctx["new_version"] = sources.newer_known(versions.running_version(), get_language() or "")
        if user.has_cap("settings.manage"):
            site = getattr(self.request, "site_settings", None) or SiteSettings.load()  # همان خواندنِ middleware
            ctx["onboarding"] = onboarding.context(site)
        if user.has_cap("content.publish") and user.has_cap("pages.manage") and samples.has_samples():
            ctx["has_samples"] = True
        return ctx


class OnboardingDismissView(CapabilityRequiredMixin, View):
    required_capability = "settings.manage"
    http_method_names = ["post"]

    def post(self, request):
        SiteSettings.objects.filter(pk=SiteSettings.load().pk).update(onboarding_dismissed=True)
        messages.success(request, gettext("The getting-started guide was closed."))
        return redirect("dashboard:home")


class MembersNoticeDismissView(CapabilityRequiredMixin, View):
    required_capability = "users.manage"
    http_method_names = ["post"]

    def post(self, request):
        SiteSettings.objects.filter(pk=SiteSettings.load().pk).update(members_notice_count=0)
        return redirect("dashboard:home")


class DeleteSamplesView(CapabilityRequiredMixin, View):
    required_capability = "content.publish"
    http_method_names = ["post"]

    def post(self, request):
        if not request.user.has_cap("pages.manage"):
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied
        result = samples.delete_samples()
        log_event(request, AuditLog.Action.DELETE, None, (gettext("Sample content was deleted (%(result)s posts and %(result2)s pages).") % {"result": result['posts'], "result2": result['pages']}),
                  object_type="sample_content")
        messages.success(request, gettext("Sample content was deleted. (What you had edited was kept.)"))
        return redirect("dashboard:home")
