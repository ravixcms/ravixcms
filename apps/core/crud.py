"""
کلاس‌های پایه CRUD با ثبت خودکار رویداد (audit) و پیام موفقیت/شکست.

هر ماژول جدید فقط ویژگی‌های ``model``، ``form_class``، ``object_label``، ``list_url_name`` و
``required_capability`` را تعیین می‌کند. برای کنترل سطح شیء، ``check_object_permission`` را بازنویسی کنید.
"""
from django.utils.translation import gettext
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, UpdateView

from apps.accounts.mixins import CapabilityRequiredMixin


class QueryStringMixin:
    """query string فعلی (بدون page) را برای لینک‌های صفحه‌بندی در اختیار قالب می‌گذارد."""

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx["query_string"] = params.urlencode()
        return ctx


class ObjectPermissionMixin:
    def check_object_permission(self, obj) -> bool:
        return True

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        if not self.check_object_permission(obj):
            raise PermissionDenied
        return obj


class CrudContextMixin:
    object_label = ""          # مثلاً «نوشته»
    list_url_name = ""         # مثلاً "content:post_list"
    template_name = "crud/form.html"
    pass_user_to_form = False

    def get_success_url(self):
        return reverse_lazy(self.list_url_name)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if self.pass_user_to_form:
            kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["object_label"] = self.object_label
        ctx["cancel_url"] = reverse_lazy(self.list_url_name)
        return ctx

    def audit(self, action, obj, message):
        from apps.audit.services import log_event

        log_event(self.request, action, obj, message)


class AuditedCreateView(CapabilityRequiredMixin, CrudContextMixin, CreateView):
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["heading"] = (gettext("New %(object_label)s") % {"object_label": self.object_label})
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        self.audit("create", self.object, (gettext("%(object_label)s “%(object)s” was created.") % {"object_label": self.object_label, "object": self.object}))
        messages.success(self.request, (gettext("%(object_label)s “%(object)s” was created successfully.") % {"object_label": self.object_label, "object": self.object}))
        return response

    def form_invalid(self, form):
        messages.error(self.request, gettext("Could not save. Please check the form errors."))
        return super().form_invalid(form)


class AuditedUpdateView(CapabilityRequiredMixin, ObjectPermissionMixin, CrudContextMixin, UpdateView):
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["heading"] = (gettext("Edit %(object_label)s") % {"object_label": self.object_label})
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        self.audit("update", self.object, (gettext("%(object_label)s “%(object)s” was edited.") % {"object_label": self.object_label, "object": self.object}))
        messages.success(self.request, (gettext("The changes to %(object_label)s “%(object)s” were saved.") % {"object_label": self.object_label, "object": self.object}))
        return response

    def form_invalid(self, form):
        messages.error(self.request, gettext("Could not save. Please check the form errors."))
        return super().form_invalid(form)


class AuditedDeleteView(CapabilityRequiredMixin, ObjectPermissionMixin, CrudContextMixin, DeleteView):
    template_name = "crud/confirm_delete.html"

    def form_valid(self, form):
        obj = self.get_object()
        label, pk, text = self.object_label, obj.pk, str(obj)
        from apps.audit.services import log_event

        # لاگ پیش از حذف ثبت می‌شود تا شناسه و عنوان در دسترس باشد؛ اگر حذف شکست بخورد لاگ هم برمی‌گردد.
        with transaction.atomic():
            log_event(self.request, "delete", obj, (gettext("%(label)s “%(text)s” was deleted.") % {"label": label, "text": text}))
            response = super().form_valid(form)
        messages.success(self.request, (gettext("%(label)s “%(text)s” was deleted.") % {"label": label, "text": text}))
        return response
