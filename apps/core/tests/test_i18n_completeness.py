"""The i18n safety net.

Fails when
* text is shown to people without being marked for translation (Persian literals in Python, any visible text or
  title/alt/placeholder/aria-label in an HTML template);
* a string marked for translation (Python, templates, JS) is missing from the catalogs;
* a catalog is incomplete (empty / fuzzy / obsolete entries), disagrees with the others about which strings exist, loses or
  invents a ``%(placeholder)s``, or its compiled ``.mo`` is out of date with its ``.po``.

Only the standard library and Django are used, so it runs anywhere ``manage.py test`` runs (no gettext tools needed).
"""
import ast
import collections
import gettext as pygettext
import json
import re
from pathlib import Path

from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase
from django.utils.translation import to_locale
from django.utils.translation.template import templatize

ROOT = Path(settings.BASE_DIR)
LOCALE_DIR = ROOT / "locale"
DOMAINS = ("django", "djangojs")
LANGUAGES = [code for code, _name in settings.LANGUAGES if code != "en"]  # English is the msgid language: no catalog
LOCALES = [to_locale(code) for code in LANGUAGES]  # fa, ar, tr, es, fr, de, ru, pt_BR, zh_Hans

ARABIC_SCRIPT = re.compile("[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
LATIN_WORD = re.compile(r"[A-Za-z]{3,}")
PLACEHOLDER = re.compile(r"%\(\w+\)[sdifr]|%[sdif]|\{\w+\}")  # "%%" is a literal percent sign: a language may use its own (Persian ٪)

# --------------------------------------------------------------------------------------------------------------------
# Explicit, justified exceptions. Everything not listed here must be translatable.
# --------------------------------------------------------------------------------------------------------------------
# Persian literals that are data, not UI text: (relative path, literal) → why.
PERSIAN_DATA = {
    "apps/public/formatting.py": "Jalali month names (calendar data, used only while the language is Persian)",
    "apps/shop/forms.py": "Persian/Arabic-Indic digit tables used to normalise input",
    "apps/core/localefmt.py": "digit tables and the Persian thousands separator (formatting data)",
    "apps/core/deprecation.py": "developer-facing deprecation warnings (never shown to site visitors)",
    "apps/shop/views.py": "values of the SHOP_CURRENCY_LABEL setting that are translated for display",
}
# Template text that is deliberately not translated: technical tokens and API names.
TEMPLATE_LITERALS = {"(API )", "Authorization: Bearer ..."}
# Directories whose Persian text is intentionally not translated (CLI / start-up errors for the server operator, tests).
SKIP_PYTHON_DIRS = ("__pycache__", "/migrations", "/tests", "/management/commands", "config/settings")

# Technical-looking text: identifiers, paths, flags, domains, hash names, bare lowercase tokens (CSS classes, keywords).
TECHNICAL = re.compile(r"[_/=\\]|--|\.py|\.json|\.md|\.html|\bhttps?://|\w\.\w+\.\w+|^[A-Z]+-\d+$|^[a-z0-9-]+$|^Ravix$")

MARKERS = {"gettext", "gettext_lazy", "gettext_noop", "ngettext", "ngettext_lazy", "pgettext", "pgettext_lazy", "npgettext", "_"}
LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def walk(bases, suffixes, *, skip=()):
    for base in bases:
        top = ROOT / base
        if not top.is_dir():
            continue
        for path in sorted(top.rglob("*")):
            if path.suffix in suffixes and path.is_file() and not any(part in path.as_posix() for part in skip):
                yield path


def python_files():
    return walk(("apps", "config", "themes", "plugins"), {".py"}, skip=SKIP_PYTHON_DIRS)


def template_files():
    for path in walk(("apps", "templates", "themes", "plugins"), {".html", ".txt"}, skip=("__pycache__", "/tests")):
        if "templates" in path.relative_to(ROOT).parts:
            yield path


def js_files():
    return walk(("static", "themes", "plugins", "apps"), {".js"}, skip=("__pycache__", "/tests", ".min.", "/vendor/"))


# --------------------------------------------------------------------------------------------------------------------
# .po parsing (stdlib only)
# --------------------------------------------------------------------------------------------------------------------
def _unquote(text: str) -> str:
    return ast.literal_eval(f'"{text}"')


def parse_po(path: Path):
    """→ (entries, obsolete_count). entry = {"ctx", "id", "plural", "str": [msgstr…], "fuzzy"}; the header is dropped."""
    entries, cur, field, obsolete = [], None, None, 0

    def flush():
        nonlocal cur
        if cur is not None and cur["id"] != "":
            entries.append(cur)
        cur = None

    flags_pending = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            flush()
            flags_pending = []
            continue
        if line.startswith("#~"):
            obsolete += 1
            continue
        if line.startswith("#,"):
            flags_pending = [f.strip() for f in line[2:].split(",")]
            continue
        if line.startswith("#"):
            continue
        m = re.match(r'(msgctxt|msgid_plural|msgid|msgstr(?:\[(\d+)\])?)\s+"(.*)"$', line)
        if m:
            key, text = m.group(1), _unquote(m.group(3))
            if key == "msgid" and cur is not None and cur["str"]:
                flush()
            if cur is None:
                cur = {"ctx": None, "id": "", "plural": None, "str": [], "fuzzy": "fuzzy" in flags_pending}
            if key == "msgctxt":
                cur["ctx"], field = text, ("ctx",)
            elif key == "msgid":
                cur["id"], field = text, ("id",)
            elif key == "msgid_plural":
                cur["plural"], field = text, ("plural",)
            else:
                cur["str"].append(text)
                field = ("str", len(cur["str"]) - 1)
            continue
        if line.startswith('"') and cur is not None and field:
            text = _unquote(line[1:-1])
            if field[0] == "str":
                cur["str"][field[1]] += text
            else:
                cur[field[0]] += text
    flush()
    return entries, obsolete


def key_of(entry) -> str:
    return f"{entry['ctx']}\x04{entry['id']}" if entry["ctx"] is not None else entry["id"]


def catalog_path(locale: str, domain: str, ext: str) -> Path:
    return LOCALE_DIR / locale / "LC_MESSAGES" / f"{domain}.{ext}"


def load_po(locale: str, domain: str):
    return parse_po(catalog_path(locale, domain, "po"))


# --------------------------------------------------------------------------------------------------------------------
# Extraction of marked strings
# --------------------------------------------------------------------------------------------------------------------
def marked_in_python(source: str, name: str = "<string>") -> set:
    found = set()
    for node in ast.walk(ast.parse(source, filename=name)):
        if not isinstance(node, ast.Call):
            continue
        fn = getattr(node.func, "id", getattr(node.func, "attr", ""))
        if fn not in MARKERS or not node.args:
            continue
        first = node.args[0]
        if fn in ("pgettext", "pgettext_lazy", "npgettext"):
            if len(node.args) >= 2 and all(isinstance(a, ast.Constant) and isinstance(a.value, str) for a in node.args[:2]):
                found.add(f"{node.args[0].value}\x04{node.args[1].value}")
        elif fn in ("ngettext", "ngettext_lazy"):
            continue  # plural entries are not used by Ravix
        elif isinstance(first, ast.Constant) and isinstance(first.value, str):
            found.add(first.value)
    return found


_STR = r"""u?(?:'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")"""
_TPL_GETTEXT = re.compile(rf"\bgettext\(\s*({_STR})\s*\)")
_TPL_PGETTEXT = re.compile(rf"\bpgettext\(\s*({_STR})\s*,\s*({_STR})\s*\)")
_TPL_UNDERSCORE = re.compile(rf"(?<![\w])_\(\s*({_STR})\s*\)")


def marked_in_template_source(source: str, name: str = "<template>") -> set:
    """Strings marked in a template ({% trans %}, {% blocktrans %}, {{ _('x') }}), found with Django's own ``templatize``
    (the function makemessages uses), so the result is exactly what makemessages would extract."""
    code = templatize(source, origin=name)
    found = {ast.literal_eval(m.group(1)) for m in _TPL_GETTEXT.finditer(code)}
    found |= {ast.literal_eval(m.group(1)) for m in _TPL_UNDERSCORE.finditer(code)}
    found |= {f"{ast.literal_eval(m.group(1))}\x04{ast.literal_eval(m.group(2))}" for m in _TPL_PGETTEXT.finditer(code)}
    return found


def marked_in_templates():
    found = {}
    for path in template_files():
        try:
            msgids = marked_in_template_source(path.read_text(encoding="utf-8"), rel(path))
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f"{rel(path)}: cannot be scanned for translatable text: {exc}") from exc
        for msgid in msgids:
            found.setdefault(msgid, rel(path))
    return found


JS_CALL = re.compile(r"""(?<![\w.])gettext\(\s*("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')\s*\)""")


def marked_in_js():
    found = {}
    for path in js_files():
        for m in JS_CALL.finditer(path.read_text(encoding="utf-8")):
            literal = m.group(1)
            text = json.loads(literal) if literal[0] == '"' else ast.literal_eval(literal)
            found.setdefault(text, rel(path))
    return found


# --------------------------------------------------------------------------------------------------------------------
# Scanning for unmarked text
# --------------------------------------------------------------------------------------------------------------------
def unmarked_persian_in_python():
    problems = []
    for path in python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parent = {id(c): n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        docstrings = set()
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body:
                first = n.body[0]
                if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant):
                    docstrings.add(id(first.value))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str) and ARABIC_SCRIPT.search(node.value)):
                continue
            if id(node) in docstrings:
                continue
            up, exempt = parent.get(id(node)), False
            while up is not None:
                if isinstance(up, ast.Call):
                    fn = getattr(up.func, "id", getattr(up.func, "attr", ""))
                    if fn in MARKERS or (fn in LOG_METHODS and isinstance(up.func, ast.Attribute)):
                        exempt = True  # marked for translation, or a log line (operator-facing, stays in one language)
                        break
                up = parent.get(id(up))
            if not exempt and rel(path) not in PERSIAN_DATA:
                problems.append(f"{rel(path)}:{node.lineno}: {node.value[:50]!r}")
    return problems


_ATTR = re.compile(r"""\b(title|alt|placeholder|aria-label|aria-description|aria-placeholder)\s*=\s*("([^"]*)"|'([^']*)')""", re.I)


def visible_text_problems(source: str):
    """Text shown to a person that is not wrapped in {% trans %}/{% blocktrans %}: text nodes and title/alt/placeholder/aria-*."""
    s = re.sub(r"\{#.*?#\}", "", source, flags=re.S)
    s = re.sub(r"\{%\s*comment\b.*?\{%\s*endcomment\s*%\}", "", s, flags=re.S)
    s = re.sub(r"\{%\s*(blocktrans|blocktranslate)\b.*?\{%\s*(endblocktrans|endblocktranslate)\s*%\}", "", s, flags=re.S)
    s = re.sub(r"\{%\s*block\s+(\w*_class)\s*%\}.*?\{%\s*endblock[^%]*%\}", "", s, flags=re.S)  # CSS class blocks
    s = re.sub(r"\{%.*?%\}", "", s, flags=re.S)
    s = re.sub(r"\{\{.*?\}\}", "", s, flags=re.S)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"<(script|style|pre|code|kbd|svg)\b.*?</\1\s*>", "", s, flags=re.S | re.I)
    problems = []

    def suspicious(text):
        text = text.strip()
        return bool(text) and (ARABIC_SCRIPT.search(text) or LATIN_WORD.search(text)) and not TECHNICAL.search(text) and text not in TEMPLATE_LITERALS

    for m in _ATTR.finditer(s):
        value = m.group(3) if m.group(3) is not None else m.group(4)
        if suspicious(value):
            problems.append(f"{m.group(1)}={value.strip()[:50]!r}")
    for line in re.sub(r"<[^>]*>", "\n", s).split("\n"):
        if suspicious(line):
            problems.append(line.strip()[:60])
    return problems


# --------------------------------------------------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------------------------------------------------
class UnmarkedTextTests(SimpleTestCase):
    def test_no_unmarked_persian_in_python(self):
        problems = unmarked_persian_in_python()
        self.assertEqual(problems, [], "Persian text must be written in English and marked with gettext():\n" + "\n".join(problems))

    def test_no_unmarked_text_in_templates(self):
        problems = []
        for path in template_files():
            problems += [f"{rel(path)}: {p}" for p in visible_text_problems(path.read_text(encoding="utf-8"))]
        self.assertEqual(problems, [], "Wrap visible text in {% trans %} / {% blocktrans %}:\n" + "\n".join(problems))

    def test_the_scanners_themselves_detect_problems(self):
        """Guard against a scanner that silently stops finding anything."""
        self.assertTrue(visible_text_problems("<h1>Plugin Directory</h1>"))
        self.assertTrue(visible_text_problems("<p>سلام دنیا</p>"))
        self.assertTrue(visible_text_problems('<input placeholder="Search here">'))
        self.assertFalse(visible_text_problems('<h1>{% trans "Hello" %}</h1><input placeholder="{% trans \'x\' %}">'))
        self.assertFalse(visible_text_problems("<p>{% blocktrans %}Plain English text{% endblocktrans %}</p>"))
        self.assertFalse(visible_text_problems('<div class="content-narrow">{{ x }}</div>'))
        self.assertEqual(marked_in_python("a = gettext('One')\nb = _('Two %(n)s')\nc = other('No')"), {"One", "Two %(n)s"})
        self.assertEqual(marked_in_template_source('{% trans "A" %}{% blocktrans with n=x %}B {{ n }}{% endblocktrans %}{{ _("C") }}'), {"A", "B %(n)s", "C"})


class MarkedStringsAreTranslatedTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.django_ids = {key_of(e) for e in load_po("fa", "django")[0]}
        cls.js_ids = {key_of(e) for e in load_po("fa", "djangojs")[0]}

    def test_python_strings_are_in_the_catalog(self):
        missing = []
        for path in python_files():
            for msgid in marked_in_python(path.read_text(encoding="utf-8"), rel(path)):
                if msgid not in self.django_ids:
                    missing.append(f"{rel(path)}: {msgid[:70]!r}")
        # CLI / settings are excluded from the Persian scan but still may mark strings; those must also be translated
        self.assertEqual(missing, [], "Run makemessages and translate these into every language:\n" + "\n".join(missing))

    def test_template_strings_are_in_the_catalog(self):
        missing = [f"{where}: {msgid[:70]!r}" for msgid, where in marked_in_templates().items() if msgid not in self.django_ids]
        self.assertEqual(missing, [], "Run makemessages and translate these into every language:\n" + "\n".join(missing))

    def test_javascript_strings_are_in_the_catalog(self):
        missing = [f"{where}: {msgid[:70]!r}" for msgid, where in marked_in_js().items() if msgid not in self.js_ids]
        self.assertEqual(missing, [], "Run makemessages -d djangojs and translate these into every language:\n" + "\n".join(missing))

    def test_there_is_something_to_check(self):
        self.assertGreater(len(self.django_ids), 1500)
        self.assertGreater(len(marked_in_templates()), 300)
        self.assertGreater(len(marked_in_js()), 5)


class CatalogCompletenessTests(SimpleTestCase):
    def test_every_language_has_both_catalogs(self):
        for locale in LOCALES:
            for domain in DOMAINS:
                for ext in ("po", "mo"):
                    self.assertTrue(catalog_path(locale, domain, ext).is_file(), f"missing {locale}/{domain}.{ext}")

    def test_every_entry_is_translated(self):
        problems = []
        for locale in LOCALES:
            for domain in DOMAINS:
                entries, obsolete = load_po(locale, domain)
                if obsolete:
                    problems.append(f"{locale}/{domain}: {obsolete} obsolete (#~) lines; remove them")
                for e in entries:
                    if e["fuzzy"]:
                        problems.append(f"{locale}/{domain}: fuzzy {e['id'][:60]!r}")
                    if not e["str"] or any(not s.strip() for s in e["str"]):
                        problems.append(f"{locale}/{domain}: untranslated {e['id'][:60]!r}")
        self.assertEqual(problems, [], "\n".join(problems[:40]))

    def test_every_language_has_the_same_strings(self):
        for domain in DOMAINS:
            reference = {key_of(e) for e in load_po("fa", domain)[0]}
            for locale in LOCALES:
                ids = {key_of(e) for e in load_po(locale, domain)[0]}
                self.assertEqual(sorted(reference - ids)[:5], [], f"{locale}/{domain}: missing strings")
                self.assertEqual(sorted(ids - reference)[:5], [], f"{locale}/{domain}: strings that the other languages do not have")

    def test_msgids_are_english(self):
        problems = [f"{e['id'][:60]!r}" for domain in DOMAINS for e in load_po("fa", domain)[0] if ARABIC_SCRIPT.search(e["id"])]
        self.assertEqual(problems, [], "msgids must be English; the Persian text belongs in fa/…/django.po:\n" + "\n".join(problems))

    def test_placeholders_survive_translation(self):
        problems = []
        for locale in LOCALES:
            for domain in DOMAINS:
                for e in load_po(locale, domain)[0]:
                    want = collections.Counter(PLACEHOLDER.findall(e["id"]))
                    for translated in e["str"]:
                        if collections.Counter(PLACEHOLDER.findall(translated)) != want:
                            problems.append(f"{locale}/{domain}: {e['id'][:60]!r} → {translated[:60]!r}")
                        elif "%(" in e["id"] and "%" in PLACEHOLDER.sub("", translated).replace("%%", ""):
                            problems.append(f"{locale}/{domain}: stray '%' (write %% for a literal percent) in {translated[:60]!r}")
        self.assertEqual(problems, [], "Placeholders must be kept exactly (names and count):\n" + "\n".join(problems[:40]))

    def test_compiled_catalogs_match_the_po_files(self):
        problems = []
        for locale in LOCALES:
            for domain in DOMAINS:
                with catalog_path(locale, domain, "mo").open("rb") as fh:
                    compiled = {k: v for k, v in pygettext.GNUTranslations(fh)._catalog.items() if k != ""}
                expected = {key_of(e): e["str"][0] for e in load_po(locale, domain)[0] if not e["fuzzy"] and e["str"] and e["str"][0]}
                stale = [k for k in expected if compiled.get(k) != expected[k]][:3]
                extra = [k for k in compiled if k not in expected and not isinstance(k, tuple)][:3]
                if stale or extra or len(compiled) != len(expected):
                    problems.append(f"{locale}/{domain}.mo is out of date (differs on {stale or extra}); run compilemessages")
        self.assertEqual(problems, [], "\n".join(problems))


# --------------------------------------------------------------------------------------------------------------------
# All ten languages, named explicitly (a language can never silently drop out)
# --------------------------------------------------------------------------------------------------------------------
EXPECTED_LANGUAGES = ["fa", "en", "ar", "tr", "es", "fr", "de", "ru", "pt-br", "zh-hans"]
SCRIPT = {"fa": "[\u0600-\u06FF]", "ar": "[\u0600-\u06FF]", "ru": "[\u0400-\u04FF]", "zh-hans": "[\u4E00-\u9FFF]"}
RTL = {"fa", "ar"}
SAME_AS_ENGLISH_OK = 0.05  # Latin-script languages share many words with English (Status, Spam, Post …)
SCRIPT_TOKENS_OK = {"Ravix", "RAVIX", "GATEWAY", "KEY", "Python", "URL", "SEO", "Email", "mail", "lt", "gt", "code", "amp"}  # names, env-var parts, HTML entities


def translated_pairs(locale):
    return [(e["id"], e["str"][0]) for domain in DOMAINS for e in load_po(locale, domain)[0] if e["str"]]


class TenLanguagesTests(SimpleTestCase):
    def test_exactly_these_ten_languages_are_supported(self):
        self.assertEqual([code for code, _ in settings.LANGUAGES], EXPECTED_LANGUAGES)
        self.assertEqual(len(EXPECTED_LANGUAGES), 10)

    def test_every_language_but_english_has_a_complete_catalog(self):
        for code in EXPECTED_LANGUAGES:
            if code == "en":
                continue
            locale = to_locale(code)
            for domain in DOMAINS:
                entries, _ = load_po(locale, domain)
                self.assertGreater(len(entries), 100 if domain == "djangojs" else 2000, f"{code}/{domain}")
                self.assertTrue(catalog_path(locale, domain, "mo").is_file(), f"{code}/{domain}.mo")

    def test_english_is_the_source_language_and_has_no_catalog(self):
        """English strings are the msgids: a catalog for ``en`` would only add a second copy that can drift."""
        self.assertFalse((LOCALE_DIR / "en").exists())
        from django.utils import translation

        with translation.override("en"):
            for msgid, _ in translated_pairs("fa")[:200]:
                if "%" not in msgid:
                    self.assertEqual(translation.gettext(msgid), msgid)

    def test_translations_are_not_copies_of_the_english_text(self):
        for code in EXPECTED_LANGUAGES:
            if code == "en":
                continue
            pairs = [(i, t) for i, t in translated_pairs(to_locale(code)) if len(i) > 3]
            same = [i for i, t in pairs if i == t]
            limit = 0.01 if code in SCRIPT else SAME_AS_ENGLISH_OK
            self.assertLessEqual(len(same) / len(pairs), limit, f"{code}: too many untranslated copies, e.g. {same[:8]}")

    def test_non_latin_languages_really_use_their_script(self):
        for code, pattern in SCRIPT.items():
            bad = []
            for msgid, text in translated_pairs(to_locale(code)):
                if re.search(pattern, text) or msgid == text:
                    continue
                words = [w for w in re.findall(r"[A-Za-z]{2,}", PLACEHOLDER.sub("", text)) if w not in SCRIPT_TOKENS_OK]
                if words:
                    bad.append((msgid[:40], text[:40]))
            self.assertEqual(bad, [], f"{code}: Latin-only translations")


class TenLanguagesRenderingTests(TestCase):
    def get(self, path, code):
        client = Client()
        client.cookies[settings.LANGUAGE_COOKIE_NAME] = code
        return client.get(path)

    def test_every_language_renders_login_and_the_public_site(self):
        english_login = self.get("/accounts/login/", "en").content.decode()
        for code in EXPECTED_LANGUAGES:
            for path in ("/accounts/login/", f"/{code}/"):
                response = self.get(path, code)
                self.assertEqual(response.status_code, 200, f"{code} {path}")
                html = response.content.decode()
                self.assertIn(f'lang="{code}"', html, f"{code} {path}")
                self.assertIn(f'dir="{"rtl" if code in RTL else "ltr"}"', html, f"{code} {path}")
            login = self.get("/accounts/login/", code).content.decode()
            if code == "en":
                continue
            self.assertNotEqual(login, english_login, code)
            if code in SCRIPT:
                self.assertTrue(re.search(SCRIPT[code], re.sub(r"<[^>]*>", " ", login)), f"{code}: login page is not in its script")

    def test_english_pages_contain_no_persian_or_arabic_text(self):
        from apps.core.models import SiteSettings

        # The site name is content the owner typed (the migration's default is Persian), not interface text.
        site = SiteSettings.load()
        site.site_name, site.tagline, site.description, site.footer_text = "Demo", "", "", ""
        site.save()
        from django.core.cache import cache

        cache.clear()
        for path in ("/accounts/login/", "/en/", "/en/blog/", "/en/search/"):
            html = re.sub(r"<select\b.*?</select>", " ", self.get(path, "en").content.decode(), flags=re.S)  # the language switcher lists endonyms
            text = re.sub(r"<[^>]*>", " ", html)
            self.assertIsNone(ARABIC_SCRIPT.search(text), f"{path} contains Arabic-script text: {ARABIC_SCRIPT.findall(text)[:10]}")
