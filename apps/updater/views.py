import logging

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import TemplateView

from apps.accounts.mixins import CapabilityRequiredMixin
from apps.audit.services import log_event as audit_log
from apps.audit.models import AuditLog

from . import pipeline, sources, versions
from .errors import UpdateError
from .logs import sanitize
from .models import UpdateRun

logger = logging.getLogger("ravix.updater")


def _audit(request, message):
    try:
        audit_log(request, AuditLog.Action.UPDATE, None, message, object_type="به‌روزرسانی")
    except Exception:  # noqa: BLE001
        logger.exception("ثبت audit ناموفق بود")


class ViewMixin(CapabilityRequiredMixin):
    required_capability = "updates.view"


class ManageMixin(CapabilityRequiredMixin):
    """اعمال تغییر: فقط مدیرکل و فقط وقتی اعمال از وب با تنظیم سرور روشن شده است."""

    required_capability = "updates.manage"
    http_method_names = ["post"]

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.has_cap("updates.manage") and not settings.RAVIX_UPDATE_WEB_APPLY:
            messages.error(request, "اعمال به‌روزرسانی از وب خاموش است. با دستور «python manage.py ravix_update apply ...» انجام دهید "
                                    "یا RAVIX_UPDATE_WEB_APPLY=true را در تنظیمات سرور بگذارید.")
            return redirect("updater:home")
        return super().dispatch(request, *args, **kwargs)


class HomeView(ViewMixin, TemplateView):
    template_name = "updater/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pipeline.mark_interrupted()
        plan, error, index = None, "", None
        if sources.has_source():
            try:
                index = sources.cached_index()
                plan = pipeline.plan_core(index)
            except UpdateError as exc:
                error = sanitize(exc)
        v = versions.component_versions()
        theme_updates, plugin_updates = {}, {}
        if index:
            for t in v["themes"]:
                es = [e for e in index["themes"].get(t["slug"], []) if versions.newer(e["version"], t["version"])]
                if es and t["slug"] != "default":
                    theme_updates[t["slug"]] = max(es, key=lambda e: versions.parse(e["version"]) or (0, 0, 0))["version"]
        from apps.plugins import sources as psources

        try:
            plugin_updates = {p["slug"]: psources.update_available(p["slug"], p["version"]) for p in v["plugins"]} if psources.has_source() else {}
        except Exception:  # noqa: BLE001
            plugin_updates = {}
        for t in v["themes"]:
            t["new"] = theme_updates.get(t["slug"])
        for p in v["plugins"]:
            p["new"] = plugin_updates.get(p["slug"])
        ctx.update(v=v, plan=plan, error=error, has_source=sources.has_source(), lock=pipeline.lock_state(), web_apply=settings.RAVIX_UPDATE_WEB_APPLY,
                   can_manage=self.request.user.has_cap("updates.manage"), theme_updates=theme_updates, plugin_updates={k: x for k, x in plugin_updates.items() if x},
                   runs=UpdateRun.objects.select_related("started_by")[:25], recovery=UpdateRun.objects.filter(status="needs_recovery")[:3])
        return ctx


class CheckView(ViewMixin, View):
    http_method_names = ["post"]

    def post(self, request):
        try:
            sources.cached_index(force=True)
            messages.success(request, "فهرست نسخه‌ها از منبع رسمی دریافت و اعتبارسنجی شد.")
        except UpdateError as exc:
            messages.error(request, sanitize(exc))
        return redirect("updater:home")


class RunDetailView(ViewMixin, TemplateView):
    template_name = "updater/run.html"

    def get_context_data(self, pk, **kwargs):
        ctx = super().get_context_data(**kwargs)
        run = get_object_or_404(UpdateRun.objects.select_related("started_by", "parent"), pk=pk)
        ctx.update(run=run, can_manage=self.request.user.has_cap("updates.manage"), web_apply=settings.RAVIX_UPDATE_WEB_APPLY,
                   can_rollback=run.component == "core" and run.kind == "upgrade" and run.status in ("succeeded", "needs_recovery") and bool(run.backup.get("dir")))
        return ctx


class ApplyView(ManageMixin, View):
    def post(self, request, component):
        slug = request.POST.get("slug", "").strip()[:60]
        version = request.POST.get("version", "").strip()[:20] or None
        try:
            if component == "core":
                run = pipeline.apply_core(version, request.user)
            elif component == "theme":
                run = pipeline.apply_theme(slug, version, request.user)
            elif component == "plugin":
                run = pipeline.apply_plugin(slug, version, request.user)
            else:
                raise Http404
        except UpdateError as exc:
            messages.error(request, sanitize(exc))
            _audit(request, f"به‌روزرسانی {component} {slug} ناموفق بود")
            last = UpdateRun.objects.first()
            return redirect("updater:run", pk=last.pk) if last and last.status != "running" else redirect("updater:home")
        _audit(request, f"به‌روزرسانی {component} {slug} به {run.to_version} انجام شد")
        messages.success(request, "به‌روزرسانی انجام شد." + (" سرویس را راه‌اندازی مجدد کنید." if run.restart_required else ""))
        return redirect("updater:run", pk=run.pk)


class RollbackView(ManageMixin, View):
    def post(self, request, pk):
        try:
            run = pipeline.rollback_run(pk, request.user, confirm_data_loss=request.POST.get("confirm_data_loss") == "on")
        except UpdateError as exc:
            messages.error(request, sanitize(exc))
            return redirect("updater:run", pk=pk)
        _audit(request, f"بازگشت به نسخهٔ قبل از اجرای #{pk}")
        messages.success(request, "بازگشت انجام شد. سرویس را راه‌اندازی مجدد کنید.")
        return redirect("updater:run", pk=run.pk)
