"""A code folder (plugins/, themes/) that the running process cannot list must be reported, never turn into a 500."""
import os
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.accounts.tests.test_auth import STRONG, make_user  # noqa: F401
from apps.core import clilang, fsaccess, sitehealth
from apps.plugins import discovery

real_scandir = os.scandir
real_iterdir = Path.iterdir


class Denied:
    """Makes listing ``root`` (and only it) fail the way root:ravix 750 does for a process without the group."""

    def __init__(self, root):
        self.root = str(root)

    def scandir(self, path=".", *a, **kw):
        if str(path) == self.root:
            raise PermissionError(13, "Permission denied", str(path))
        return real_scandir(path, *a, **kw)

    def iterdir(self_path):  # noqa: N805 - patched onto Path
        raise PermissionError(13, "Permission denied", str(self_path))

    def __enter__(self):
        denied = self
        self._patches = [mock.patch("os.scandir", self.scandir),
                         mock.patch.object(Path, "iterdir", lambda p: (_ for _ in ()).throw(PermissionError(13, "Permission denied", str(p))) if str(p) == denied.root else real_iterdir(p))]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


@override_settings(RAVIX_CLI_LANG="en")
class FsAccessTests(SimpleTestCase):
    def setUp(self):
        clilang.reset_cache()
        self.addCleanup(clilang.reset_cache)

    def test_a_listable_folder_has_no_error(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(fsaccess.list_error(d), "")

    def test_missing_and_denied_are_told_apart(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(fsaccess.list_error(Path(d) / "nope"), "missing")
            with Denied(d):
                self.assertEqual(fsaccess.list_error(d), "denied")

    def test_the_message_names_the_process_groups_the_owner_and_the_fix(self):
        with tempfile.TemporaryDirectory() as d:
            text = fsaccess.unreadable_detail(d)
        self.assertIn("SupplementaryGroups=ravix", text)
        self.assertIn("groups:", text)
        self.assertIn(d, text)


class PluginDiscoveryTests(TestCase):
    def test_unreadable_plugin_folder_is_a_warning_not_an_exception(self):
        with tempfile.TemporaryDirectory() as d, override_settings(RAVIX_PLUGIN_DIRS=[Path(d)]), Denied(d):
            found, warnings = discovery.scan()
            self.assertEqual(found, [])
            self.assertEqual(len(warnings), 1)
            self.assertIn(d, warnings[0])
            self.assertIn("SupplementaryGroups=ravix", warnings[0])
            self.assertEqual(discovery.discover(), [])  # the old entry point keeps working

    def test_other_folders_are_still_listed(self):
        with tempfile.TemporaryDirectory() as bad, tempfile.TemporaryDirectory() as good:
            (Path(good) / "demo").mkdir()
            with override_settings(RAVIX_PLUGIN_DIRS=[Path(bad), Path(good)]), Denied(bad):
                found, warnings = discovery.scan()
        self.assertEqual([f.slug for f in found], ["demo"])
        self.assertEqual(len(warnings), 1)

    def test_plugin_list_page_shows_the_warning_with_status_200(self):
        admin = make_user("perm-admin", role="super_admin")
        self.client.force_login(admin)
        with tempfile.TemporaryDirectory() as d, override_settings(RAVIX_PLUGIN_DIRS=[Path(d)]), Denied(d):
            response = self.client.get(reverse("plugins:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SupplementaryGroups=ravix")  # the command to run is not translated
        self.assertContains(response, d)

    def test_unreadable_theme_folder_does_not_break_theme_discovery(self):
        from apps.themes import registry

        with tempfile.TemporaryDirectory() as d, override_settings(RAVIX_THEME_DIRS=[Path(d)]), Denied(d):
            self.assertEqual(registry._discover(), {})


class SiteHealthCodeDirsTests(TestCase):
    def test_good_when_readable(self):
        with tempfile.TemporaryDirectory() as d, override_settings(RAVIX_PLUGIN_DIRS=[Path(d)], RAVIX_THEME_DIRS=[Path(d)]):
            (check,) = sitehealth.check_code_dirs()
        self.assertEqual(check.status, sitehealth.GOOD)

    def test_critical_and_names_the_folder_when_not_listable(self):
        with tempfile.TemporaryDirectory() as d, override_settings(RAVIX_PLUGIN_DIRS=[Path(d)], RAVIX_THEME_DIRS=[]), Denied(d):
            (check,) = sitehealth.check_code_dirs()
        self.assertEqual(check.status, sitehealth.CRITICAL)
        self.assertIn(d, check.detail)
        self.assertIn("SupplementaryGroups=ravix", check.fix)

    def test_a_missing_optional_folder_is_not_a_problem(self):
        with tempfile.TemporaryDirectory() as d, override_settings(RAVIX_PLUGIN_DIRS=[Path(d) / "absent"], RAVIX_THEME_DIRS=[]):
            (check,) = sitehealth.check_code_dirs()
        self.assertEqual(check.status, sitehealth.GOOD)

    def test_the_check_is_part_of_the_health_page(self):
        with tempfile.TemporaryDirectory() as d, override_settings(RAVIX_PLUGIN_DIRS=[Path(d)]), Denied(d):
            keys = [c.key for c in sitehealth.run_all()]
        self.assertIn("code_dirs", keys)


@override_settings(RAVIX_CLI_LANG="en")
class PreflightCodeDirsTests(TestCase):
    def setUp(self):
        clilang.reset_cache()
        self.addCleanup(clilang.reset_cache)

    def run_preflight(self, **kw):
        out = StringIO()
        try:
            call_command("ravix_preflight", stdout=out, **kw)
        except SystemExit:
            pass
        return out.getvalue()

    def test_preflight_fails_for_a_folder_the_process_cannot_list(self):
        with tempfile.TemporaryDirectory() as d, override_settings(RAVIX_PLUGIN_DIRS=[Path(d)], RAVIX_PLUGIN_WEB_INSTALL=False), Denied(d):
            text = self.run_preflight()
        self.assertIn(f"Cannot list {d}", text)
        self.assertIn("SupplementaryGroups=ravix", text)

    def test_preflight_confirms_a_readable_folder(self):
        with tempfile.TemporaryDirectory() as d, override_settings(RAVIX_PLUGIN_DIRS=[Path(d)], RAVIX_PLUGIN_WEB_INSTALL=False):
            text = self.run_preflight()
        self.assertIn(f"The code folder {d} is readable and listable by this process", text)
