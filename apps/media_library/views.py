from django.utils.translation import gettext, gettext_lazy
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import FormView, ListView

from apps.accounts.mixins import CapabilityRequiredMixin
from apps.audit.services import log_event
from apps.core.crud import AuditedDeleteView, AuditedUpdateView, QueryStringMixin

from .forms import MediaEditForm, MediaUploadForm
from .models import Media


class MediaListView(CapabilityRequiredMixin, QueryStringMixin, ListView):
    required_capability = "media.upload"
    template_name = "media_library/media_list.html"
    context_object_name = "items"
    paginate_by = 24

    def get_queryset(self):
        qs = Media.objects.select_related("uploaded_by")
        q = self.request.GET.get("q", "").strip()
        kind = self.request.GET.get("kind", "")
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(original_name__icontains=q) | Q(alt_text__icontains=q))
        if kind in Media.Kind.values:
            qs = qs.filter(kind=kind)
        if self.request.GET.get("mine") == "1":
            qs = qs.filter(uploaded_by=self.request.user)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        g = self.request.GET
        ctx.update(q=g.get("q", ""), selected_kind=g.get("kind", ""), mine=g.get("mine") == "1", kinds=Media.Kind.choices)
        ctx["manageable_ids"] = {m.pk for m in ctx["items"] if m.user_can_manage(self.request.user)}
        return ctx


class MediaUploadView(CapabilityRequiredMixin, FormView):
    required_capability = "media.upload"
    template_name = "media_library/media_upload.html"
    form_class = MediaUploadForm
    success_url = reverse_lazy("media_library:media_list")

    def form_valid(self, form):
        created = form.save(self.request.user)
        for media in created:
            log_event(self.request, "create", media, (gettext("The file “%(original_name)s” was uploaded.") % {"original_name": media.original_name}))
        messages.success(self.request, (gettext("%(created_count)s files were uploaded successfully.") % {"created_count": len(created)}))
        if len(created) == 1 and created[0].is_image and not created[0].alt_text:
            messages.info(self.request, gettext("To improve accessibility, add alternative (Alt) text for the image."))
            return redirect("media_library:media_update", pk=created[0].pk)
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, gettext("The upload did not complete. Please check the errors."))
        return super().form_invalid(form)


class MediaUpdateView(AuditedUpdateView):
    model = Media
    form_class = MediaEditForm
    object_label = gettext_lazy("Media")
    list_url_name = "media_library:media_list"
    required_capability = "media.upload"
    template_name = "media_library/media_detail.html"

    def check_object_permission(self, obj):
        # مشاهده برای همه دارندگان media.upload آزاد است؛ ویرایش (POST) فقط برای مدیر یا مالک
        if self.request.method == "POST":
            return obj.user_can_manage(self.request.user)
        return True

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["can_manage"] = self.object.user_can_manage(self.request.user)
        ctx["absolute_url"] = self.request.build_absolute_uri(self.object.file.url)
        return ctx


class MediaDeleteView(AuditedDeleteView):
    model = Media
    object_label = gettext_lazy("Media")
    list_url_name = "media_library:media_list"
    required_capability = "media.upload"

    def check_object_permission(self, obj):
        return obj.user_can_manage(self.request.user)
