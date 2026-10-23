"""به‌روزرسانی یکپارچهٔ Ravix از خط فرمان (همیشه در دسترس؛ مستقل از RAVIX_UPDATE_WEB_APPLY)."""
from apps.core.clilang import cli_gettext
from django.core.management.base import BaseCommand, CommandError

from apps.updater import pipeline, sources, versions
from apps.updater.errors import UpdateError
from apps.updater.models import UpdateRun


class Command(BaseCommand):
    help = "status | check | apply core|theme|plugin | rollback --run ID | history"

    def add_arguments(self, p):
        p.add_argument("action", choices=["status", "check", "apply", "rollback", "history"])
        p.add_argument("target", nargs="?", choices=["core", "theme", "plugin"])
        p.add_argument("--slug", default="")
        p.add_argument("--to", dest="target_version", default="", help=cli_gettext("Specific version (default: the latest compatible version)"))
        p.add_argument("--run", type=int, default=0)
        p.add_argument("--dry-run", action="store_true", help=cli_gettext("Fetch and validate completely without changing files"))
        p.add_argument("--yes-data-loss", action="store_true", help=cli_gettext("Confirm that the rollback destroys data created after the upgrade"))
        p.add_argument("--user", default="", help=cli_gettext("Administrator username/email (required for plugins)"))

    def _user(self, ident):
        if not ident:
            return None
        from django.contrib.auth import get_user_model

        U = get_user_model()
        u = U.objects.filter(email=ident).first() or U.objects.filter(username=ident).first()
        if u is None:
            raise CommandError(cli_gettext("User not found."))
        return u

    def handle(self, *a, **o):
        act = o["action"]
        try:
            if act == "status":
                v = versions.component_versions()
                self.stdout.write(cli_gettext("Running core: %(a1)s | on disk: %(a2)s") % {"a1": v['core']['running'], "a2": v['core']['disk']})
                if v["core"]["restart_pending"]:
                    self.stdout.write(self.style.WARNING(cli_gettext("The code on disk is newer than the running process; restart the service.")))
                lock = pipeline.lock_state()
                if lock:
                    self.stdout.write(self.style.WARNING(cli_gettext("Active lock: %(a1)s since %(a2)s") % {"a1": lock['operation'], "a2": lock['since']}))
                pipeline.mark_interrupted()
                for r in UpdateRun.objects.filter(status="needs_recovery")[:5]:
                    self.stdout.write(self.style.ERROR(cli_gettext("Needs recovery: run #%(a1)s\n%(a2)s") % {"a1": r.pk, "a2": r.recovery}))
            elif act == "check":
                if not sources.has_source():
                    raise CommandError(cli_gettext("No update source is configured (RAVIX_UPDATE_URL or RAVIX_UPDATE_DIR)."))
                plan = pipeline.plan_core(sources.cached_index(force=True))
                if not plan["latest"]:
                    self.stdout.write(cli_gettext("Up to date (%(a1)s).") % {"a1": plan['current']})
                else:
                    self.stdout.write(cli_gettext("New version: %(a1)s (current %(a2)s)") % {"a1": plan['latest']['version'], "a2": plan['current']})
                    for p in plan["problems"]:
                        self.stdout.write(self.style.ERROR(cli_gettext("Blocker: ") + p))
                    for w in plan["warnings"]:
                        self.stdout.write(self.style.WARNING(cli_gettext("Warning: ") + w))
            elif act == "history":
                for r in UpdateRun.objects.all()[:20]:
                    self.stdout.write(f"#{r.pk} {r.started_at:%Y-%m-%d %H:%M} {r.component}:{r.slug or '-'} {r.from_version}→{r.to_version} [{r.status}]")
            elif act == "apply":
                if not o["target"]:
                    raise CommandError(cli_gettext("Specify the target: core | theme | plugin"))
                ver = o["target_version"] or None
                if o["target"] == "core":
                    run = pipeline.apply_core(ver, self._user(o["user"]), dry_run=o["dry_run"])
                elif o["target"] == "theme":
                    if not o["slug"]:
                        raise CommandError(cli_gettext("--slug is required."))
                    run = pipeline.apply_theme(o["slug"], ver, self._user(o["user"]), dry_run=o["dry_run"])
                else:
                    if not (o["slug"] and o["user"]):
                        raise CommandError(cli_gettext("--slug and --user (administrator) are required."))
                    run = pipeline.apply_plugin(o["slug"], ver, self._user(o["user"]))
                self.stdout.write(self.style.SUCCESS(cli_gettext("Run #%(a1)s: %(a2)s — %(a3)s → %(a4)s") % {"a1": run.pk, "a2": run.get_status_display(), "a3": run.from_version, "a4": run.to_version}))
                self.stdout.write(run.recovery)
            elif act == "rollback":
                if not o["run"]:
                    raise CommandError(cli_gettext("--run is required."))
                run = pipeline.rollback_run(o["run"], self._user(o["user"]), confirm_data_loss=o["yes_data_loss"])
                self.stdout.write(self.style.SUCCESS(cli_gettext("Rollback done (run #%(a1)s). %(a2)s") % {"a1": run.pk, "a2": run.recovery}))
        except UpdateError as exc:
            raise CommandError(str(exc))
