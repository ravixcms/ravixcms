from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from . import services


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        ctx["profile_completeness"] = services.profile_completeness(user)
        if user.has_cap("content.write"):
            ctx["content_stats"] = services.content_stats(user)
        if user.has_cap("dashboard.stats"):
            ctx["stats"] = services.site_stats()
            ctx["latest_users"] = services.latest_users()
        return ctx
