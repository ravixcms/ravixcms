"""The first page builder lived under /panel/builder/. Its addresses keep working: they send you to the same page in Ravix Studio."""
from django.shortcuts import redirect
from django.urls import path, re_path

from apps.accounts.mixins import CapabilityRequiredMixin  # noqa: F401


def _to_list(request):
    return redirect("studio:list", permanent=False)


def _to_editor(request, page_id):
    return redirect("studio:editor", kind="page", pk=page_id, permanent=False)


urlpatterns = [
    path("", _to_list),
    path("<int:page_id>/", _to_editor),
    re_path(r"^.*$", _to_list),
]
