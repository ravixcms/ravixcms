import json
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.template import TemplateDoesNotExist
from django.test import SimpleTestCase, TestCase

from apps.themes import engine, registry
from apps.themes.colors import contrast, palette_errors
from apps.themes.config import default_config, sanitize
from apps.themes.constants import REQUIRED_TEMPLATES

from .helpers import ThemeDirMixin


class DefaultThemeTests(TestCase):
    def test_default_theme_is_valid_and_complete(self):
        info = registry.get_theme("default")
        self.assertIsNotNone(info)
        self.assertTrue(info.valid, info.errors)
        for name in REQUIRED_TEMPLATES:
            self.assertTrue((info.templates_dir / name).is_file(), name)
        self.assertTrue(info.screenshot)

    def test_no_python_files_in_theme_folders(self):
        for info in registry.get_themes().values():
            self.assertEqual([p for p in info.path.rglob("*.py")], [], info.slug)


class ContractValidationTests(ThemeDirMixin, TestCase):
    def errors(self, slug):
        info = registry.get_theme(slug)
        self.assertIsNotNone(info)
        return " | ".join(info.errors)

    def test_valid_copy_is_accepted(self):
        self.install_theme("mine")
        self.assertTrue(registry.get_theme("mine").valid)

    def test_slug_must_match_folder(self):
        def edit(path):
            m = path / "theme.json"
            d = json.loads(m.read_text(encoding="utf-8"))
            d["slug"] = "other"
            m.write_text(json.dumps(d), encoding="utf-8")
        self.install_theme("mine", edit=edit)
        self.assertIn("slug", self.errors("mine"))

    def test_missing_required_template(self):
        self.install_theme("mine", edit=lambda p: (p / "templates" / "search.html").unlink())
        self.assertIn("search.html", self.errors("mine"))

    def test_wrong_api_version(self):
        def edit(path):
            m = path / "theme.json"
            d = json.loads(m.read_text(encoding="utf-8"))
            d["api_version"] = 99
            m.write_text(json.dumps(d), encoding="utf-8")
        self.install_theme("mine", edit=edit)
        self.assertIn("api_version", self.errors("mine"))

    def test_python_and_other_executable_files_rejected(self):
        def edit(path):
            (path / "static" / "evil.py").write_text("import os", encoding="utf-8")
            (path / "static" / "run.sh").write_text("echo", encoding="utf-8")
        self.install_theme("mine", edit=edit)
        text = self.errors("mine")
        self.assertIn("evil.py", text)
        self.assertIn("run.sh", text)

    def test_html_in_static_and_non_html_in_templates_rejected(self):
        def edit(path):
            (path / "static" / "page.html").write_text("<p>x</p>", encoding="utf-8")
            (path / "templates" / "note.txt").write_text("x", encoding="utf-8")
        self.install_theme("mine", edit=edit)
        text = self.errors("mine")
        self.assertIn("page.html", text)
        self.assertIn("note.txt", text)

    def test_unknown_root_entry_rejected(self):
        self.install_theme("mine", edit=lambda p: (p / "manage.py").write_text("x", encoding="utf-8"))
        self.assertIn("manage.py", self.errors("mine"))

    def test_symlink_rejected(self):
        def edit(path):
            (path / "static" / "link.css").symlink_to("/etc/passwd")
        self.install_theme("mine", edit=edit)
        self.assertIn("symlink", self.errors("mine"))

    def test_forbidden_template_library_and_debug_tag(self):
        def edit(path):
            f = path / "templates" / "404.html"
            f.write_text("{% load static %}{% debug %}" + f.read_text(encoding="utf-8"), encoding="utf-8")
        self.install_theme("mine", edit=edit)
        text = self.errors("mine")
        self.assertIn("static", text)
        self.assertIn("debug", text)

    def test_syntax_error_reported(self):
        def edit(path):
            f = path / "templates" / "home.html"
            f.write_text(f.read_text(encoding="utf-8") + "{% if %}", encoding="utf-8")
        self.install_theme("mine", edit=edit)
        self.assertIn("home.html", self.errors("mine"))

    def test_base_must_use_required_tags(self):
        def edit(path):
            f = path / "templates" / "base.html"
            f.write_text(f.read_text(encoding="utf-8").replace("{% theme_toolbar %}", ""), encoding="utf-8")
        self.install_theme("mine", edit=edit)
        self.assertIn("theme_toolbar", self.errors("mine"))

    def test_bad_screenshot_path_rejected(self):
        def edit(path):
            m = path / "theme.json"
            d = json.loads(m.read_text(encoding="utf-8"))
            d["screenshot"] = "../../etc/passwd.png"
            m.write_text(json.dumps(d), encoding="utf-8")
        self.install_theme("mine", edit=edit)
        self.assertIn("screenshot", self.errors("mine"))

    def test_invalid_folder_name_rejected(self):
        self.install_theme("Bad_Name")
        info = registry.get_theme("Bad_Name")
        self.assertFalse(info.valid)


class EngineSandboxTests(ThemeDirMixin, TestCase):
    def test_theme_template_cannot_reach_outside_its_folder(self):
        self.install_theme("mine")
        info = registry.get_theme("mine")
        (info.templates_dir / "probe.html").write_text('{% extends "layouts/panel.html" %}', encoding="utf-8")
        with self.assertRaises(TemplateDoesNotExist):
            engine.render(info, "probe.html", {})
        (info.templates_dir / "probe2.html").write_text('{% include "../../../../etc/passwd" %}', encoding="utf-8")
        try:
            output = engine.render(info, "probe2.html", {})
        except Exception:  # noqa: BLE001 - رد شدن با هر خطایی پذیرفتنی است
            output = ""
        self.assertNotIn("root:", output)

    def test_theme_template_sees_no_request_user_or_settings(self):
        self.install_theme("mine")
        info = registry.get_theme("mine")
        (info.templates_dir / "probe.html").write_text(
            "[{{ request.path }}|{{ user.password }}|{{ settings.SECRET_KEY }}|{{ perms }}|{{ csrf_token }}]", encoding="utf-8"
        )
        self.assertEqual(engine.render(info, "probe.html", {}), "[||||]")

    def test_autoescape_is_on(self):
        self.install_theme("mine")
        info = registry.get_theme("mine")
        (info.templates_dir / "probe.html").write_text("{{ v }}", encoding="utf-8")
        self.assertEqual(engine.render(info, "probe.html", {"v": "<script>"}), "&lt;script&gt;")


class ConfigSanitizeTests(SimpleTestCase):
    def test_default_config_is_stable(self):
        self.assertEqual(sanitize(default_config()), default_config())

    def test_garbage_never_raises_and_falls_back(self):
        for junk in (None, [], "x", 5, {"appearance": "x", "menus": 3, "home": [], "header": None, "theme": {"a": 1}}):
            doc = sanitize(junk)
            self.assertEqual(doc["theme"], "default")
            self.assertRegex(doc["appearance"]["primary"], r"^#[0-9a-f]{6}$")

    def test_hostile_values_are_neutralised(self):
        doc = sanitize({
            "theme": "../../etc",
            "appearance": {"primary": "red;} body{display:none", "font": "evil", "font_size": "huge"},
            "header": {"cta_label": "x", "cta_url": "javascript:alert(1)"},
            "menus": {"header": [{"label": "a", "kind": "url", "url": "javascript:alert(1)", "target": None, "new_tab": False}]},
        })
        self.assertEqual(doc["theme"], "default")
        self.assertRegex(doc["appearance"]["primary"], r"^#[0-9a-f]{6}$")
        self.assertEqual(doc["appearance"]["font"], "vazirmatn")
        self.assertNotIn("javascript", json.dumps(doc))


class ColorTests(SimpleTestCase):
    def test_contrast_rules(self):
        self.assertAlmostEqual(contrast("#000000", "#ffffff"), 21.0, places=1)
        self.assertEqual(palette_errors("#4f46e5", "#ffffff", "#1d2433"), [])
        self.assertTrue(palette_errors("#4f46e5", "#ffffff", "#cccccc"))
        self.assertTrue(palette_errors("#ffff00", "#ffffff", "#000000"))


class CommandTests(ThemeDirMixin, TestCase):
    def test_check_themes_ok_and_failure(self):
        out = StringIO()
        call_command("check_themes", "default", stdout=out)
        self.assertIn("default", out.getvalue())
        self.install_theme("broken", edit=lambda p: (p / "templates" / "home.html").unlink())
        with self.assertRaises(CommandError):
            call_command("check_themes", "broken", stderr=StringIO())
        with self.assertRaises(CommandError):
            call_command("check_themes", "does-not-exist", stderr=StringIO())

    def test_startheme_creates_valid_theme(self):
        call_command("startheme", "fresh", "--name", "تازه", stdout=StringIO())
        # startheme در اولین مسیر (themes/ پروژه) می‌نویسد؛ پس از بررسی پاک می‌شود.
        import shutil

        from .helpers import BASE_THEMES

        target = BASE_THEMES / "fresh"
        try:
            registry.clear_cache()
            info = registry.get_theme("fresh")
            self.assertTrue(info.valid, info.errors)
            self.assertEqual(info.display_name, "تازه")
            self.assertEqual(info.version, "0.1.0")
            with self.assertRaises(CommandError):
                call_command("startheme", "fresh", stdout=StringIO())
        finally:
            shutil.rmtree(target, ignore_errors=True)
            registry.clear_cache()

    def test_startheme_rejects_bad_slug(self):
        for bad in ("Bad", "../x", "a b", ""):
            with self.assertRaises(CommandError):
                call_command("startheme", bad, stdout=StringIO())
