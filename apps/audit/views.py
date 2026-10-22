from django.db.models import Q
from django.views.generic import ListView

from apps.accounts.mixins import CapabilityRequiredMixin
from apps.core.crud import QueryStringMixin

from .models import AuditLog


class AuditLogListView(CapabilityRequiredMixin, QueryStringMixin, ListView):
    required_capability = "audit.view"
    template_name = "audit/log_list.html"
    context_object_name = "events"
    paginate_by = 25

    def get_queryset(self):
        qs = AuditLog.objects.select_related("actor")
        q = self.request.GET.get("q", "").strip()
        action = self.request.GET.get("action", "")
        object_type = self.request.GET.get("type", "")
        if q:
            qs = qs.filter(Q(message__icontains=q) | Q(actor_name__icontains=q) | Q(object_repr__icontains=q))
        if action in AuditLog.Action.values:
            qs = qs.filter(action=action)
        if object_type:
            qs = qs.filter(object_type=object_type)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["actions"] = AuditLog.Action.choices
        ctx["types"] = list(AuditLog.objects.exclude(object_type="").values_list("object_type", flat=True).distinct().order_by("object_type"))
        ctx["q"] = self.request.GET.get("q", "")
        ctx["selected_action"] = self.request.GET.get("action", "")
        ctx["selected_type"] = self.request.GET.get("type", "")
        return ctx
