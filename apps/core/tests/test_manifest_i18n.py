"""Optional multilingual «name» / «description» in theme.json and plugin.json; bundled packages cover all ten languages."""
import json
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from django.utils import translation

from apps.core.regional import parse_localized_text, pick_localized
from apps.plugins.manifest import ManifestError, parse_manifest
from apps.plugins.tests.helpers import base_manifest
from apps.themes.manifest import inspect_theme_dir

LANGS = [code for code, _ in settings.LANGUAGES]
ROOT = Path(settings.BASE_DIR)
BUNDLED = [ROOT / "themes/default/theme.json", *sorted((ROOT / "plugins").glob("*/plugin.json"))]


class ParseTests(SimpleTestCase):
    def parse(self, value, **kw):
        errors = []
        out = parse_localized_text(value, "name", errors, max_len=kw.pop("max_len", 20), **kw)
        return out, errors

    def test_plain_string_is_unchanged(self):
        self.assertEqual(self.parse(" Blog ")[0], ("Blog", {}))

    def test_object_default_is_english_else_first(self):
        (default, tr), errors = self.parse({"fa": "وبلاگ", "en": "Blog", "pt_BR": "Blogue"})
        self.assertEqual((default, errors), ("Blog", []))
        self.assertEqual(tr, {"fa": "وبلاگ", "en": "Blog", "pt-br": "Blogue"})
        self.assertEqual(self.parse({"fa": "وبلاگ", "de": "Blog"})[0][0], "وبلاگ")

    def test_errors(self):
        self.assertTrue(self.parse({"xx": "Blog"})[1])  # unsupported language
        self.assertTrue(self.parse({"en": ""})[1])
        self.assertTrue(self.parse({"en": "x" * 21})[1])  # too long
        self.assertTrue(self.parse(["Blog"])[1])
        self.assertTrue(self.parse({}, required=True)[1])
        self.assertTrue(self.parse("", required=True)[1])

    def test_pick(self):
        tr = {"fa": "وبلاگ", "de": "Blog DE", "pt-br": "Blogue"}
        self.assertEqual(pick_localized("Blog", tr, "de"), "Blog DE")
        self.assertEqual(pick_localized("Blog", tr, "pt_BR"), "Blogue")
        self.assertEqual(pick_localized("Blog", tr, "ru"), "Blog")  # no entry → default
        with translation.override("fa"):
            self.assertEqual(pick_localized("Blog", tr), "وبلاگ")


class PluginManifestTests(SimpleTestCase):
    def test_multilingual_name_and_description(self):
        raw = base_manifest("demo")
        raw["name"] = {"fa": "نمونه", "en": "Demo", "de": "Beispiel"}
        raw["description"] = {"fa": "توضیح", "en": "Description"}
        m = parse_manifest(raw)
        self.assertEqual((m.name, m.description), ("Demo", "Description"))
        with translation.override("de"):
            self.assertEqual((m.display_name, m.display_description), ("Beispiel", "Description"))
        with translation.override("fa"):
            self.assertEqual(m.display_name, "نمونه")
        self.assertEqual(m.to_json()["name"], raw["name"])
        self.assertEqual(parse_manifest(m.to_json()).to_json(), m.to_json())  # survives the database round trip

    def test_plain_strings_still_work(self):
        m = parse_manifest(base_manifest("demo"))
        self.assertEqual(m.names, {})
        self.assertEqual(m.to_json()["name"], m.name)

    def test_bad_language_code_is_rejected(self):
        raw = base_manifest("demo")
        raw["name"] = {"en": "Demo", "xx": "Nope"}
        with self.assertRaises(ManifestError):
            parse_manifest(raw)


class ThemeManifestTests(SimpleTestCase):
    def test_default_theme_is_localized(self):
        info = inspect_theme_dir(ROOT / "themes/default")
        self.assertTrue(info.valid, info.errors)
        with translation.override("de"):
            self.assertEqual(info.display_name, "Ravix Standard")
        with translation.override("zh-hans"):
            self.assertEqual(info.display_name, "Ravix 默认主题")
        self.assertEqual(info.name, "Ravix default")


class BundledPackagesCoverTenLanguagesTests(SimpleTestCase):
    def test_every_bundled_package_names_and_describes_itself_in_all_ten_languages(self):
        self.assertEqual(len(LANGS), 10)
        for path in BUNDLED:
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("name", "description"):
                self.assertIsInstance(data[key], dict, f"{path.parent.name}: {key}")
                self.assertEqual(sorted(data[key]), sorted(LANGS), f"{path.parent.name}.{key}")
                for lang, text in data[key].items():
                    self.assertTrue(text.strip(), f"{path.parent.name}.{key}.{lang}")
                # every translation is really translated (only the English text may equal the English text)
                english = data[key]["en"]
                for lang in LANGS:
                    if lang != "en":
                        self.assertNotEqual(data[key][lang], english, f"{path.parent.name}.{key}.{lang} is untranslated")
