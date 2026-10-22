from django.contrib import messages
from apps.core.params import safe_id, safe_ids
from django.db.models import Count, Q
from django.shortcuts import redirect
from django.urls import reverse
from django.views import View
from django.views.generic import ListView

from apps.accounts.mixins import CapabilityRequiredMixin
from apps.audit.services import log_event
from apps.core.crud import QueryStringMixin

from . import services
from .models import Comment

CAP = "comments.moderate"
STATUS_LABELS = {"approve": "تأیید", "pending": "بازگشت به «در انتظار»", "spam": "اسپم", "trash": "انتقال به زباله"}


class CommentListView(CapabilityRequiredMixin, QueryStringMixin, ListView):
    required_capability = CAP
    template_name = "comments/list.html"
    context_object_name = "comments"
    paginate_by = 30

    def current_status(self):
        s = self.request.GET.get("status", "")
        return s if s in Comment.Status.values else ""

    def get_queryset(self):
        qs = Comment.objects.select_related("post", "parent")
        status = self.current_status()
        qs = qs.filter(status=status) if status else qs.exclude(status__in=[Comment.Status.SPAM, Comment.Status.TRASH])
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(body__icontains=q) | Q(author_name__icontains=q) | Q(author_email__icontains=q) | Q(post__title__icontains=q))
        post = self.request.GET.get("post", "")
        post_id = safe_id(post)
        if post_id is not None:
            qs = qs.filter(post_id=post_id)
        return qs.order_by("-created_at", "-id")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        counts = dict(Comment.objects.values_list("status").annotate(n=Count("id")))
        ctx["tabs"] = [("", "فعال", sum(counts.get(s, 0) for s in ("pending", "approved")))] + [
            (value, label, counts.get(value, 0)) for value, label in Comment.Status.choices
        ]
        ctx["status"] = self.current_status()
        ctx["q"] = self.request.GET.get("q", "")
        ctx["actions"] = STATUS_LABELS
        return ctx


class CommentBulkView(CapabilityRequiredMixin, View):
    """عملیات گروهی (فقط POST با CSRF). حذف دائمی فقط برای اسپم/زباله مجاز است."""

    required_capability = CAP
    http_method_names = ["post"]

    def post(self, request):
        action = request.POST.get("action", "")
        back = request.POST.get("next", "")
        target = back if back.startswith(reverse("comments:list")) and "//" not in back else reverse("comments:list")
        if action == "empty_trash":
            qs = Comment.objects.filter(status=Comment.Status.TRASH)
        else:
            ids = safe_ids(request.POST.getlist("ids"))
            if not ids:
                messages.error(request, "هیچ دیدگاهی انتخاب نشده است.")
                return redirect(target)
            qs = Comment.objects.filter(pk__in=ids)
        if action in services.BULK_ACTIONS:
            n = services.change_status(request, qs, services.BULK_ACTIONS[action])
            log_event(request, "update", None, f"وضعیت {n} دیدگاه: {STATUS_LABELS[action]}", object_type="دیدگاه")
            messages.success(request, f"{n} دیدگاه: {STATUS_LABELS[action]}.")
        elif action in ("delete", "empty_trash"):
            qs = qs.filter(status__in=[Comment.Status.SPAM, Comment.Status.TRASH])
            n = qs.count()
            qs.delete()
            log_event(request, "delete", None, f"{n} دیدگاه برای همیشه حذف شد.", object_type="دیدگاه")
            messages.success(request, f"{n} دیدگاه برای همیشه حذف شد.")
        else:
            messages.error(request, "عملیات نامعتبر است.")
        return redirect(target)
