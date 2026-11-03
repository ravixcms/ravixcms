"""Panel and account pages: the settings, and the lists of connected apps with a Revoke button."""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import TemplateView, UpdateView

from apps.accounts.mixins import CapabilityRequiredMixin
from apps.audit.models import AuditLog
from apps.audit.services import log_event

from . import addresses, clients, scopes as sc
from .forms import McpSettingsForm
from .models import McpConnection, McpSettings


def _rows(queryset):
    out = []
    for conn in queryset:
        out.append({
            "conn": conn, "scopes": [(s, sc.LABELS[s]) for s in conn.scope_list], "hosts": conn.client.redirect_hosts(),
            "trusted": all(clients.is_trusted_host(h, McpSettings.load()) for h in conn.client.redirect_hosts()) if conn.client.redirect_hosts() else False,
        })
    return out


class SettingsView(CapabilityRequiredMixin, UpdateView):
    required_capability = "mcp.manage"
    template_name = "mcp/settings.html"
    form_class = McpSettingsForm
    success_url = reverse_lazy("mcp:settings")

    def get_object(self, queryset=None):
        return McpSettings.load()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        cfg = self.object
        ctx.update({
            "tab": "settings", "endpoint_url": addresses.resource(self.request), "multisite_blocked": McpSettings.multisite_blocks(), "is_active": cfg.is_active,
            "active_count": McpConnection.objects.filter(revoked_at__isnull=True).count(), "known_hosts": clients.KNOWN_HOSTS,
            "https": self.request.is_secure() or addresses.issuer(self.request).startswith("https://"),
        })
        return ctx

    def form_valid(self, form):
        was = McpSettings.load().is_active
        response = super().form_valid(form)
        changed = _(", ").join(str(form.fields[f].label) for f in form.changed_data) or _("No changes")
        log_event(self.request, AuditLog.Action.UPDATE, None, _("Ravix MCP settings were edited (%(changed)s).") % {"changed": changed}, object_type=_("AI assistant"))
        if not was and self.object.is_active:
            messages.success(self.request, _("Ravix MCP is on. AI assistants can now connect to this site."))
        else:
            messages.success(self.request, _("The settings were saved."))
        return response

    def form_invalid(self, form):
        messages.error(self.request, _("The settings were not saved. Please check the form errors."))
        return super().form_invalid(form)


class AdminConnectionsView(CapabilityRequiredMixin, TemplateView):
    required_capability = "mcp.manage"
    template_name = "mcp/connections_admin.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = McpConnection.objects.filter(revoked_at__isnull=True).select_related("user", "client")
        ctx.update({"tab": "connections", "rows": _rows(qs[:200]), "is_active": McpSettings.load().is_active})
        return ctx


class AdminRevokeView(CapabilityRequiredMixin, View):
    required_capability = "mcp.manage"

    def post(self, request, pk):
        conn = get_object_or_404(McpConnection.objects.select_related("client", "user"), pk=pk)
        conn.revoke("revoked_by_admin")
        log_event(request, AuditLog.Action.MCP, None, _("The connection of “%(app)s” for %(user)s was ended by an administrator.") % {"app": conn.client.name, "user": conn.user.display_name}, object_type=_("AI assistant"))
        messages.success(request, _("The connection was ended."))
        return redirect("mcp:admin_connections")


class MyConnectionsView(LoginRequiredMixin, TemplateView):
    template_name = "mcp/connections_mine.html"

    def dispatch(self, request, *args, **kwargs):
        if not McpSettings.load().is_active:
            raise Http404()
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = McpConnection.objects.filter(user=self.request.user, revoked_at__isnull=True).select_related("client")
        ctx.update({"tab": "apps", "rows": _rows(qs)})
        return ctx


class MyRevokeView(LoginRequiredMixin, View):
    def post(self, request, pk):
        conn = get_object_or_404(McpConnection.objects.select_related("client"), pk=pk, user=request.user)  # somebody else's connection is a 404
        conn.revoke("revoked_by_user")
        log_event(request, AuditLog.Action.MCP, None, _("The connection of “%(app)s” was ended by its owner.") % {"app": conn.client.name}, object_type=_("AI assistant"))
        messages.success(request, _("The connection was ended."))
        return redirect("mcp:my_apps")
