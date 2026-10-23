"""Operator-facing text (commands, start-up errors, preflight, logs): English by default, Persian only for RAVIX_CLI_LANG = fa (LANGUAGE_CODE is unrelated)."""
import os
import subprocess
import sys
import tempfile
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import translation

from apps.core import clilang
from apps.core.clilang import cli_gettext, cli_language

ROOT = Path(settings.BASE_DIR)
SLUG_ERROR_EN = "The slug is invalid or reserved"
SLUG_ERROR_FA = "نامک نامعتبر یا رزرو است"


class CliLanguageTests(SimpleTestCase):
    def setUp(self):
        clilang.reset_cache()
        self.addCleanup(clilang.reset_cache)

    def test_only_ravix_cli_lang_decides(self):
        with override_settings(LANGUAGE_CODE="fa", RAVIX_CLI_LANG=""):  # a Persian web interface does not make the CLI Persian
            self.assertEqual(cli_language(), "en")
            self.assertEqual(cli_gettext("The email address is not valid."), "The email address is not valid.")
        with override_settings(LANGUAGE_CODE="fa", RAVIX_CLI_LANG="en"):
            self.assertEqual(cli_gettext("The email address is not valid."), "The email address is not valid.")
        with override_settings(LANGUAGE_CODE="en", RAVIX_CLI_LANG="fa"):
            self.assertEqual(cli_gettext("The email address is not valid."), "نشانی ایمیل معتبر نیست.")

    def test_language_without_a_catalog_shows_english(self):
        for code in ("de", "pt-br", "zh-hans", "xx", ""):
            with override_settings(LANGUAGE_CODE="en", RAVIX_CLI_LANG=code):
                self.assertEqual(cli_gettext("The email address is not valid."), "The email address is not valid.", code)

    def test_regional_variants_of_persian(self):
        for code in ("fa", "fa-IR", "fa_IR", "FA"):
            with override_settings(RAVIX_CLI_LANG=code):
                self.assertTrue(cli_gettext("The email address is not valid.").startswith("نشانی"), code)

    def test_unknown_message_is_returned_unchanged(self):
        with override_settings(RAVIX_CLI_LANG="fa"):
            self.assertEqual(cli_gettext("Something not in the catalog"), "Something not in the catalog")

    def test_independent_of_the_request_language(self):
        """A log line must not change language with the visitor."""
        with override_settings(LANGUAGE_CODE="en", RAVIX_CLI_LANG=""), translation.override("fa"):
            self.assertEqual(cli_gettext("The email address is not valid."), "The email address is not valid.")

    def test_environment_is_read_before_settings_exist(self):
        with mock_env(RAVIX_CLI_LANG="fa"):
            self.assertEqual(clilang._read_env("RAVIX_CLI_LANG"), "fa")


class mock_env:
    def __init__(self, **values):
        self.values, self.old = values, {}

    def __enter__(self):
        for k, v in self.values.items():
            self.old[k] = os.environ.get(k)
            os.environ[k] = v

    def __exit__(self, *exc):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class CommandOutputTests(TestCase):
    def setUp(self):
        clilang.reset_cache()
        self.addCleanup(clilang.reset_cache)

    def error(self):
        with self.assertRaises(CommandError) as cm:
            call_command("startplugin", "ab", stdout=StringIO())
        return str(cm.exception)

    def test_english_by_default_persian_on_request(self):
        with override_settings(LANGUAGE_CODE="en", RAVIX_CLI_LANG=""):
            self.assertIn(SLUG_ERROR_EN, self.error())
        with override_settings(LANGUAGE_CODE="en", RAVIX_CLI_LANG="fa"):
            self.assertIn(SLUG_ERROR_FA, self.error())
        with override_settings(LANGUAGE_CODE="fa", RAVIX_CLI_LANG=""):  # LANGUAGE_CODE does not matter
            self.assertIn(SLUG_ERROR_EN, self.error())

    def test_preflight_report_language(self):
        def report():
            out = StringIO()
            try:
                call_command("ravix_preflight", stdout=out, stderr=StringIO())
            except (CommandError, SystemExit):
                pass
            return out.getvalue()

        with override_settings(LANGUAGE_CODE="en", RAVIX_CLI_LANG=""), translation.override("en"):
            self.assertIn("DEBUG", report())
            self.assertNotRegex(report(), "[؀-ۿ]")
        with override_settings(LANGUAGE_CODE="en", RAVIX_CLI_LANG="fa"), translation.override("fa"):
            self.assertRegex(report(), "[؀-ۿ]")


class StartPluginTemplateTests(SimpleTestCase):
    def test_generated_plugin_is_english_with_gettext(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(RAVIX_PLUGIN_DIRS=[tmp]):
            call_command("startplugin", "demo_gen", "--models", stdout=StringIO())
            root = Path(tmp) / "demo_gen"
            views = (root / "views.py").read_text(encoding="utf-8")
            template = (root / "templates" / "hello.html").read_text(encoding="utf-8")
            self.assertIn("from django.utils.translation import gettext", views)
            self.assertIn('gettext("Hello!")', views)
            self.assertIn("{% load i18n %}", template)
            self.assertIn("{% trans ", template)
            for path in root.rglob("*"):
                if path.is_file():
                    self.assertNotRegex(path.read_text(encoding="utf-8"), "[؀-ۿ]", path.name)
            from apps.plugins.manifest import read_manifest

            manifest = read_manifest(root, expected_slug="demo_gen")  # the skeleton is a valid plugin
            self.assertEqual(manifest.description, "Describe your plugin here.")


class SettingsErrorTests(SimpleTestCase):
    def run_settings(self, **env):
        base = {k: v for k, v in os.environ.items() if k not in ("RAVIX_CLI_LANG", "LANGUAGE_CODE")}
        base.update(DJANGO_SETTINGS_MODULE="config.settings.production", DJANGO_SECRET_KEY="short", DATABASE_URL="sqlite:///../../../tmp/x.db", **env)
        result = subprocess.run([sys.executable, "manage.py", "check"], cwd=ROOT, env=base, capture_output=True, text=True, timeout=120)
        return result.stderr

    def test_startup_error_is_english_by_default(self):
        err = self.run_settings()
        self.assertIn("DJANGO_SECRET_KEY must be set", err)

    def test_startup_error_in_persian(self):
        self.assertIn("DJANGO_SECRET_KEY باید تنظیم شده", self.run_settings(RAVIX_CLI_LANG="fa"))
        self.assertIn("DJANGO_SECRET_KEY must be set", self.run_settings(LANGUAGE_CODE="fa"))  # LANGUAGE_CODE is unrelated


class StartThemeTests(SimpleTestCase):
    def test_generated_theme_is_english_and_valid(self):
        from apps.themes.manifest import inspect_theme_dir

        with tempfile.TemporaryDirectory() as tmp, override_settings(RAVIX_THEME_DIRS=[tmp, ROOT / "themes"], LANGUAGE_CODE="en", RAVIX_CLI_LANG=""):
            clilang.reset_cache()
            out = StringIO()
            call_command("startheme", "demo-gen", stdout=out)
            self.assertIn("was created in", out.getvalue())
            info = inspect_theme_dir(Path(tmp) / "demo-gen")
            self.assertTrue(info.valid, info.errors)
            self.assertEqual(info.description, "A new theme based on the theme “default”; edit this description.")
            for path in (Path(tmp) / "demo-gen" / "templates").rglob("*.html"):
                self.assertNotRegex(path.read_text(encoding="utf-8"), "[؀-ۿ]", path.name)


class PreflightCiModeTests(TestCase):
    """--ci / RAVIX_CI=1: server-only checks (systemd, folders) are warnings; configuration and security checks stay errors."""

    def run_pf(self, *args, env=None, **over):
        import os
        from unittest import mock

        out = StringIO()
        base = dict(RAVIX_CLI_LANG="", LANGUAGE_CODE="en", RAVIX_PLUGIN_WEB_INSTALL=True,
                    RAVIX_PLUGIN_INSTALL_DIR="/nonexistent/ci-plugins", RAVIX_PLUGIN_BACKUP_DIR="/nonexistent/ci-backups")
        base.update(over)
        code = 0
        with override_settings(**base), translation.override("en"), mock.patch.dict(os.environ, env or {}), \
                mock.patch("apps.core.management.commands.ravix_preflight.systemd_unit_state", return_value=("not-found", "inactive")):
            try:
                call_command("ravix_preflight", *args, stdout=out, stderr=StringIO())
            except SystemExit as exc:
                code = exc.code
        return out.getvalue(), code

    def lines(self, out, mark):
        return [line for line in out.splitlines() if line.strip().startswith(mark)]

    def test_server_checks_are_errors_without_ci(self):
        out, code = self.run_pf()
        self.assertEqual(code, 1)
        errors = "\n".join(self.lines(out, "✘"))
        self.assertIn("is not active", errors)  # systemd timers
        self.assertIn("plugin install folder", errors)
        self.assertIn("plugin backup folder", errors)

    def test_ci_flag_and_env_turn_server_checks_into_warnings(self):
        for args, env in ((("--ci",), None), ((), {"RAVIX_CI": "1"})):
            out, _ = self.run_pf(*args, env=env)
            errors = "\n".join(self.lines(out, "✘"))
            warnings = "\n".join(self.lines(out, "!"))
            for text in ("is not active", "plugin install folder", "plugin backup folder", "STATIC_ROOT"):
                self.assertNotIn(text, errors)
            self.assertIn("is not active", warnings)
            self.assertIn("plugin install folder", warnings)
            self.assertIn("CI mode", warnings)

    def test_ci_mode_keeps_configuration_and_security_checks_strict(self):
        out, code = self.run_pf("--ci", DEBUG=True, SECRET_KEY="short", SESSION_COOKIE_SECURE=False)
        errors = "\n".join(self.lines(out, "✘"))
        self.assertEqual(code, 1)
        for text in ("DEBUG is on", "SECRET_KEY is weak", "Cookies are not Secure", "SECURE_SSL_REDIRECT is off"):
            self.assertIn(text, errors)

    def test_ci_warnings_vanish_when_everything_is_in_place(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            out, _ = self.run_pf("--ci", RAVIX_PLUGIN_INSTALL_DIR=d, RAVIX_PLUGIN_BACKUP_DIR=d)
        self.assertNotIn("plugin install folder", "\n".join(self.lines(out, "!")))
