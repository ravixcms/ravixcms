import json
from copy import deepcopy

from django.test import SimpleTestCase

from apps.plugins import versions
from apps.plugins.manifest import ManifestError, parse_manifest
from apps.plugins.settings_schema import SettingField, clean_all
from apps.plugins.tests.helpers import REPO_PLUGINS, base_manifest


class VersionTests(SimpleTestCase):
    def test_satisfies(self):
        self.assertTrue(versions.satisfies("0.5.0", ">=0.5.0,<1.0.0"))
        self.assertTrue(versions.satisfies("0.9.9", ">=0.5,<1.0"))
        self.assertFalse(versions.satisfies("1.0.0", ">=0.5.0,<1.0.0"))
        self.assertFalse(versions.satisfies("0.4.9", ">=0.5.0"))
        self.assertTrue(versions.satisfies("1.2.3", ""))
        self.assertFalse(versions.satisfies("1.2.3", "garbage"))
        self.assertFalse(versions.is_valid_spec("~=1.0"))
        self.assertTrue(versions.is_valid_version("1.0.0"))
        self.assertFalse(versions.is_valid_version("1.0"))


class ManifestTests(SimpleTestCase):
    def bad(self, **over):
        data = base_manifest("demo")
        data.update(over)
        with self.assertRaises(ManifestError) as cm:
            parse_manifest(data)
        return " ".join(cm.exception.errors)

    def test_valid_roundtrip(self):
        m = parse_manifest(base_manifest("demo"))
        self.assertEqual(m.label, "plugin_demo")
        self.assertEqual(parse_manifest(m.to_json()).to_json(), m.to_json())

    def test_samples_are_valid(self):
        for slug in ("contact_form", "seo_toolkit"):
            raw = json.loads((REPO_PLUGINS / slug / "plugin.json").read_text(encoding="utf-8"))
            m = parse_manifest(raw, expected_slug=slug)
            self.assertEqual(parse_manifest(m.to_json()).to_json(), m.to_json())

    def test_rejections(self):
        self.assertIn("slug", self.bad(slug="Bad-Slug"))
        self.assertIn("رزرو", self.bad(slug="admin"))
        self.assertIn("کلید ناشناخته", self.bad(evil_key=1))
        self.assertIn("version", self.bad(version="1.0"))
        self.assertIn("api_version", self.bad(api_version=2))
        self.assertIn("ravin", self.bad(ravin=">>1"))
        self.assertIn("ravin", self.bad(ravin=""))
        self.assertIn("hook ناشناخته", self.bad(hooks=[{"name": "core.everything", "handler": "views:home"}]))
        self.assertIn("view", self.bad(admin_pages=[{"key": "a", "title": "x", "view": "os.system", "capability": "demo.use"}]))
        self.assertIn("view", self.bad(public_routes=[{"path": "a", "view": "../etc:passwd"}]))
        self.assertIn("path", self.bad(public_routes=[{"path": "../x", "view": "views:ping"}]))
        self.assertIn("methods", self.bad(public_routes=[{"path": "x", "view": "views:ping", "methods": ["DELETE"]}]))
        self.assertIn("homepage", self.bad(homepage="javascript:alert(1)"))

    def test_slug_must_match_folder(self):
        with self.assertRaises(ManifestError):
            parse_manifest(base_manifest("demo"), expected_slug="other")

    def test_capability_namespace_enforced(self):
        # افزونه نمی‌تواند مجوز هسته را تعریف/بازتعریف کند یا مجوز افزونهٔ دیگر را ادعا کند
        self.assertIn("capabilities", self.bad(capabilities={"users.manage": "x"}))
        self.assertIn("capabilities", self.bad(capabilities={"other.use": "x"}))
        self.assertIn("capabilities", self.bad(capabilities={"demo.": "x"}))

    def test_page_capability_must_be_known(self):
        self.assertIn("capability", self.bad(admin_pages=[{"key": "a", "title": "x", "view": "views:home", "capability": "made.up"}]))
        # مجوز هسته مجاز است و پیش‌فرض صفحه plugins.manage است (امن‌ترین حالت)
        m = parse_manifest(base_manifest("demo", admin_pages=[{"key": "a", "title": "x", "view": "views:home"}]))
        self.assertEqual(m.admin_pages[0]["capability"], "plugins.manage")

    def test_duplicates_rejected(self):
        self.assertIn("تکراری", self.bad(admin_pages=[{"key": "a", "title": "x", "view": "views:home"}, {"key": "a", "title": "y", "view": "views:home"}]))
        self.assertIn("تکراری", self.bad(settings=[{"key": "k", "label": "a"}, {"key": "k", "label": "b"}]))


class SettingsSchemaTests(SimpleTestCase):
    def f(self, **kw):
        return SettingField(key="k", label="برچسب", **kw)

    def test_types(self):
        self.assertEqual(self.f(type="int", min=1, max=10).clean("5"), 5)
        for bad in ("0", "11", "x", "1.5", True):
            with self.assertRaises(Exception):
                self.f(type="int", min=1, max=10).clean(bad)
        self.assertTrue(self.f(type="bool").clean("on"))
        self.assertFalse(self.f(type="bool").clean("0"))
        self.assertEqual(self.f(type="email").clean(" a@b.com "), "a@b.com")
        with self.assertRaises(Exception):
            self.f(type="email").clean("nope")
        with self.assertRaises(Exception):
            self.f(type="url").clean("javascript:alert(1)")
        self.assertEqual(self.f(type="url").clean("/about/"), "/about/")
        with self.assertRaises(Exception):
            self.f(type="choice", choices=(("a", "A"),)).clean("b")
        with self.assertRaises(Exception):
            self.f(max_len=5).clean("123456")
        self.assertEqual(self.f().clean("a\x00b\nc"), "ab c")

    def test_clean_all_ignores_unknown_keys_and_reports_errors(self):
        schema = (self.f(type="int", min=1, max=3),)
        clean, errors = clean_all(schema, {"k": "9", "evil": "x"})
        self.assertIn("k", errors)
        self.assertNotIn("evil", clean)
