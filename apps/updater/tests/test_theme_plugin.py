"""ارتقای قالب (اعتبارسنجی، پشتیبان، بازگشت خودکار) و واگذاری ارتقای افزونه به نصب‌کنندهٔ موجود."""
import json
import shutil
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import override_settings

from apps.themes import registry
from apps.updater import pipeline, release
from apps.updater.errors import UpdateBlocked, UpdateBusy, UpdateError
from apps.updater.models import UpdateLock, UpdateRun

from .helpers import SEED, UpdaterCase


class ThemeUpgrade(UpdaterCase):
    def setUp(self):
        super().setUp()
        self.themes = self.base / "themes"
        self.themes.mkdir()
        self.make_theme("0.1.0")
        ov = override_settings(RAVIN_THEME_DIRS=[Path(settings.BASE_DIR) / "themes", self.themes])
        ov.enable()
        self.addCleanup(ov.disable)
        registry.clear_cache()
        self.addCleanup(registry.clear_cache)

    def make_theme(self, version, where=None, ravin=""):
        d = (where or self.themes) / "mytheme"
        if d.exists():
            shutil.rmtree(d)
        shutil.copytree(Path(settings.BASE_DIR) / "themes/default", d, ignore=shutil.ignore_patterns("__pycache__"))
        m = json.loads((d / "theme.json").read_text())
        m.update(slug="mytheme", name="My", version=version)
        if ravin:
            m["ravin"] = ravin
        (d / "theme.json").write_text(json.dumps(m))
        (d / "static/marker.txt").write_text(version)
        return d

    def publish_theme(self, version, *, seed=SEED, ravin=""):
        (self.base / f"src-{version}").mkdir(exist_ok=True)
        src = self.make_theme(version, self.base / f"src-{version}", ravin)
        slug, entry = release.build_theme(src, self.dist, seed=seed)
        release.write_index(self.dist, themes={slug: [entry]})
        return entry

    def apply(self, version=None):
        return pipeline.apply_theme("mytheme", version, self.superadmin)

    def test_theme_upgrade_success(self):
        self.publish_theme("0.2.0")
        run = self.apply()
        self.assertEqual((run.status, run.from_version, run.to_version), ("succeeded", "0.1.0", "0.2.0"))
        self.assertEqual((self.themes / "mytheme/static/marker.txt").read_text(), "0.2.0")
        self.assertEqual(registry.get_theme("mytheme").version, "0.2.0")
        self.assertTrue(run.backup["dir"])
        self.assertFalse(UpdateLock.objects.exists())

    def test_unsigned_or_foreign_signed_theme_rejected_and_unchanged(self):
        self.publish_theme("0.2.0", seed=None)
        with self.assertRaisesRegex(UpdateBlocked, "امضا"):
            self.apply()
        self.assertEqual((self.themes / "mytheme/static/marker.txt").read_text(), "0.1.0")

    def test_invalid_theme_package_rejected(self):
        src = self.make_theme("0.2.0", self.base / "bad")
        (src / "templates/base.html").write_text("{% load nonexistent_lib %}")
        # ناشر هم قالب نامعتبر را منتشر نمی‌کند
        with self.assertRaises(UpdateError):
            release.build_theme(src, self.dist, seed=SEED)

    def test_python_file_in_theme_rejected(self):
        src = self.make_theme("0.2.0", self.base / "py")
        (src / "static/evil.py").write_text("print(1)")
        with mock.patch("apps.themes.manifest.ALLOWED_EXTENSIONS", frozenset({"html", "css", "js", "json", "txt", "md", "svg", "png", "jpg", "py"})):
            slug, entry = release.build_theme(src, self.dist, seed=SEED)
        release.write_index(self.dist, themes={slug: [entry]})
        with self.assertRaisesRegex(UpdateError, "پایتون"):
            self.apply()
        self.assertEqual((self.themes / "mytheme/static/marker.txt").read_text(), "0.1.0")

    def test_post_swap_failure_restores_previous_theme(self):
        self.publish_theme("0.2.0")
        real = registry.clear_cache
        n = {"c": 0}

        def boom():
            n["c"] += 1
            real()
            if n["c"] == 1:
                raise RuntimeError("cache error")

        with mock.patch.object(registry, "clear_cache", boom):
            with self.assertRaises(UpdateError):
                self.apply()
        run = UpdateRun.objects.first()
        self.assertEqual(run.status, "rolled_back")
        self.assertEqual((self.themes / "mytheme/static/marker.txt").read_text(), "0.1.0")
        self.assertFalse(UpdateLock.objects.exists())

    def test_theme_requiring_other_core_rejected(self):
        self.publish_theme("0.2.0", ravin=">=9.0")
        with self.assertRaises(UpdateBlocked):
            self.apply()

    def test_default_and_uninstalled_theme_refused(self):
        with self.assertRaisesRegex(UpdateBlocked, "پیش‌فرض"):
            pipeline.apply_theme("default", None, self.superadmin)
        self.publish_theme("0.2.0")
        shutil.rmtree(self.themes / "mytheme")
        with self.assertRaisesRegex(UpdateBlocked, "نصب نیست"):
            self.apply()

    def test_concurrent_lock(self):
        self.publish_theme("0.2.0")
        UpdateLock.objects.create(name="global", holder="x", operation="apply-core")
        with self.assertRaises(UpdateBusy):
            self.apply()


class PluginDelegation(UpdaterCase):
    def test_success_is_recorded_in_unified_history(self):
        from apps.plugins.models import PluginRecord

        PluginRecord.objects.create(slug="demo", name="D", version="1.0.0", is_active=False, manifest={}, fingerprint="x")

        def fake_install(slug, version, user, upgrade_existing=False):
            PluginRecord.objects.filter(slug=slug).update(version="1.1.0")
            return "upgraded", slug

        with mock.patch("apps.plugins.installer.install_from_registry", side_effect=fake_install) as m:
            run = pipeline.apply_plugin("demo", None, self.superadmin)
        m.assert_called_once()
        self.assertEqual((run.component, run.slug, run.status, run.from_version, run.to_version), ("plugin", "demo", "succeeded", "1.0.0", "1.1.0"))

    def test_failure_recorded_and_signature_rules_stay_with_installer(self):
        from apps.plugins.installer import InstallError

        with mock.patch("apps.plugins.installer.install_from_registry", side_effect=InstallError("امضا نامعتبر")):
            with self.assertRaisesRegex(UpdateError, "امضا"):
                pipeline.apply_plugin("demo", None, self.superadmin)
        self.assertEqual(UpdateRun.objects.first().status, "failed")

    def test_refused_while_core_update_active(self):
        UpdateLock.objects.create(name="global", holder="x", operation="apply-core")
        with self.assertRaises(UpdateBusy):
            pipeline.apply_plugin("demo", None, self.superadmin)
