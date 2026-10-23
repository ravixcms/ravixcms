"""Translations contributed by plugins (gettext trees) and themes (locale/<lang>.json); core strings can never be overridden."""
import gettext as pygettext
import json
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.utils import translation
from django.utils.translation import gettext

from apps.core import catalogs
from apps.themes.manifest import inspect_theme_dir

CORE_MSGID = "Hide password"  # a core string (present in djangojs; also used as a stand-in id below)


def django_msgid():
    """A msgid that exists in the core django.po for German."""
    import polib

    po = polib.pofile(str(settings.BASE_DIR / "locale" / "de" / "LC_MESSAGES" / "django.po"))
    entry = next(e for e in po if e.msgstr and "%" not in e.msgid and len(e.msgid) > 6 and not e.fuzzy)
    return entry.msgid, entry.msgstr


class CatalogTestCase(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ravix-cat-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(catalogs.reset_for_tests)
        self.addCleanup(translation.deactivate)

    def make_tree(self, name, lang_dir, entries):
        root = self.tmp / name
        catalogs.write_mo(root / lang_dir / "LC_MESSAGES" / "django.mo", entries, language=lang_dir)
        return root


class MoWriterTests(CatalogTestCase):
    def test_roundtrip_with_python_gettext(self):
        root = self.make_tree("x", "de", {"Hello": "Hallo", "Zürich ✓": "ツ", "Multi\nline": "Zeile\nzwei"})
        t = pygettext.translation("django", str(root), languages=["de"])
        self.assertEqual(t.gettext("Hello"), "Hallo")
        self.assertEqual(t.gettext("Zürich ✓"), "ツ")
        self.assertEqual(t.gettext("Multi\nline"), "Zeile\nzwei")
        self.assertEqual(t.gettext("missing"), "missing")


class PluginCatalogTests(CatalogTestCase):
    def test_plugin_strings_appear_and_disappear(self):
        msgid = "Zebra crossing ahead (test)"
        root = self.make_tree("plugin", "de", {msgid: "Zebrastreifen voraus"})
        with translation.override("de"):
            self.assertEqual(gettext(msgid), msgid)
        catalogs.register("plugin:demo", root)
        with translation.override("de"):
            self.assertEqual(gettext(msgid), "Zebrastreifen voraus")
        with translation.override("fr"):
            self.assertEqual(gettext(msgid), msgid)  # no French file: falls back to the English text
        catalogs.unregister("plugin:demo")
        with translation.override("de"):
            self.assertEqual(gettext(msgid), msgid)

    def test_core_strings_cannot_be_overridden(self):
        msgid, core = django_msgid()
        root = self.make_tree("plugin", "de", {msgid: "HIJACKED"})
        catalogs.register("plugin:evil", root)
        with translation.override("de"):
            self.assertEqual(gettext(msgid), core)
        self.assertEqual(settings.LOCALE_PATHS[0], str(settings.BASE_DIR / "locale"))

    def test_the_active_language_survives_registration(self):
        translation.activate("de")
        catalogs.register("plugin:demo", self.make_tree("plugin", "de", {"Another test string": "Noch ein Test"}))
        self.assertEqual(translation.get_language(), "de")
        self.assertEqual(gettext("Another test string"), "Noch ein Test")

    def test_registry_unload_drops_the_catalog(self):
        from apps.plugins import registry

        catalogs.register("plugin:gone", self.make_tree("plugin", "de", {"Some string": "Etwas"}))
        registry.unload("gone")
        self.assertNotIn("plugin:gone", catalogs.registered())


class ThemeCatalogTests(CatalogTestCase):
    def theme(self, slug="tcat"):
        dest = self.tmp / slug
        shutil.copytree(settings.BASE_DIR / "themes" / "default", dest)
        manifest = json.loads((dest / "theme.json").read_text(encoding="utf-8"))
        manifest["slug"] = slug
        (dest / "theme.json").write_text(json.dumps(manifest), encoding="utf-8")
        return dest

    def put(self, theme, name, content):
        (theme / "locale").mkdir(exist_ok=True)
        (theme / "locale" / name).write_text(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False), encoding="utf-8")

    def test_json_is_compiled_and_used(self):
        theme = self.theme()
        self.put(theme, "de.json", {"Read the full story": "Ganze Geschichte lesen"})
        self.put(theme, "pt-br.json", {"Read the full story": "Leia a história completa"})
        self.assertTrue(inspect_theme_dir(theme).valid, inspect_theme_dir(theme).errors)
        with self.settings(RAVIX_THEME_INSTALL_DIR=str(self.tmp / "install")):
            catalogs.sync_themes([SimpleNamespace(valid=True, slug="tcat", path=theme)])
            with translation.override("de"):
                self.assertEqual(gettext("Read the full story"), "Ganze Geschichte lesen")
            with translation.override("pt-br"):
                self.assertEqual(gettext("Read the full story"), "Leia a história completa")
            catalogs.sync_themes([])
            with translation.override("de"):
                self.assertEqual(gettext("Read the full story"), "Read the full story")

    def test_core_wins_over_theme(self):
        msgid, core = django_msgid()
        theme = self.theme()
        self.put(theme, "de.json", {msgid: "HIJACKED"})
        with self.settings(RAVIX_THEME_INSTALL_DIR=str(self.tmp / "install")):
            catalogs.sync_themes([SimpleNamespace(valid=True, slug="tcat", path=theme)])
            with translation.override("de"):
                self.assertEqual(gettext(msgid), core)

    def test_recompiled_when_the_file_changes(self):
        theme = self.theme()
        self.put(theme, "de.json", {"Brand new words": "Eins"})
        with self.settings(RAVIX_THEME_INSTALL_DIR=str(self.tmp / "install")):
            info = SimpleNamespace(valid=True, slug="tcat", path=theme)
            catalogs.sync_themes([info])
            self.put(theme, "de.json", {"Brand new words": "Zwei"})
            catalogs.sync_themes([info])
            with translation.override("de"):
                self.assertEqual(gettext("Brand new words"), "Zwei")
            self.assertEqual(len(list((self.tmp / "install" / ".i18n").glob("tcat-*"))), 1)  # old build removed

    def test_validator_rejects_bad_locale_files(self):
        theme = self.theme()
        self.put(theme, "xx.json", {"a": "b"})  # unsupported language
        self.put(theme, "de.json", ["not", "an", "object"])
        self.put(theme, "fr.json", {"ok": 5})  # value is not text
        self.put(theme, "es.json", "{not json")
        errors = "\n".join(inspect_theme_dir(theme).errors)
        for name in ("xx.json", "de.json", "fr.json", "es.json"):
            self.assertIn(name, errors)

    def test_validator_rejects_other_file_types_in_locale(self):
        theme = self.theme()
        self.put(theme, "de.po", "msgid ''")
        self.assertIn("de.po", "\n".join(inspect_theme_dir(theme).errors))

    def test_theme_without_locale_is_untouched(self):
        self.assertTrue(inspect_theme_dir(self.theme()).valid)
        self.assertIsNone(catalogs.compile_theme_catalogs("none", self.theme("none")))
