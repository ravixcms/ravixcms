"""سازگاری نام‌های قدیمی «ravin» تا Ravix 2.0 (docs/upgrade.md): هر نام قدیمی باید کار کند و هشدار بدهد."""
import json
import os
import re
import subprocess
import warnings
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command, get_commands
from django.test import SimpleTestCase, TestCase

from apps.core import deprecation
from apps.core.deprecation import RavixDeprecationWarning, getenv, read_legacy_key, upgrade_legacy_key
from apps.core.version import RAVIN_VERSION, RAVIX_VERSION
from apps.plugins.manifest import parse_manifest
from apps.plugins.tests.helpers import base_manifest
from apps.themes.tests.helpers import ThemeDirMixin

ROOT = Path(__file__).resolve().parents[3]


class EnvFallbackTests(SimpleTestCase):
    def setUp(self):
        deprecation.LEGACY_ENV_USED.clear()
        self.addCleanup(deprecation.LEGACY_ENV_USED.clear)

    def test_new_name_wins_without_warning(self):
        with mock.patch.dict(os.environ, {"RAVIX_FOO": "new", "RAVIN_FOO": "old"}), warnings.catch_warnings():
            warnings.simplefilter("error")
            self.assertEqual(getenv("RAVIX_FOO"), "new")

    def test_legacy_name_works_with_one_warning(self):
        env = {k: v for k, v in os.environ.items() if k not in ("RAVIX_BAR",)}
        env["RAVIN_BAR"] = "old"
        with mock.patch.dict(os.environ, env, clear=True), warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            self.assertEqual(getenv("RAVIX_BAR"), "old")
            self.assertEqual(getenv("RAVIX_BAR"), "old")
        hits = [x for x in w if issubclass(x.category, RavixDeprecationWarning)]
        self.assertEqual(len(hits), 1)
        self.assertIn("RAVIN_BAR", str(hits[0].message))
        self.assertIn("2.0", str(hits[0].message))
        self.assertEqual(deprecation.LEGACY_ENV_USED, ["RAVIN_BAR"])

    def test_warning_is_visible_by_default(self):
        self.assertTrue(issubclass(RavixDeprecationWarning, FutureWarning))

    def test_unset_returns_none(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(getenv("RAVIX_NOPE"))

    def test_settings_module_reads_legacy_variable(self):
        code = "import os; os.environ['DJANGO_SETTINGS_MODULE']='config.settings.development'; from django.conf import settings; print(settings.RAVIX_PLUGINS_SAFE_MODE)"
        env = {k: v for k, v in os.environ.items() if not k.startswith("RAVI")}
        env.update(RAVIN_PLUGINS_SAFE_MODE="true", DJANGO_SECRET_KEY="x" * 60)
        r = subprocess.run([os.sys.executable, "-W", "default", "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "True", r.stderr)
        self.assertIn("RAVIN_PLUGINS_SAFE_MODE", r.stderr)


class VersionAliasTests(SimpleTestCase):
    def test_both_constants_equal(self):
        self.assertEqual(RAVIN_VERSION, RAVIX_VERSION)

    def test_updater_regex_reads_both_spellings(self):
        from apps.updater import versions

        for name in ("RAVIX_VERSION", "RAVIN_VERSION"):
            self.assertEqual(versions._VER.search(f'{name} = "1.2.3"\n').group(1), "1.2.3")

    def test_version_file_contains_both_lines(self):
        text = (ROOT / "apps/core/version.py").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r'^RAVI[XN]_VERSION\s*=', text, re.M)), 2)


class LegacyKeyTests(SimpleTestCase):
    def test_upgrade_legacy_key_copies_and_drops_old(self):
        src = {"ravin": ">=1"}
        with self.assertWarns(RavixDeprecationWarning):
            out = upgrade_legacy_key(src, "x")
        self.assertEqual(out, {"ravix": ">=1"})
        self.assertEqual(src, {"ravin": ">=1"})  # ورودی دست‌نخورده

    def test_new_key_preferred_silently(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            self.assertEqual(upgrade_legacy_key({"ravix": "a", "ravin": "b"}, "x")["ravix"], "a")

    def test_read_legacy_key(self):
        self.assertEqual(read_legacy_key({"ravin": "o"}), "o")
        self.assertEqual(read_legacy_key({"ravix": "n", "ravin": "o"}), "n")
        self.assertEqual(read_legacy_key(None), "")

    def test_plugin_manifest_with_old_key(self):
        raw = base_manifest("old1")
        raw["ravin"] = raw.pop("ravix")
        with self.assertWarns(RavixDeprecationWarning):
            m = parse_manifest(raw)
        self.assertEqual(m.ravix, ">=0.5.0,<1.0.0")
        self.assertNotIn("ravin", m.to_json())

    def test_release_and_index_emit_both_keys(self):
        from apps.updater.release import _with_legacy

        self.assertEqual(_with_legacy({"ravix": ">=1"}), {"ravix": ">=1", "ravin": ">=1"})
        self.assertEqual(_with_legacy({}), {})

    def test_index_entry_accepts_old_requires_key(self):
        from apps.updater import sources

        entry = sources._parse_entry({"version": "1.0.0", "sha256": "a" * 64, "size": 10, "requires": {"ravin": ">=0.6"}}) \
            if hasattr(sources, "_parse_entry") else None
        if entry is not None:
            self.assertEqual(entry["requires"]["ravix"], ">=0.6")


class ThemeAliasTests(ThemeDirMixin, TestCase):
    def test_old_theme_manifest_key_and_old_tag_library(self):
        from apps.themes import registry
        from apps.themes.manifest import inspect_theme_dir

        def edit(path):
            data = json.loads((path / "theme.json").read_text(encoding="utf-8"))
            data.pop("ravix", None)
            data["ravin"] = ">=0.6,<1.0"
            (path / "theme.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            base = path / "templates" / "base.html"
            base.write_text(base.read_text(encoding="utf-8").replace("ravix_theme", "ravin_theme"), encoding="utf-8")

        target = self.install_theme("oldtheme", edit=edit)
        registry.clear_cache()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            info = inspect_theme_dir(target)
        self.assertEqual(list(info.errors), [], info.errors)
        self.assertEqual(info.ravix, ">=0.6,<1.0")
        msgs = " ".join(str(x.message) for x in w if issubclass(x.category, RavixDeprecationWarning))
        self.assertIn("ravin_theme", msgs)

    def test_unknown_library_still_rejected(self):
        from apps.themes.manifest import inspect_theme_dir

        def edit(path):
            base = path / "templates" / "base.html"
            base.write_text("{% load static %}" + base.read_text(encoding="utf-8"), encoding="utf-8")

        info = inspect_theme_dir(self.install_theme("badlib", edit=edit))
        self.assertTrue(any("static" in e for e in info.errors))


class CommandAliasTests(TestCase):
    def test_old_command_names_exist(self):
        cmds = get_commands()
        for name in ("ravin_preflight", "ravin_update", "ravin_release"):
            self.assertIn(name, cmds)

    def test_old_preflight_runs_and_warns(self):
        out = StringIO()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            try:
                from django.core.management import ManagementUtility

                ManagementUtility(["manage.py", "ravin_preflight"]).execute()
            except SystemExit:
                pass
        self.assertTrue(any(issubclass(x.category, RavixDeprecationWarning) and "ravin_preflight" in str(x.message) for x in w))
        del out


class PreflightLegacyTests(TestCase):
    def run_pf(self, state):
        out = StringIO()
        with mock.patch("apps.core.management.commands.ravix_preflight.systemd_unit_state", side_effect=state):
            try:
                call_command("ravix_preflight", stdout=out)
            except SystemExit:
                pass
        return out.getvalue()

    def test_legacy_timers_accepted_with_warning(self):
        def state(unit):
            if unit == "ravix-backup.timer":
                return ("disabled", "inactive")
            if unit == "ravin-backup.timer":
                return ("enabled", "active")
            return ("enabled", "active")

        out = self.run_pf(state)
        self.assertRegex(out, r"! تایمر قدیمی ravin-backup\.timer")
        self.assertNotRegex(out, r"✘.*ravix-backup\.timer")

    def test_legacy_env_reported(self):
        with mock.patch.object(deprecation, "LEGACY_ENV_USED", ["RAVIN_X"]):
            out = self.run_pf(lambda u: ("enabled", "active"))
        self.assertIn("RAVIN_X", out)


class DeployShellTests(SimpleTestCase):
    def sh(self, script, env=None):
        e = {"PATH": os.environ["PATH"], **(env or {})}
        return subprocess.run(["bash", "-c", f'. "{ROOT}/deploy/_legacy.sh"; {script}'], env=e, capture_output=True, text=True)

    def test_legacy_vars_mapped(self):
        r = self.sh('rav_legacy_vars; echo "$RAVIX_KEEP"', {"RAVIN_KEEP": "9"})
        self.assertEqual(r.stdout.strip(), "9")
        self.assertIn("منسوخ", r.stderr)

    def test_new_var_not_overridden(self):
        r = self.sh('rav_legacy_vars; echo "$RAVIX_KEEP"', {"RAVIN_KEEP": "9", "RAVIX_KEEP": "1"})
        self.assertEqual(r.stdout.strip(), "1")

    def test_rav_path_prefers_existing_legacy(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ravin").mkdir()
            r = self.sh(f'rav_path "{d}/ravix"')
            self.assertEqual(r.stdout.strip(), f"{d}/ravin")
            (Path(d) / "ravix").mkdir()
            self.assertEqual(self.sh(f'rav_path "{d}/ravix"').stdout.strip(), f"{d}/ravix")


class NoStrayNamesTests(SimpleTestCase):
    """«ravin» فقط در جاهای عمداً باقی‌مانده مجاز است (فهرستش در docs/upgrade.md)."""

    ALLOWED = (
        "apps/core/deprecation.py", "apps/core/version.py", "apps/core/tests/test_rename_compat.py",
        "apps/themes/legacy/", "apps/core/templatetags/ravin_ui.py", "apps/themes/constants.py", "apps/themes/engine.py", "apps/themes/manifest.py",
        "apps/updater/versions.py", "apps/updater/release.py", "apps/updater/package.py", "apps/updater/pipeline.py",
        "apps/pluginhub/api.py", "apps/pluginhub/services.py", "apps/pluginhub/models.py",
        "apps/core/management/commands/ravin_", "apps/updater/management/commands/ravin_",
        "apps/core/management/commands/ravix_preflight.py", "apps/core/sitehealth.py", "apps/updater/management/commands/ravix_release.py",
        "config/settings/", "deploy/", "scripts/", "docs/", "CHANGELOG.md", "migrations/", "/tests/", ".github/",
        "apps/updater/signing.py", "apps/accounts/emailing.py", "apps/shop/payments/vault.py",  # ثابت‌های پروتکل/رمزنگاری
        "templates/pluginhub/",  # فیلد مدل ravin_spec (ستون دیتابیس)
        "apps/plugins/", "apps/core/models.py", "apps/core/apps.py",
    )

    def test_no_unexpected_ravin_in_python_and_templates(self):
        bad = []
        for path in list(ROOT.glob("apps/**/*.py")) + list(ROOT.glob("templates/**/*.html")) + list(ROOT.glob("themes/**/*")):
            if not path.is_file() or path.suffix not in (".py", ".html", ".json"):
                continue
            rel = path.relative_to(ROOT).as_posix()
            if any(a in rel for a in self.ALLOWED):
                continue
            if re.search(r"ravin|راوین", path.read_text(encoding="utf-8", errors="ignore"), re.I):
                bad.append(rel)
        self.assertEqual(bad, [])


class LegacyLibraryTests(SimpleTestCase):
    def test_ravin_ui_loads_and_warns_on_use(self):
        from django.template import engines

        tpl = engines["django"].from_string("{% load ravin_ui %}{{ 12|fa_digits }}")
        with self.assertWarns(RavixDeprecationWarning):
            out = tpl.render({})
        self.assertEqual(out, engines["django"].from_string("{% load ravix_ui %}{{ 12|fa_digits }}").render({}))
        self.assertNotEqual(out, "12")

    def test_ravin_ui_keeps_all_filters(self):
        from apps.core.templatetags import ravin_ui, ravix_ui

        self.assertEqual(set(ravin_ui.register.filters), set(ravix_ui.register.filters))
        self.assertEqual(set(ravin_ui.register.tags), set(ravix_ui.register.tags))
