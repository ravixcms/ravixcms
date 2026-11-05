"""The update executor: recovers an update that died half-way, then runs every waiting update job. Started by the systemd path unit (VPS)
or by ravix_cron (shared hosting); it can also be run by hand. The outcome of each job is recorded in the update history."""
from django.core.management.base import BaseCommand

from apps.core.clilang import cli_gettext
from apps.updater import jobs
from apps.updater.errors import UpdateBusy


class Command(BaseCommand):
    help = cli_gettext("Runs the waiting update jobs (and recovers an update that stopped half-way). Started by the systemd path unit or by ravix_cron.")

    def handle(self, *args, **options):
        try:
            done = jobs.run_pending()
        except UpdateBusy as exc:
            self.stdout.write(str(exc))
            return
        if not done:
            self.stdout.write(cli_gettext("No update job was waiting."))
        for job in done:
            line = cli_gettext("Update job #%(a1)s: %(a2)s") % {"a1": job.pk, "a2": job.get_status_display()}
            self.stdout.write(self.style.SUCCESS(line) if job.status == job.Status.SUCCEEDED else self.style.ERROR(line + (f" — {job.message}" if job.message else "")))
