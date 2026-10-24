"""Panel-installed themes and plugins with the REAL production static storage (hashed names + manifest), not the test default.

On a real server ``/panel/themes/`` answered 500 right after a ZIP upload: ``ValueError: Missing staticfiles manifest entry for
'themes/<slug>/screenshot.png'``, because the uploaded theme is not in the ``collectstatic`` manifest. Panel-installed themes are now served
from ``/theme-assets/`` (no manifest, no collectstatic, no restart) and the production storage never turns a missing entry into a 500.
"""
import io
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from django.contrib.staticfiles.storage import staticfiles_storage  # noqa: F401
from django.test import Client, SimpleTestCase, override_settings
from django.urls import reverse
from whitenoise.storage import CompressedManifestStaticFilesStorage

from apps.core.staticstorage import RavixStaticFilesStorage
from apps.plugins import installer as plugin_installer
from apps.plugins.models import PluginRecord
from apps.plugins.tests.test_installer import InstallerCase, pkg
from apps.themes import assets, registry
from apps.themes.models import ThemeConfig

from .test_upload import UploadCase, build_zip, default_files, upload

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?"
       b"\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa0\x00\x00\x00\x00IEND\xaeB`\x82")


def production_storage(static_root):
    """The settings production uses: hashed + manifest storage, DEBUG off, a STATIC_ROOT that has never seen the uploaded theme."""
    return override_settings(
        STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                  "staticfiles": {"BACKEND": "apps.core.staticstorage.RavixStaticFilesStorage"}},
        STATIC_ROOT=static_root, DEBUG=False)


def with_screenshot(files):
    manifest = json.loads(files["theme.json"])
    manifest["screenshot"] = "screenshot.png"
    files["theme.json"] = json.dumps(manifest).encode()
    files["static/screenshot.png"] = PNG
    files["static/css/extra.css"] = b"body{color:#123456}"


class ProductionStorageTests(SimpleTestCase):
    def test_default_manifest_storage_really_raises_for_a_missing_entry(self):
        # the failure that happened on the server: documents why the production storage is customised
        with tempfile.TemporaryDirectory() as root, override_settings(STATIC_ROOT=root):
            storage = CompressedManifestStaticFilesStorage()
            with self.assertRaises(ValueError):
                storage.url("themes/nova/screenshot.png")

    def test_ravix_storage_degrades_to_the_plain_url(self):
        with tempfile.TemporaryDirectory() as root, override_settings(STATIC_ROOT=root):
            self.assertEqual(RavixStaticFilesStorage().url("themes/nova/screenshot.png"), "/static/themes/nova/screenshot.png")

    def test_ravix_storage_still_hashes_collected_files(self):
        with tempfile.TemporaryDirectory() as root, override_settings(STATIC_ROOT=root):
            (Path(root) / "a.css").write_text("body{}", encoding="utf-8")
            storage = RavixStaticFilesStorage()
            storage.hashed_files = {"a.css": "a.0123456789ab.css"}
            self.assertEqual(storage.url("a.css"), "/static/a.0123456789ab.css")

    def test_production_settings_use_it(self):
        text = (Path(__file__).resolve().parents[3] / "config/settings/production.py").read_text(encoding="utf-8")
        self.assertIn("apps.core.staticstorage.RavixStaticFilesStorage", text)


class ThemeUploadWithManifestStorageTests(UploadCase):
    def setUp(self):
        super().setUp()
        self.static_root = tempfile.mkdtemp(prefix="ravix-static-")
        self.addCleanup(shutil.rmtree, self.static_root, True)
        ov = production_storage(self.static_root)
        ov.enable()
        self.addCleanup(ov.disable)

    def install(self, slug="my-theme"):
        resp = upload(self.client, build_zip(slug, edit=with_screenshot))
        self.assertEqual(resp.status_code in (200, 302), True, resp.content.decode()[:300])
        self.assertIn(slug, self.installed_names())

    def test_theme_list_after_upload_is_200_and_uses_the_asset_route(self):
        self.install()
        resp = self.client.get(reverse("themes:list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "/theme-assets/my-theme/screenshot.png?v=1.2.3")
        self.assertNotContains(resp, "/static/themes/my-theme/")

    def test_the_screenshot_is_served_without_collectstatic_or_restart(self):
        self.install()
        self.assertFalse((Path(self.static_root) / "themes").exists())
        resp = Client().get("/theme-assets/my-theme/screenshot.png?v=1.2.3")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/png")
        self.assertEqual(resp["X-Content-Type-Options"], "nosniff")
        self.assertIn("immutable", resp["Cache-Control"])
        self.assertEqual(b"".join(resp.streaming_content), PNG)
        css = Client().get("/theme-assets/my-theme/css/extra.css")
        self.assertEqual(css.status_code, 200)
        self.assertEqual(css["Content-Type"].split(";")[0], "text/css")
        self.assertNotIn("immutable", css["Cache-Control"])  # without ?v= the answer may change

    def test_conditional_request_gets_304(self):
        self.install()
        first = Client().get("/theme-assets/my-theme/screenshot.png")
        again = Client().get("/theme-assets/my-theme/screenshot.png", HTTP_IF_MODIFIED_SINCE=first["Last-Modified"])
        self.assertEqual(again.status_code, 304)

    def test_active_installed_theme_renders_the_public_site_with_its_assets(self):
        self.install()
        self.client.post(reverse("themes:activate", args=["my-theme"]))
        self.assertEqual(ThemeConfig.load().active_theme, "my-theme")
        resp = Client().get("/fa/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "/theme-assets/my-theme/css/")
        self.assertEqual(self.client.get(reverse("themes:list")).status_code, 200)

    def test_theme_list_is_200_even_if_a_bundled_asset_is_missing_from_the_manifest(self):
        # STATIC_ROOT is empty: every bundled theme's screenshot is a missing manifest entry; the page must still render
        resp = self.client.get(reverse("themes:list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "/static/themes/default/")

    def test_nothing_unsafe_is_served(self):
        self.install()
        (self.install_dir / "my-theme" / "static" / ".hidden").write_text("x", encoding="utf-8")
        c = Client()
        for path in ("../theme.json", "css/../../theme.json", "%2e%2e/theme.json", ".hidden", "templates/home.html", "nope.png", "css", ""):
            self.assertEqual(c.get(f"/theme-assets/my-theme/{path}").status_code, 404, path)
        self.assertEqual(c.get("/theme-assets/unknown/screenshot.png").status_code, 404)
        self.assertEqual(c.get("/theme-assets/default/screenshot.png").status_code, 404)  # bundled themes use the static pipeline
        self.assertEqual(c.get("/theme-assets/My_Theme/screenshot.png").status_code, 404)
        self.assertEqual(c.post("/theme-assets/my-theme/screenshot.png").status_code, 405)

    def test_html_svg_and_scripts_are_never_served_even_if_they_are_on_disk(self):
        self.install()
        static = self.install_dir / "my-theme" / "static"
        for name in ("page.html", "x.svg", "a.php", "s.xml"):
            (static / name).write_text("<script>alert(1)</script>", encoding="utf-8")
            self.assertEqual(Client().get(f"/theme-assets/my-theme/{name}").status_code, 404, name)

    def test_symlinks_inside_static_are_refused(self):
        self.install()
        static = self.install_dir / "my-theme" / "static"
        secret = Path(self.tmp) / "secret.txt"
        secret.write_text("secret", encoding="utf-8")
        os.symlink(secret, static / "leak.txt")
        os.symlink(self.tmp, static / "dir")
        self.assertEqual(Client().get("/theme-assets/my-theme/leak.txt").status_code, 404)
        self.assertEqual(Client().get("/theme-assets/my-theme/dir/secret.txt").status_code, 404)

    def test_update_by_reinstall_keeps_working_and_removal_stops_serving(self):
        self.install()
        self.assertEqual(Client().get("/theme-assets/my-theme/screenshot.png").status_code, 200)
        shutil.rmtree(self.install_dir / "my-theme")  # an operator removes the theme folder
        registry.get_themes(refresh=True)
        self.assertEqual(Client().get("/theme-assets/my-theme/screenshot.png").status_code, 404)
        self.assertEqual(self.client.get(reverse("themes:list")).status_code, 200)
        self.install()  # installing again works and is served again
        self.assertEqual(Client().get("/theme-assets/my-theme/screenshot.png").status_code, 200)

    def test_installed_themes_are_not_in_staticfiles_dirs(self):
        from django.conf import settings

        text = (Path(settings.BASE_DIR) / "config/settings/base.py").read_text(encoding="utf-8")
        self.assertIn("RAVIX_THEME_INSTALL_DIR", text.split("STATICFILES_DIRS =")[1].split("\n")[0])


class ThemeAssetHelpersTests(UploadCase):
    def test_web_installed_detection(self):
        upload(self.client, build_zip("my-theme"))
        self.assertTrue(assets.is_web_installed(self.install_dir / "my-theme"))
        self.assertFalse(assets.is_web_installed(Path(__file__).resolve().parents[3] / "themes" / "default"))

    def test_url_for_a_bundled_theme_uses_the_static_pipeline(self):
        with production_storage(tempfile.mkdtemp()):
            self.assertTrue(assets.theme_static_url("default", "css/theme.css", "1.0").startswith("/static/themes/default/css/theme.css"))


class PluginInstallWithManifestStorageTests(InstallerCase):
    def test_plugin_install_update_and_removal_pages_stay_200(self):
        from apps.plugins import services
        from apps.plugins.integrity import fingerprint

        with production_storage(tempfile.mkdtemp(prefix="ravix-static-")):
            c = self.login(self.admin)
            self.assertEqual(c.get("/panel/plugins/").status_code, 200)
            self.local(pkg("demo_pkg", "1.0.0"))  # placed, waiting for approval
            self.assertEqual(c.get("/panel/plugins/").status_code, 200)
            services.install("demo_pkg", self.admin, fingerprint(self.tmp / "demo_pkg"))
            services.activate("demo_pkg", self.admin)
            self.assertEqual(c.get("/panel/plugins/demo_pkg/").status_code, 200)
            self.local(pkg("demo_pkg", "1.1.0"), upgrade_existing=True)
            self.assertEqual(c.get("/panel/plugins/demo_pkg/").status_code, 200)
            self.assertEqual(c.get("/panel/plugins/").status_code, 200)
            self.assertEqual(c.get("/panel/plugins/install-package/").status_code, 200)
            services.deactivate("demo_pkg", self.admin)
            services.uninstall("demo_pkg", self.admin, remove_files=True)
            self.assertFalse((self.tmp / "demo_pkg").exists())
            self.assertEqual(c.get("/panel/plugins/").status_code, 200)


class DuplicateSlugTests(UploadCase):
    """Nova ships in themes/nova and an older Nova 1.0 was uploaded earlier into the install folder."""

    def put_old_copy(self, slug="nova"):
        src = Path(__file__).resolve().parents[3] / "themes" / slug
        dst = self.install_dir / slug
        shutil.copytree(src, dst)
        manifest = json.loads((dst / "theme.json").read_text(encoding="utf-8"))
        manifest["version"] = "1.0.0"
        (dst / "theme.json").write_text(json.dumps(manifest), encoding="utf-8")
        registry.get_themes(refresh=True)
        return dst

    def test_the_bundled_theme_wins_and_the_uploaded_copy_is_ignored(self):
        bundled = registry.get_theme("nova")
        self.assertEqual(bundled.path.parent, Path(__file__).resolve().parents[3] / "themes")
        self.put_old_copy()
        winner = registry.get_theme("nova")
        self.assertEqual(winner.path, bundled.path)
        self.assertEqual(winner.version, bundled.version)
        self.assertNotEqual(winner.version, "1.0.0")
        self.assertEqual([s for s, _i, _w in registry.shadowed_themes()], ["nova"])

    def test_assets_come_from_the_bundled_theme_through_the_static_pipeline(self):
        self.put_old_copy()
        with production_storage(tempfile.mkdtemp()):
            self.assertTrue(assets.theme_static_url("nova", "screenshot.png", "1.1.0").startswith("/static/themes/nova/screenshot.png"))
        self.assertEqual(Client().get("/theme-assets/nova/screenshot.png").status_code, 404)

    def test_the_panel_lists_one_nova_and_explains_the_unused_copy(self):
        self.put_old_copy()
        resp = self.client.get(reverse("themes:list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "/static/themes/nova/screenshot.png", count=1)  # one card, from the bundled copy
        self.assertContains(resp, "nova")
        self.assertNotContains(resp, str(self.install_dir))  # no server path in the page

    def test_uploading_the_same_slug_again_is_refused_and_changes_nothing(self):
        before = registry.get_theme("nova").version
        resp = upload(self.client, build_zip("nova"))
        self.assertEqual(resp.status_code, 400)
        self.assertNotIn("nova", self.installed_names())
        self.assertEqual(registry.get_theme("nova").version, before)

    def test_deleting_the_unused_folder_cleans_up(self):
        dst = self.put_old_copy()
        self.assertTrue(registry.shadowed_themes())
        shutil.rmtree(dst)
        registry.get_themes(refresh=True)
        self.assertEqual(registry.shadowed_themes(), [])
        self.assertEqual(self.client.get(reverse("themes:list")).status_code, 200)

    def test_preflight_names_the_unused_folder(self):
        from io import StringIO
        from django.core.management import call_command

        dst = self.put_old_copy()
        out = StringIO()
        with override_settings(RAVIX_CLI_LANG="en"):
            from apps.core import clilang

            clilang.reset_cache()
            self.addCleanup(clilang.reset_cache)
            try:
                call_command("ravix_preflight", stdout=out)
            except SystemExit:
                pass
        self.assertIn(f"is unused: ", out.getvalue())
        self.assertIn(str(dst), out.getvalue())
