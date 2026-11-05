"""The single scheduled job for hosts without systemd timers: run it every 5 minutes from cPanel ▸ Cron Jobs.

It publishes scheduled posts, cancels overdue unpaid orders (Ravix Shop), sends queued email, cleans up old data, makes the daily backup
(once every 24 hours) and opens the home page so the application stays awake. A lock keeps two runs from overlapping.
"""
import os
import sys
import time
import urllib.request
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.clilang import cli_gettext

BUDGET_SECONDS = 240  # cron fires every 5 minutes; a run never plans work past this


class Locked(Exception):
    pass


class RunLock:
    """An exclusive, non-blocking file lock (released by the system even if the process is killed)."""

    def __init__(self, path: Path):
        self.path = path
        self.fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+")
        try:
            if os.name == "posix":
                import fcntl

                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:  # pragma: no cover - Windows is not a hosting target
                import msvcrt

                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.fh.close()
            raise Locked() from None
        return self

    def __exit__(self, *exc):
        try:
            self.fh.close()
        finally:
            self.fh = None


def lock_path() -> Path:
    base = getattr(settings, "RAVIX_DATA_DIR", None) or Path(settings.BASE_DIR) / "run"
    return Path(base) / "run" / "cron.lock"


class Command(BaseCommand):
    help = cli_gettext("Runs all scheduled jobs once (publishing, orders, queued email, cleanup, daily backup, keep-alive). Put it in cPanel Cron Jobs, every 5 minutes.")

    def add_arguments(self, parser):
        parser.add_argument("--no-backup", action="store_true", help=cli_gettext("Skip the daily backup in this run."))
        parser.add_argument("--no-keep-alive", action="store_true", help=cli_gettext("Do not open the home page."))

    def handle(self, *args, **options):
        from apps.core.models import CronRun

        try:
            lock = RunLock(lock_path())
            lock.__enter__()
        except Locked:
            self.stdout.write(cli_gettext("Another run is still active; this one did nothing."))
            return
        started = time.monotonic()
        run = CronRun.objects.create(started_at=timezone.now(), ok=True, summary={})
        try:
            jobs = [("updates", self.updates), ("publish", self.publish), ("orders", self.orders), ("mail", self.mail), ("cleanup", self.cleanup)]
            if not options["no_backup"]:
                jobs.append(("backup", self.backup))
            if not options["no_keep_alive"]:
                jobs.append(("keep_alive", self.keep_alive))
            for name, job in jobs:
                if time.monotonic() - started > BUDGET_SECONDS and name != "keep_alive":
                    run.summary[name] = "skipped: out of time"
                    continue
                try:
                    run.summary[name] = job() or "ok"
                except Exception as exc:  # noqa: BLE001 - one failing job must not stop the others
                    run.ok = False
                    run.summary[name] = f"failed: {type(exc).__name__}"
        finally:
            run.finished_at = timezone.now()
            run.save(update_fields=["finished_at", "ok", "summary"])
            lock.__exit__()
        self.stdout.write(cli_gettext("Scheduled jobs finished: %(summary)s") % {"summary": ", ".join(f"{k}={v}" for k, v in run.summary.items())})

    # ---------------------------------------------------------------- jobs
    def updates(self):
        from apps.updater import jobs

        return jobs.cron_tick()

    def publish(self):
        call_command("publish_scheduled", verbosity=0)

    def orders(self):
        from apps.core import modules

        if not modules.is_installed("shop") or not modules.is_enabled("shop"):
            return "skipped: shop is off"
        call_command("shop_expire_orders", verbosity=0)

    def mail(self):
        from apps.core.mailbackend import send_queued

        done = send_queued()
        return "ok" if not any(done.values()) else "ok: " + ", ".join(f"{k}={v}" for k, v in done.items() if v)

    def cleanup(self):
        from apps.core.models import CronRun, ThrottleCacheEntry
        from apps.plugins.models import PluginEvent

        now = timezone.now()
        call_command("clearsessions", verbosity=0)
        ThrottleCacheEntry.objects.filter(expires__lt=now).delete()
        PluginEvent.objects.filter(created_at__lt=now - timedelta(days=90)).delete()
        keep = list(CronRun.objects.order_by("-started_at").values_list("pk", flat=True)[:60])
        CronRun.objects.exclude(pk__in=keep).delete()
        from apps.core import hosthtaccess

        try:
            from apps.mcp.housekeeping import cleanup as mcp_cleanup

            mcp_cleanup()
        except Exception:  # noqa: BLE001 — the table may not exist yet; cleaning must never stop the other jobs
            pass
        hosthtaccess.ensure()  # restore the protective .htaccess files if someone deleted them
        work = Path(getattr(settings, "RAVIX_UPDATE_WORK_DIR", ""))
        if work.is_dir():
            import shutil

            for child in work.iterdir():
                try:
                    if time.time() - child.stat().st_mtime > 2 * 86400:
                        shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink()
                except OSError:
                    pass

    def backup(self):
        from apps.core import hostbackup

        if not hostbackup.due(24):
            return "skipped: a backup was made in the last 24 hours"
        info = hostbackup.create()
        return "ok: " + info["dir"]

    def keep_alive(self):
        from apps.core.models import SiteSettings

        if not getattr(settings, "RAVIX_KEEP_ALIVE", True) or not SiteSettings.load().keep_alive:
            return "skipped: turned off"
        url = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
        if not url:
            return "skipped: the site address is not set"
        if not url.startswith(("http://", "https://")):
            return "skipped: the site address is not http(s)"
        req = urllib.request.Request(url + "/", headers={"User-Agent": "Ravix-cron/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310  # nosec B310 - the owner's own address, http(s) only (checked above)
            resp.read(256)
        return "ok"
