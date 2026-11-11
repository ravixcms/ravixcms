from apps.core.clilang import cli_gettext
from django.utils.translation import gettext
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from apps.accounts.mixins import CapabilityRequiredMixin
from apps.audit.services import log_event as audit_log
from apps.audit.models import AuditLog

from . import jobs, manifest, package, pipeline, sources, versions
from .errors import UpdateError
from .logs import sanitize
from .models import UpdateJob, UpdateRun

logger = logging.getLogger("ravix.updater")


def _audit(request, message):
    try:
        audit_log(request, AuditLog.Action.UPDATE, None, message, object_type=gettext("Update"))
    except Exception:  # noqa: BLE001
        logger.exception(cli_gettext("Recording the audit entry failed"))


class ViewMixin(CapabilityRequiredMixin):
    required_capability = "updates.view"


class ManageMixin(CapabilityRequiredMixin):
    """اعمال تغییر: فقط مدیرکل و فقط وقتی اعمال از وب با تنظیم سرور روشن شده است."""

    required_capability = "updates.manage"
    http_method_names = ["post"]

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.has_cap("updates.manage") and not settings.RAVIX_UPDATE_WEB_APPLY:
            messages.error(request, gettext("Applying updates from the web is turned off. Run it with the command “python manage.py ravix_update apply ...” or set RAVIX_UPDATE_WEB_APPLY=true in the server settings."))
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
        from django.utils.translation import get_language

        notes = sources.notes_for(plan["latest"], get_language() or "") if plan and plan.get("latest") else ""
        active = jobs.active_job()
        report = manual_files() if plan and plan.get("latest") else None
        ctx.update(notes=notes, can_queue=jobs.available() and self.request.user.has_cap("updates.manage"), job=active, manual=report if report and (report["modified"] or report["added"]) else None,
                   last_job=UpdateJob.objects.exclude(status="cancelled").first())
        ctx.update(v=v, plan=plan, error=error, has_source=sources.has_source(), source_state=sources.source_state(), lock=pipeline.lock_state(), web_apply=settings.RAVIX_UPDATE_WEB_APPLY,
                   can_manage=self.request.user.has_cap("updates.manage"), theme_updates=theme_updates, plugin_updates={k: x for k, x in plugin_updates.items() if x},
                   runs=UpdateRun.objects.select_related("started_by")[:25], recovery=UpdateRun.objects.filter(status="needs_recovery")[:3])
        return ctx


class GuideView(ViewMixin, TemplateView):
    """«How to update Ravix» in the language of the panel."""

    template_name = "updater/guide.html"


class QueueMixin(CapabilityRequiredMixin):
    """Asking for an update: allowed to whoever may manage updates when a separate executor does the work (it has its own rights, so this does not need
    RAVIX_UPDATE_WEB_APPLY, which is for applying inside the web process)."""

    required_capability = "updates.manage"
    http_method_names = ["post"]


def manual_files() -> dict | None:
    """Files the owner put into the managed folders (or edited) that an update would overwrite or remove; None when the install has no manifest."""
    try:
        return manifest.local_report(versions.app_root(), package.CORE_ENTRIES)
    except OSError:
        return None


class QueueView(QueueMixin, View):
    def post(self, request):
        version = request.POST.get("version", "").strip()[:20] or None
        report = manual_files() or {"modified": [], "added": []}
        if (report["modified"] or report["added"]) and request.POST.get("acknowledge") != "on":
            messages.error(request, gettext("Some files you changed or added inside the program folders would be replaced or removed. Tick the confirmation box to continue (a backup is made first)."))
            return redirect("updater:home")
        try:
            job = jobs.enqueue(version, request.user)
        except UpdateError as exc:
            messages.error(request, sanitize(exc))
            return redirect("updater:home")
        _audit(request, (gettext("Queued an update to %(version)s") % {"version": version or gettext("the latest version")}))
        return redirect("updater:job", pk=job.pk)


class JobView(ViewMixin, TemplateView):
    template_name = "updater/job.html"

    def get_context_data(self, pk, **kwargs):
        from . import progress

        ctx = super().get_context_data(**kwargs)
        job = get_object_or_404(UpdateJob.objects.select_related("run"), pk=pk)
        ctx.update(job=job, state=progress.view(job), can_manage=self.request.user.has_cap("updates.manage"),
                   labels={"restarting": gettext("The site is restarting. This page keeps trying…"), "queued": gettext("Waiting for the update service to start…")})
        return ctx


class JobStatusView(ViewMixin, View):
    http_method_names = ["get"]

    def get(self, request, pk):
        job = get_object_or_404(UpdateJob.objects.select_related("run"), pk=pk)
        return JsonResponse(jobs.progress(job), headers={"Cache-Control": "no-store"})


class JobCancelView(QueueMixin, View):
    def post(self, request, pk):
        if jobs.cancel(pk):
            _audit(request, gettext("Cancelled a queued update"))
            messages.success(request, gettext("The queued update was cancelled."))
        else:
            messages.error(request, gettext("Only an update that has not started yet can be cancelled."))
        return redirect("updater:home")


class CheckView(ViewMixin, View):
    http_method_names = ["post"]

    def post(self, request):
        try:
            sources.cached_index(force=True)
            messages.success(request, gettext("The version list was fetched from the official source and validated."))
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
            _audit(request, (gettext("Updating %(component)s %(slug)s failed") % {"component": component, "slug": slug}))
            last = UpdateRun.objects.first()
            return redirect("updater:run", pk=last.pk) if last and last.status != "running" else redirect("updater:home")
        _audit(request, (gettext("Updated %(component)s %(slug)s to %(to_version)s") % {"component": component, "slug": slug, "to_version": run.to_version}))
        messages.success(request, (gettext("Update completed.%(value)s") % {"value": (gettext(" Restart the service.") if run.restart_required else "")}))
        return redirect("updater:run", pk=run.pk)


class UploadView(ManageMixin, View):
    """Shared hosting: upload the update bundle (index.json [+ index.json.sig] + the ravix-core-*.zip it lists) instead of fetching it from a server.
    It goes through exactly the same checks as an online update (SHA-256 and signature per the trusted keys), the same automatic backup, the
    same migrate step and the same automatic rollback."""

    def post(self, request):
        import shutil
        import zipfile

        from . import work

        upload = request.FILES.get("bundle")
        if upload is None:
            messages.error(request, gettext("Choose the update ZIP file."))
            return redirect("updater:home")
        folder = None
        try:
            folder = work.new_folder("upload")
            work.extract_bundle(upload, folder, max_total=settings.RAVIX_UPDATE_MAX_PACKAGE_BYTES * 2)
            run = pipeline.apply_core(None, request.user, source=sources.DirSource(folder))
        except (UpdateError, zipfile.BadZipFile) as exc:
            messages.error(request, sanitize(exc) if isinstance(exc, UpdateError) else gettext("The file is not a valid ZIP."))
            _audit(request, gettext("Updating the core from an uploaded file failed"))
            last = UpdateRun.objects.first()
            return redirect("updater:run", pk=last.pk) if last and last.status != "running" and last.started_at > timezone.now() - timedelta(minutes=5) else redirect("updater:home")
        finally:
            if folder is not None:
                shutil.rmtree(folder, ignore_errors=True)
        _audit(request, (gettext("Updated core to %(to_version)s from an uploaded file") % {"to_version": run.to_version}))
        messages.success(request, (gettext("Update completed.%(value)s") % {"value": (gettext(" Restart the service.") if run.restart_required else "")}))
        return redirect("updater:run", pk=run.pk)


class RollbackView(ManageMixin, View):
    def post(self, request, pk):
        try:
            run = pipeline.rollback_run(pk, request.user, confirm_data_loss=request.POST.get("confirm_data_loss") == "on")
        except UpdateError as exc:
            messages.error(request, sanitize(exc))
            return redirect("updater:run", pk=pk)
        _audit(request, (gettext("Rollback to the version before run #%(pk)s") % {"pk": pk}))
        messages.success(request, gettext("Rollback completed. Restart the service."))
        return redirect("updater:run", pk=run.pk)
