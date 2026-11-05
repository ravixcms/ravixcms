"""Update jobs: the web (or the command line) *asks* for an update, a separate process with its own rights — the executor — *does* it.

* **VPS** (``RAVIX_UPDATE_EXECUTOR=systemd``): ``enqueue`` writes an empty file into ``<jobs dir>/queue/``; a systemd path unit starts ``ravix-update.service``
  (user ``ravix-deploy``), which runs ``manage.py ravix_update_exec``. The web user never writes code and never runs the update itself.
* **Shared hosting** (``cron``): ``ravix_cron`` (every 5 minutes at most) starts the executor as a detached process when a job is waiting.
* A running job proves it is alive with a heartbeat. If the process dies (kill -9, power loss), the next executor run finds the job without a heartbeat,
  puts the site back to the verified backup (``pipeline.recover_run``) and records what happened.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.utils import timezone
from django.utils.translation import gettext

from . import lifecycle as lifecycle_mod
from . import pipeline
from .errors import UpdateBlocked, UpdateBusy, UpdateError
from .logs import sanitize
from .models import UpdateJob, UpdateLock, UpdateRun

QUEUE_NAME = "queue"
LOCK_NAME = "executor.lock"
_STATUS_OF_RUN = {
    UpdateRun.Status.SUCCEEDED: UpdateJob.Status.SUCCEEDED,
    UpdateRun.Status.FAILED: UpdateJob.Status.FAILED,
    UpdateRun.Status.ROLLED_BACK: UpdateJob.Status.ROLLED_BACK,
    UpdateRun.Status.NEEDS_RECOVERY: UpdateJob.Status.NEEDS_RECOVERY,
    UpdateRun.Status.REVERTED: UpdateJob.Status.ROLLED_BACK,
}


def mode() -> str:
    value = (getattr(settings, "RAVIX_UPDATE_EXECUTOR", "") or "").strip().lower()
    return value if value in ("systemd", "cron") else ""


def available() -> bool:
    return bool(mode())


def jobs_dir() -> Path:
    return Path(settings.RAVIX_UPDATE_JOBS_DIR)


def queue_dir() -> Path:
    return jobs_dir() / QUEUE_NAME


def _now():
    return timezone.now()


# ------------------------------------------------------------------ asking for an update
def active_job() -> UpdateJob | None:
    return UpdateJob.objects.filter(status__in=UpdateJob.ACTIVE).order_by("id").first()


def enqueue(version: str | None = None, user=None) -> UpdateJob:
    if not available():
        raise UpdateBlocked(gettext("This site has no update executor configured (RAVIX_UPDATE_EXECUTOR); apply the update with the command line instead."))
    reap_dead()
    if active_job() is not None or pipeline.lock_state() is not None:
        raise UpdateBusy(gettext("Another upgrade is in progress; try again after it finishes."))
    job = UpdateJob.objects.create(component="core", target_version=(version or "")[:40],
                                   requested_by=user if getattr(user, "is_authenticated", False) else None)
    _signal(job)
    return job


def _signal(job: UpdateJob) -> None:
    """systemd: one file per job wakes the path unit. cron: nothing — the next ravix_cron run looks at the table."""
    if mode() != "systemd":
        return
    queue = queue_dir()
    queue.mkdir(parents=True, exist_ok=True)
    path = queue / str(job.pk)
    path.write_text("", encoding="utf-8")  # no chmod: the executor never reads it, it only needs the name, and deleting a file needs rights on the folder (2770, group ravix), not on the file


def cancel(job_id: int) -> bool:
    """A job that has not started can be withdrawn. A running one cannot (a half-done update must finish or roll back)."""
    n = UpdateJob.objects.filter(pk=job_id, status=UpdateJob.Status.QUEUED).update(status=UpdateJob.Status.CANCELLED, finished_at=_now(),
                                                                                  message=gettext("Cancelled before it started."))
    (queue_dir() / str(job_id)).unlink(missing_ok=True)
    return bool(n)


def progress(job: UpdateJob) -> dict:
    """What the progress page shows: the job, its run's steps and the phase — read-only and cheap (no secrets are stored in them)."""
    run = job.run
    return {"id": job.pk, "status": job.status, "message": job.message, "target": job.target_version, "run_id": run.pk if run else None,
            "phase": run.phase if run else "", "steps": list(run.steps) if run else [], "from": run.from_version if run else "", "to": run.to_version if run else "",
            "finished": not job.is_active}


# ------------------------------------------------------------------ the executor
class Heartbeat:
    """Refreshes the job's and the lock's heartbeat while the update runs (a thread; ``RAVIX_UPDATE_HEARTBEAT_SECONDS=0`` turns the thread off)."""

    def __init__(self, job: UpdateJob):
        self.job_id = job.pk
        self.every = int(getattr(settings, "RAVIX_UPDATE_HEARTBEAT_SECONDS", 10))
        self._stop = threading.Event()
        self._thread = None

    def beat(self) -> None:
        now = _now()
        UpdateJob.objects.filter(pk=self.job_id, status=UpdateJob.Status.RUNNING).update(heartbeat=now)
        run_id = UpdateJob.objects.filter(pk=self.job_id).values_list("run_id", flat=True).first()
        if run_id:
            UpdateLock.objects.filter(run_id=run_id).update(heartbeat=now)

    def _loop(self):
        try:
            while not self._stop.wait(self.every):
                try:
                    self.beat()
                except Exception:  # noqa: BLE001 - a missed beat must never stop the update; the next one follows
                    pass
        finally:
            connection.close()

    def __enter__(self):
        self.beat()
        if self.every > 0:
            self._thread = threading.Thread(target=self._loop, name="ravix-update-heartbeat", daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


def claim_next() -> UpdateJob | None:
    """Take the oldest waiting job (atomically: two executors can never take the same one)."""
    for job in UpdateJob.objects.filter(status=UpdateJob.Status.QUEUED).order_by("id"):
        now = _now()
        if UpdateJob.objects.filter(pk=job.pk, status=UpdateJob.Status.QUEUED).update(status=UpdateJob.Status.RUNNING, pid=os.getpid(), started_at=now, heartbeat=now):
            (queue_dir() / str(job.pk)).unlink(missing_ok=True)
            job.refresh_from_db()
            return job
    return None


def _finish(job: UpdateJob, status: str, message: str = "") -> None:
    UpdateJob.objects.filter(pk=job.pk).update(status=status, message=sanitize(message, 1500), finished_at=_now())
    job.refresh_from_db()


def execute(job: UpdateJob, *, lifecycle=None, **pipeline_kwargs) -> UpdateJob:
    """Run one claimed job through the update pipeline and record how it ended. Never raises for an update failure — the outcome is in the job."""
    life = lifecycle if lifecycle is not None else lifecycle_mod.get()

    def remember(run):
        UpdateJob.objects.filter(pk=job.pk).update(run=run)

    with Heartbeat(job):
        try:
            run = pipeline.apply_core(job.target_version or None, job.requested_by, lifecycle=life, on_run=remember, **pipeline_kwargs)
            _finish(job, _STATUS_OF_RUN.get(run.status, UpdateJob.Status.SUCCEEDED), run.error)
        except UpdateBusy as exc:
            _finish(job, UpdateJob.Status.FAILED, str(exc))
        except UpdateError as exc:
            run = UpdateRun.objects.filter(pk=UpdateJob.objects.filter(pk=job.pk).values_list("run_id", flat=True).first()).first()
            status = _STATUS_OF_RUN.get(run.status, UpdateJob.Status.FAILED) if run else UpdateJob.Status.FAILED
            _finish(job, status, str(exc))
        except Exception as exc:  # noqa: BLE001 - the executor must report, not crash
            _finish(job, UpdateJob.Status.FAILED, f"{type(exc).__name__}: {exc}")
    return job


def reap_dead() -> list[UpdateJob]:
    """Jobs that say "running" but whose process stopped answering (no heartbeat for two minutes). The run they were doing is marked as needing recovery."""
    cutoff = pipeline.heartbeat_cutoff()
    dead = list(UpdateJob.objects.filter(status=UpdateJob.Status.RUNNING, heartbeat__lt=cutoff)
                | UpdateJob.objects.filter(status=UpdateJob.Status.RUNNING, heartbeat__isnull=True, started_at__lt=cutoff))
    for job in dead:
        UpdateJob.objects.filter(pk=job.pk, status=UpdateJob.Status.RUNNING).update(status=UpdateJob.Status.NEEDS_RECOVERY, finished_at=_now(),
                                                                                  message=gettext("The update process stopped unexpectedly; recovery follows."))
        if job.run_id:
            UpdateLock.objects.filter(run_id=job.run_id).delete()
            UpdateRun.objects.filter(pk=job.run_id, status=UpdateRun.Status.RUNNING).update(
                status=UpdateRun.Status.NEEDS_RECOVERY, finished_at=_now(),
                error=gettext("The update process stopped unexpectedly (killed or the server lost power)."))
    return dead


def recover_dead(*, lifecycle=None, **kw) -> list[tuple[UpdateRun, bool]]:
    """Put back every update that died half-way. Returns [(run, recovered)]. A run whose backup cannot be verified stays as it is, with the manual steps."""
    reap_dead()
    life = lifecycle if lifecycle is not None else lifecycle_mod.get()
    out = []
    for run in pipeline.interrupted_runs():
        job = UpdateJob.objects.filter(run=run).first()
        try:
            done = pipeline.recover_run(run.pk, lifecycle=life, **kw)
            out.append((done, True))
            if job:
                _finish(job, UpdateJob.Status.ROLLED_BACK, gettext("The update was interrupted; the site was put back to the previous version automatically."))
        except UpdateBusy:
            continue
        except UpdateError as exc:
            out.append((run, False))
            if job:
                _finish(job, UpdateJob.Status.NEEDS_RECOVERY, str(exc))
    return out


class ExecutorLock:
    """One executor at a time (a file lock the system releases even if the process is killed)."""

    def __init__(self):
        self.path = jobs_dir() / LOCK_NAME
        self.fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+")  # noqa: SIM115
        try:
            import fcntl

            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, ImportError):
            self.fh.close()
            self.fh = None
            raise UpdateBusy(gettext("Another update executor is already running.")) from None
        return self

    def __exit__(self, *exc):
        if self.fh is not None:
            self.fh.close()
            self.fh = None


def run_pending(*, lifecycle=None, **pipeline_kwargs) -> list[UpdateJob]:
    """What ``manage.py ravix_update_exec`` does: recover anything that died, then run every waiting job in order."""
    done: list[UpdateJob] = []
    with ExecutorLock():
        recover_dead(lifecycle=lifecycle, **{k: v for k, v in pipeline_kwargs.items() if k in ("db_backend", "root")})
        while (job := claim_next()) is not None:
            done.append(execute(job, lifecycle=lifecycle, **pipeline_kwargs))
        if not UpdateJob.objects.filter(status=UpdateJob.Status.QUEUED).exists() and queue_dir().is_dir():
            for leftover in queue_dir().iterdir():
                leftover.unlink(missing_ok=True)  # an empty queue must not wake the path unit again
    return done


def cron_tick() -> str:
    """Shared hosting: called by ravix_cron. Recovers a dead update and starts the executor (detached) when a job is waiting."""
    if mode() != "cron":
        return "skipped"
    try:
        if reap_dead():
            pass
        if UpdateRun.objects.filter(component="core", status=UpdateRun.Status.NEEDS_RECOVERY).exclude(backup={}).exists() or UpdateJob.objects.filter(status=UpdateJob.Status.QUEUED).exists():
            start_detached()
            return "started"
    except Exception as exc:  # noqa: BLE001 - the other scheduled jobs must still run
        return f"failed: {type(exc).__name__}"
    return "idle"


def start_detached() -> None:
    root = Path(settings.BASE_DIR)
    subprocess.Popen([sys.executable, str(root / "manage.py"), "ravix_update_exec"], cwd=root, env=os.environ.copy(), stdin=subprocess.DEVNULL,  # noqa: S603  # nosec B603
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
