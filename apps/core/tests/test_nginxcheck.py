import io
import os
import tempfile
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from apps.core import nginxcheck, sitehealth

GOOD = "server {\n  location /_plugin_files/ {\n    internal;\n    alias /srv/ravix/plugin_data/;\n  }\n  location / { return 204; }\n}\n"
DIR = "/srv/ravix/plugin_data"


class StatusTests(SimpleTestCase):
    def status(self, text, prefix="/_plugin_files/"):
        return nginxcheck.plugin_files_status(prefix, DIR, text)

    def test_a_correct_block_is_ok_with_or_without_the_trailing_slash(self):
        self.assertEqual(self.status(GOOD), nginxcheck.OK)
        self.assertEqual(nginxcheck.plugin_files_status("/_plugin_files/", DIR + "/", GOOD), nginxcheck.OK)

    def test_no_block_is_missing(self):
        self.assertEqual(self.status("server { location / { return 204; } }"), nginxcheck.MISSING)

    def test_a_commented_out_block_does_not_count(self):
        self.assertEqual(self.status("server {\n# location /_plugin_files/ { internal; alias /srv/ravix/plugin_data/; }\n}"), nginxcheck.MISSING)

    def test_a_block_that_is_not_internal_is_reported(self):
        self.assertEqual(self.status(GOOD.replace("internal;", "")), nginxcheck.NOT_INTERNAL)

    def test_a_block_with_another_folder_is_reported(self):
        self.assertEqual(self.status(GOOD.replace("/srv/ravix/plugin_data/", "/srv/other/")), nginxcheck.WRONG_ALIAS)

    def test_no_text_or_no_prefix_is_unknown(self):
        self.assertEqual(self.status(None), nginxcheck.UNKNOWN)
        self.assertEqual(self.status(GOOD, prefix=""), nginxcheck.UNKNOWN)

    def test_config_files_are_read_from_the_given_patterns(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.conf").write_text(GOOD)
            self.assertIn("_plugin_files", nginxcheck.config_text([f"{tmp}/*.conf"]))
            self.assertIsNone(nginxcheck.config_text([f"{tmp}/none*"]))


@override_settings(RAVIX_ACCEL_REDIRECT_PREFIX="/_plugin_files/", RAVIX_PLUGIN_DATA_DIR=Path(DIR), RAVIX_CLI_LANG="en")
class SiteHealthAndPreflightTests(TestCase):
    def health(self, text):
        with mock.patch.object(nginxcheck, "config_text", return_value=text):
            return sitehealth.check_plugin_files()

    def test_site_health_is_good_when_the_location_is_in_place(self):
        self.assertEqual(self.health(GOOD)[0].status, sitehealth.GOOD)

    def test_site_health_is_critical_when_the_location_is_missing_or_open(self):
        self.assertEqual(self.health("server {}")[0].status, sitehealth.CRITICAL)
        self.assertEqual(self.health(GOOD.replace("internal;", ""))[0].status, sitehealth.CRITICAL)
        self.assertEqual(self.health(GOOD.replace("plugin_data", "x"))[0].status, sitehealth.CRITICAL)

    def test_site_health_does_not_guess_when_nginx_cannot_be_read(self):
        self.assertEqual(self.health(None)[0].status, sitehealth.UNKNOWN)

    @override_settings(RAVIX_ACCEL_REDIRECT_PREFIX="")
    def test_site_health_has_no_such_check_without_the_prefix(self):
        self.assertEqual(self.health(None), [])

    def preflight(self, text):
        out = io.StringIO()
        with mock.patch.object(nginxcheck, "config_text", return_value=text), mock.patch.dict(os.environ, {"RAVIX_CI": "1"}):
            try:
                call_command("ravix_preflight", "--ci", stdout=out, stderr=io.StringIO())
            except SystemExit:
                pass
        return out.getvalue()

    def test_preflight_warns_clearly_when_the_location_is_missing(self):
        text = self.preflight("server {}")
        self.assertIn("RAVIX_ACCEL_REDIRECT_PREFIX", text)
        self.assertIn("404", text)

    def test_preflight_is_quiet_when_nginx_cannot_be_read_and_happy_when_it_is_right(self):
        self.assertNotIn("X-Accel", self.preflight(None))
        self.assertIn("X-Accel-Redirect", self.preflight(GOOD))
        self.assertNotIn("answer 404", self.preflight(GOOD))


class DockerTests(SimpleTestCase):
    def test_docker_never_enables_accel_redirect_without_an_nginx_location(self):
        from django.conf import settings

        root = Path(settings.BASE_DIR)
        sets_prefix = any("RAVIX_ACCEL_REDIRECT_PREFIX" in p.read_text() for p in [*root.glob("docker-compose*.yml"), *root.glob("docker/*.sh"), root / "Dockerfile"])
        templates = "\n".join(p.read_text() for p in (root / "docker" / "nginx").glob("*"))
        if sets_prefix:
            self.assertIn("internal;", templates)
            self.assertIn("_plugin_files", templates)
