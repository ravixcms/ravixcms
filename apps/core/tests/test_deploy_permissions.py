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
        on_disk = {p.name for p in DEPLOY.glob("ravix*.service")} | {p.name for p in DEPLOY.glob("ravix*.timer")}
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
