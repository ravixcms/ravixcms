"""The installation job of the shared-hosting web installer. It runs detached from the web request (so a slow migration cannot time out), writes its
progress to ``<data>/setup-jobs/status.json`` and can be started by hand when a host kills background processes::

    python manage.py ravix_host_install --resume
"""
import json
import os
import secrets
import subprocess
import sys
import time

from django.core.management.base import BaseCommand, CommandError

from apps.core import hosting
from apps.core.clilang import cli_gettext
from apps.installer import hostinstall as hi


class Command(BaseCommand):
    help = cli_gettext("Runs the installation prepared by the web installer on shared hosting (migrate, static files, administrator, settings file, lock).")

    def add_arguments(self, parser):
        parser.add_argument("--job", help=cli_gettext("Path of the job file written by the web installer"))
        parser.add_argument("--resume", action="store_true", help=cli_gettext("Run the pending job again (use this if the background process was stopped by the host)"))

    def handle(self, *args, **o):
        if hosting.installed_lock_exists():
            raise CommandError(cli_gettext("Installation has already been done."))
        path = o["job"]
        if not path:
            found = sorted(hi.jobs_dir().glob("job-*.json"), key=lambda p: p.stat().st_mtime)
            if not found:
                raise CommandError(cli_gettext("There is no pending installation job. Start again from the /install/ page."))
            path = str(found[-1])
        try:
            with open(path, encoding="utf-8") as fh:
                job = json.load(fh)
        except (OSError, ValueError) as exc:
            raise CommandError(cli_gettext("The job file could not be read.")) from exc
        hi.write_status(state="running", step="", done_steps=[], error="", pid=os.getpid())
        try:
            self.run_job(job)
        except Exception as exc:  # noqa: BLE001 - the message goes to the installer page; secrets are removed
            text = " ".join(str(exc).split())[:600]
            for secret in (job.get("db", {}).get("password"), job.get("admin_password"), job.get("secret_key")):
                if secret:
                    text = text.replace(secret, "***")
            hi.write_status(state="error", error=text)
            try:
                os.unlink(path)  # a failed job is started again from the form; passwords do not stay on disk
            except OSError:
                pass
            raise CommandError(text) from None
        try:
            os.unlink(path)
        except OSError:
            pass

    # ------------------------------------------------------------------
    def sub(self, *argv, env, step):
        hi.write_status(step=step)
        proc = subprocess.run([hosting.python_executable(), str(hosting.APP_ROOT / "manage.py"), *argv], cwd=str(hosting.APP_ROOT), env=env,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=1500)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "").strip()[-600:] or f"{argv[0]} failed")
        hi.write_status(done_steps=hi.read_status().get("done_steps", []) + [step])

    def run_job(self, job):
        from cryptography.fernet import Fernet

        db = job["db"]
        secret_key = job.get("secret_key") or secrets.token_urlsafe(50)
        secrets_key = job.get("secrets_key") or Fernet.generate_key().decode()
        data = hosting.ensure_dir(hosting.data_dir())
        hosting.ensure_dir(data / "db")
        values = {
            "DJANGO_SECRET_KEY": secret_key,
            "DJANGO_ALLOWED_HOSTS": job["allowed_hosts"],
            "RAVIX_SITE_URL": job["site_url"],
            "RAVIX_SECRETS_KEY": secrets_key,
        }
        if job.get("database_url"):
            values["DATABASE_URL"] = job["database_url"]
        if job.get("script_name"):
            values["RAVIX_SCRIPT_NAME"] = job["script_name"]
        # ravix_install removes "the install token file" when it finishes; here that file is the install code, which must stay until the very end.
        env = dict(os.environ, DJANGO_SETTINGS_MODULE=hosting.SETTINGS_MODULE, RAVIX_ADMIN_PASSWORD=job["admin_password"],
                   RAVIX_INSTALL_TOKEN_FILE=str(hi.jobs_dir() / "unused-token"), **values)

        self.sub("migrate", "--noinput", env=env, step="database")
        self.sub("collectstatic", "--noinput", env=env, step="static")
        self.sub("ravix_install", "--skip-migrate", "--site-name", job["site_name"], "--language", job["language"], "--timezone", job["timezone"],
                 "--username", job["username"], "--email", job["email"], "--password-env", "RAVIX_ADMIN_PASSWORD", env=env, step="admin")
        hi.write_status(step="settings")
        from apps.core import hosthtaccess

        hosthtaccess.ensure(media_root=hosting.public_html() and (hosting.public_html() / (job.get("script_name", "").strip("/")) / "media") or None, data_dir=data)
        hosting.write_env_file(values)
        hi.write_status(done_steps=hi.read_status().get("done_steps", []) + ["settings"], step="finish")
        hosting.write_install_lock()  # from here on the installer is closed for good (also checked in code)
        hi.remove_key()
        hi.write_status(state="done", step="", done_steps=list(hi.STEPS), cron=hosting.cron_command(), site_url=job["site_url"], finished=time.time(), seen=False)
        self.stdout.write(cli_gettext("Installation complete."))
        sys.stdout.flush()
        # The browser that ran the installer is still talking to the old (setup-mode) process. Give it time to show the last page (the page marks
        # itself "seen"), then restart so the next request starts the installed site. A job started from a terminal restarts at once.
        if not sys.stdin.isatty():
            deadline = time.time() + 30
            while time.time() < deadline and not hi.read_status().get("seen"):
                time.sleep(0.5)
        hosting.restart_app()
