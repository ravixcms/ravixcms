"""What the live progress page shows: a short list of phases (not the dozens of internal steps) and where the update is in them.

The page asks ``/panel/updates/job/<id>/status/`` every couple of seconds. That endpoint is exempt from maintenance mode (the whole ``/panel/updates/``
prefix is), and the page keeps asking while the application restarts: a few failed answers in a row are *expected* and shown as "restarting".
"""
from __future__ import annotations

from django.utils.translation import gettext_lazy

# (key, label, the internal step names that belong to it) in the order they happen
PHASES = (
    ("fetch", gettext_lazy("Downloading and verifying the package"), {"index", "compat_index", "download", "checksum", "signature", "validate", "compat", "deps", "dry_run"}),
    ("backup", gettext_lazy("Backing up files and database"), {"backup", "backup_verify"}),
    ("files", gettext_lazy("Replacing the files"), {"swap", "deps_install"}),
    ("database", gettext_lazy("Checking the new code and updating the database"), {"code_check", "migrate", "verify"}),
    ("switch", gettext_lazy("Switching to the new version"), {"venv_switch", "static"}),
    ("restart", gettext_lazy("Restarting and checking that the site answers"), {"restart", "health"}),
)
ROLLBACK_PREFIXES = ("rollback", "recover", "restore")

FINAL_OK = "succeeded"
FINAL_BAD = ("failed", "rolled_back", "needs_recovery", "cancelled")


def _phase_of(step_name: str) -> int | None:
    for index, (_key, _label, names) in enumerate(PHASES):
        if step_name in names:
            return index
    return None


def view(job, run=None) -> dict:
    """The progress of ``job`` (a ``UpdateJob``) as plain data: phases with a state, a percentage and the outcome. No secrets are involved."""
    run = run if run is not None else job.run
    steps = list(run.steps) if run else []
    names = [s.get("name", "") for s in steps]
    reached = max((p for p in (_phase_of(n) for n in names) if p is not None), default=-1)
    rolling_back = any(n.startswith(ROLLBACK_PREFIXES) for n in names)
    finished = not job.is_active
    ok = job.status == FINAL_OK
    bad = [p for p in (_phase_of(s.get("name", "")) for s in steps if not s.get("ok", True)) if p is not None]
    failed_phase = None if (ok or not finished or job.status == "cancelled") else (bad[-1] if bad else (reached if reached >= 0 else None))
    phases = []
    for index, (key, label, _names) in enumerate(PHASES):
        if index == failed_phase:
            state = "failed"
        elif ok or index < reached or (failed_phase is not None and index < failed_phase):
            state = "done"
        elif index == reached and not finished:
            state = "active"
        else:
            state = "pending"
        phases.append({"key": key, "label": str(label), "state": state})
    if job.status == "queued":
        percent = 0
    elif ok:
        percent = 100
    else:
        done = sum(1 for p in phases if p["state"] == "done")
        percent = int(100 * (done + (0.5 if any(p["state"] == "active" for p in phases) else 0)) / len(PHASES))
    return {"id": job.pk, "status": job.status, "message": job.message, "target": job.target_version, "run_id": run.pk if run else None,
            "from": run.from_version if run else "", "to": run.to_version if run else "", "phases": phases, "percent": min(percent, 100),
            "rolling_back": rolling_back, "finished": finished, "ok": ok, "detail": steps[-1].get("name", "") if steps else ""}
