"""The systemd units and install.sh must agree on who may read the code.

install.sh gives the bundled plugins to ``root:ravix`` with mode 750 (read-only for the service user). A unit that runs as ``ravix`` but
has ``Group=www-data`` and no ``SupplementaryGroups=ravix`` therefore cannot even list ``plugins/``: ``/panel/plugins/`` answered 500
(PermissionError) on a real server. These tests read both files and fail when they drift apart again.
"""
import re
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy"
INSTALL = (ROOT / "install.sh").read_text(encoding="utf-8")


def unit_settings(path: Path) -> dict[str, list[str]]:
    """``{key: [values...]}`` of the [Service] section (a key may repeat)."""
    out: dict[str, list[str]] = {}
    in_service = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_service = line == "[Service]"
            continue
        if not in_service or not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out.setdefault(key.strip(), []).append(value.strip())
    return out


def process_groups(settings: dict[str, list[str]]) -> set[str]:
    """The groups systemd gives the process: the primary group (``Group=``, else the user's own group) plus SupplementaryGroups=."""
    user = settings.get("User", ["root"])[-1]
    primary = settings.get("Group", [user])[-1]
    extra = {g for v in settings.get("SupplementaryGroups", []) for g in v.split()}
    return {primary, *extra}


def service_units() -> list[Path]:
    return sorted(DEPLOY.glob("ravix*.service"))


class CodeOwnershipInInstallScript(SimpleTestCase):
    def test_bundled_plugins_are_root_owned_group_readable_and_closed_to_others(self):
        # the model the units must satisfy: root:<app user>, group may read, others nothing
        self.assertRegex(INSTALL, r'chown -R root:"\$APP_USER" "\$APP_DIR/plugins"')
        self.assertRegex(INSTALL, r'chmod -R g-w,o-rwx "\$APP_DIR/plugins"')
        self.assertRegex(INSTALL, r"(?m)^APP_USER=ravix$")

    def test_www_data_joins_the_app_group_not_the_other_way_round(self):
        self.assertRegex(INSTALL, r'usermod -aG "\$APP_USER" www-data')
        self.assertNotRegex(INSTALL, r"usermod -aG www-data")


class UnitGroupsMatchInstallScript(SimpleTestCase):
    def test_there_are_service_units(self):
        self.assertGreaterEqual(len(service_units()), 4)

    def test_every_installed_unit_exists_and_every_unit_is_installed(self):
        match = re.search(r"UNITS=\(([^)]*)\)", INSTALL)
        self.assertIsNotNone(match)
        installed = set(match.group(1).split())
        on_disk = {p.name for p in DEPLOY.glob("ravix*.service")} | {p.name for p in DEPLOY.glob("ravix*.timer")} | {p.name for p in DEPLOY.glob("ravix*.path")}
        self.assertEqual(installed, on_disk)

    def test_every_service_run_as_the_app_user_can_read_the_code(self):
        for path in service_units():
            settings = unit_settings(path)
            if settings.get("User", [""])[-1] != "ravix":
                continue
            self.assertIn("ravix", process_groups(settings),
                          f"{path.name} runs as ravix but its groups {sorted(process_groups(settings))} do not include ravix: "
                          "it cannot list the root:ravix 750 plugins folder (add SupplementaryGroups=ravix)")

    def test_a_different_primary_group_always_comes_with_the_supplementary_group(self):
        for path in service_units():
            settings = unit_settings(path)
            if settings.get("Group", ["ravix"])[-1] != "ravix":
                self.assertIn("ravix", " ".join(settings.get("SupplementaryGroups", [])).split(), path.name)

    def test_the_web_service_keeps_www_data_for_the_socket_and_gets_ravix_for_the_code(self):
        settings = unit_settings(DEPLOY / "ravix.service")
        self.assertEqual(settings["Group"][-1], "www-data")  # nginx reaches the socket through this group
        self.assertEqual(process_groups(settings), {"www-data", "ravix"})

    def test_the_timer_services_use_the_same_groups_as_the_web_service(self):
        web = process_groups(unit_settings(DEPLOY / "ravix.service"))
        for name in ("ravix-backup.service", "ravix-publish-scheduled.service", "ravix-expire-orders.service"):
            self.assertIn("ravix", process_groups(unit_settings(DEPLOY / name)), name)
        for name in ("ravix-backup.service", "ravix-publish-scheduled.service"):
            self.assertEqual(process_groups(unit_settings(DEPLOY / name)), web, name)

    def test_no_unit_gives_the_service_write_access_to_the_code(self):
        for path in service_units():
            for value in unit_settings(path).get("ReadWritePaths", []):
                self.assertNotRegex(value, r"/srv/ravix/app(/|\s|$)", path.name)


class UpdateExecutorUnits(SimpleTestCase):
    """The update executor is a separate user; only a fixed, content-free request reaches root."""

    def test_the_executor_runs_as_the_deploy_user_in_the_app_group(self):
        s = unit_settings(DEPLOY / "ravix-update.service")
        self.assertEqual(s["User"], ["ravix-deploy"])
        self.assertIn("ravix", process_groups(s))
        self.assertEqual(s["Type"], ["oneshot"])
        self.assertEqual(s["ExecStart"], ["/srv/ravix/venv/bin/python manage.py ravix_update_exec"])  # through the symlink an update switches
        self.assertIn("/etc/ravix/ravix.env", s["EnvironmentFile"])
        self.assertEqual(s["NoNewPrivileges"], ["true"])

    def test_the_executor_can_reach_the_web_socket_it_health_checks(self):
        """Gunicorn's socket is group-owned by the web service's Group with umask 007 (mode 660): the executor, which health-checks through it after a
        restart, must be in that group - otherwise every update ends with "PermissionError" on /healthz and a failed rollback."""
        web = unit_settings(DEPLOY / "ravix.service")
        socket_group = web["Group"][-1]
        self.assertIn(socket_group, process_groups(unit_settings(DEPLOY / "ravix-update.service")))
        self.assertIn("umask = 0o007", (DEPLOY / "gunicorn.conf.py").read_text())  # the premise: the socket is not world-accessible

    def test_the_web_service_starts_from_the_same_symlink(self):
        self.assertIn("/srv/ravix/venv/bin/gunicorn", unit_settings(DEPLOY / "ravix.service")["ExecStart"][0])
        self.assertEqual(unit_settings(DEPLOY / "ravix.service")["User"], ["ravix"])  # the web service is not the deploy user

    def test_the_restart_unit_runs_one_fixed_command_as_root(self):
        s = unit_settings(DEPLOY / "ravix-restart.service")
        self.assertEqual(s["ExecStart"], ["/bin/systemctl restart ravix.service"])
        self.assertNotIn("User", s)  # root, so it may restart the service
        self.assertNotIn("EnvironmentFile", s)  # nothing from the application's environment, and nothing from the request file, reaches it
        self.assertEqual(s["ExecStopPost"], ["/bin/rm -f -- /srv/ravix/jobs/restart-request"])  # even a failed restart is not retried in a loop

    def test_the_path_units_watch_exactly_the_files_the_application_writes(self):
        from apps.updater import jobs, lifecycle

        text = (DEPLOY / "ravix-update.path").read_text(encoding="utf-8")
        self.assertIn(f"PathExistsGlob=/srv/ravix/jobs/{jobs.QUEUE_NAME}/*", text)
        self.assertIn("Unit=ravix-update.service", text)
        restart = (DEPLOY / "ravix-restart.path").read_text(encoding="utf-8")
        self.assertIn(f"PathExists=/srv/ravix/jobs/{lifecycle.REQUEST_NAME}", restart)
        self.assertIn("Unit=ravix-restart.service", restart)

    def test_root_never_runs_anything_from_a_folder_the_deploy_user_writes(self):
        for name in ("ravix-restart.service", "ravix-restart.path"):
            text = (DEPLOY / name).read_text(encoding="utf-8")
            self.assertNotIn("/srv/ravix/app", text)  # the code folder belongs to ravix-deploy: a root unit must not execute it
            self.assertNotIn("/srv/ravix/venv", text)

    def test_install_script_gives_the_code_to_the_deploy_user_and_only_the_queue_to_the_web_user(self):
        self.assertRegex(INSTALL, r'chown -R "\$DEPLOY_USER:\$APP_USER" "\$APP_DIR"')
        self.assertRegex(INSTALL, r'chmod -R u\+rwX,g\+rX,g-w,o-rwx "\$APP_DIR"')
        self.assertRegex(INSTALL, r'chown "\$DEPLOY_USER:\$APP_USER" "\$BASE"; chmod 750 "\$BASE"')  # the web user cannot repoint the venv symlink
        self.assertRegex(INSTALL, r'chown "\$DEPLOY_USER:\$APP_USER" "\$JOBS_DIR/queue"; chmod 2770 "\$JOBS_DIR/queue"')
        self.assertRegex(INSTALL, r'chown "\$DEPLOY_USER:\$APP_USER" "\$JOBS_DIR"; chmod 2750 "\$JOBS_DIR"')
        self.assertRegex(INSTALL, r"(?m)^DEPLOY_USER=ravix-deploy")
        self.assertRegex(INSTALL, r'adduser --system --ingroup "\$APP_USER" --no-create-home')

    def test_install_script_writes_the_executor_settings_only_when_missing_and_only_with_systemd(self):
        for key in ("RAVIX_VENV_LINK", "RAVIX_UPDATE_JOBS_DIR", "RAVIX_UPDATE_EXECUTOR systemd", "RAVIX_UPDATE_RESTART systemd"):
            self.assertIn(f"env_set {key}", INSTALL)
        self.assertRegex(INSTALL, r"if have_systemd; then env_set RAVIX_UPDATE_EXECUTOR systemd")
        self.assertRegex(INSTALL, r'\[ -d "\$APP_DIR/update_backups" \] \|\| env_set RAVIX_UPDATE_BACKUP_DIR')  # an older install keeps its backups where its runs point

    def test_the_executor_units_are_installed_and_enabled(self):
        for unit in ("ravix-update.service", "ravix-update.path", "ravix-update.timer", "ravix-restart.service", "ravix-restart.path"):
            self.assertIn(unit, re.search(r"UNITS=\(([^)]*)\)", INSTALL).group(1))
        self.assertRegex(INSTALL, r"systemctl enable --now ravix\.service .*ravix-update\.path ravix-update\.timer ravix-restart\.path")


class SystemdSyntaxTests(SimpleTestCase):
    """The units are parsed by the real ``systemd-analyze verify`` (present on the CI runners and on every Ubuntu server)."""

    def test_every_unit_parses(self):
        import shutil
        import subprocess

        if not shutil.which("systemd-analyze"):
            self.fail("systemd-analyze is required to check the units (it ships with systemd on Ubuntu)")
        names = sorted(p.name for pattern in ("ravix*.service", "ravix*.timer", "ravix*.path") for p in DEPLOY.glob(pattern))
        self.assertGreaterEqual(len(names), 12)
        r = subprocess.run(["systemd-analyze", "verify", *names], cwd=DEPLOY, capture_output=True, text=True, timeout=60)
        # Only real syntax problems count: the binaries (/srv/ravix/venv/...) do not exist on the machine that runs the tests, and newer systemd
        # versions add notes of their own about old-but-valid settings.
        bad = ("unknown key", "unknown section", "unknown lvalue", "invalid", "failed to parse", "assignment outside of section", "missing", "unit has no", "bad unit")
        problems = [line for line in (r.stdout + r.stderr).splitlines() if any(word in line.lower() for word in bad) and "is not executable" not in line]
        self.assertEqual(problems, [])
