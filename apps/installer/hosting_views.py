"""The shared-hosting installer pages (see hostinstall.py for the security model). Steps: install code → host checks → database → site and
administrator → progress → done. Nothing here touches the Django database: in setup mode there is none yet."""
import re
import time

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import get_script_prefix
from django.utils.translation import gettext

from apps.core import hosting

from . import hostinstall as hi
from .forms import HostDatabaseForm, HostSiteForm, HostUnlockForm

UNLOCKED = "ravix_install_unlocked"
STEP = "ravix_install_step"
DB = "ravix_install_db"
STALE_SECONDS = 90


def _render(request, step, **ctx):
    ctx.setdefault("step", step)
    ctx["steps"] = [("checks", gettext("Host check")), ("db", gettext("Database")), ("site", gettext("Site and administrator")), ("run", gettext("Installing"))]
    response = render(request, "installer/hosting.html", ctx)
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


def _site_urls(request):
    host = request.get_host()
    script = get_script_prefix().rstrip("/")
    scheme = "https" if request.is_secure() else "http"
    name = host.split(":")[0].lower()
    hosts = [name]
    if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", name) and not re.fullmatch(r"[0-9.]+", name) and not name.startswith("www."):
        hosts.append("www." + name)
    return f"{scheme}://{host}{script}", script, ",".join(hosts)


def install(request):
    if request.method not in ("GET", "POST"):
        return HttpResponse(status=405)
    status = hi.read_status()
    if hosting.installed_lock_exists():
        # The installer is locked for good. The one thing still shown is the last page of the run that just finished, to the browser that ran it.
        if request.session.get(UNLOCKED) and status.get("state") == "done":
            response = _render(request, "done", status=status, cron=status.get("cron") or hosting.cron_command(), site_url=status.get("site_url", ""),
                               login_url=status.get("site_url", "").rstrip("/") + "/accounts/login/")
            request.session.flush()
            hi.write_status(seen=True)  # the job restarts the application now
            return response
        raise Http404
    if not settings.RAVIX_INSTALLER_ENABLED:
        raise Http404
    if hi.https_required(request):
        return HttpResponseRedirect("https://" + request.get_host() + request.get_full_path())

    if not request.session.get(UNLOCKED):
        return _unlock(request)

    if status.get("state") == "running":
        stale = time.time() - float(status.get("updated") or 0) > STALE_SECONDS and not hi.job_alive(status)
        return _render(request, "run", status=status, stale=stale, manual=hi.manual_command(), steps_total=hi.STEPS)
    if status.get("state") == "error":
        if request.method == "POST" and request.POST.get("action") == "retry":
            hi.status_path().unlink(missing_ok=True)
            request.session[STEP] = "site"
            return HttpResponseRedirect(request.path)
        return _render(request, "error", error=status.get("error", ""))

    step = request.session.get(STEP, "checks")
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "back":
            request.session[STEP] = {"db": "checks", "site": "db"}.get(step, step)
            return HttpResponseRedirect(request.path)
        if step == "checks" and action == "continue":
            if hi.has_blockers(hi.host_checks(request)):
                return _render(request, "checks", rows=hi.host_checks(request), blocked=True)
            request.session[STEP] = "db"
            return HttpResponseRedirect(request.path)
        if step == "db" and action == "db":
            form = HostDatabaseForm(request.POST)
            if form.is_valid():
                cfg = {k: form.cleaned_data.get(k) for k in ("engine", "host", "port", "name", "user", "password")}
                ok, error, notes = hi.test_database(cfg)
                if ok:
                    request.session[DB] = cfg
                    request.session[STEP] = "site"
                    request.session["ravix_install_notes"] = [str(n) for n in notes]
                    return HttpResponseRedirect(request.path)
                form.add_error(None, error)
            return _render(request, "db", form=form)
        if step == "site" and action == "site":
            form = HostSiteForm(request.POST)
            cfg = request.session.get(DB)
            if form.is_valid() and cfg:
                d = form.cleaned_data
                site_url, script, allowed = _site_urls(request)
                hi.start_job({
                    "db": cfg, "database_url": hi.database_url(cfg), "site_name": d["site_name"], "language": d["language"], "timezone": d["timezone"],
                    "username": d["username"], "email": d["email"], "admin_password": d["password1"], "site_url": site_url, "script_name": script,
                    "allowed_hosts": allowed,
                })
                request.session.pop(DB, None)
                request.session[STEP] = "run"
                return HttpResponseRedirect(request.path)
            if not cfg:
                request.session[STEP] = "db"
                return HttpResponseRedirect(request.path)
            return _render(request, "site", form=form)
        return HttpResponseRedirect(request.path)

    if step == "db":
        return _render(request, "db", form=HostDatabaseForm())
    if step == "site":
        return _render(request, "site", form=HostSiteForm(), notes=request.session.get("ravix_install_notes", []))
    if step == "run":
        request.session[STEP] = "checks"
    rows = hi.host_checks(request)
    return _render(request, "checks", rows=rows, blocked=hi.has_blockers(rows))


def _unlock(request):
    hi.ensure_key()  # the file exists from the first visit on
    if request.method == "POST":
        if hi.blocked(request):
            return _render(request, "unlock", form=HostUnlockForm(), locked_out=True)
        form = HostUnlockForm(request.POST)
        if form.is_valid() and hi.key_matches(form.cleaned_data["code"]):
            hi.note_success(request)
            request.session.cycle_key()
            request.session[UNLOCKED] = True
            request.session[STEP] = "checks"
            return HttpResponseRedirect(request.path)
        hi.note_failure(request)
        form.errors.pop("code", None)
        form.add_error("code", gettext("The install code is not correct."))
        return _render(request, "unlock", form=form)
    return _render(request, "unlock", form=HostUnlockForm(), locked_out=hi.blocked(request))
