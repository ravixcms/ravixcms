"""install.sh speaks English by default and Persian with RAVIX_CLI_LANG=fa (or --lang fa), like the rest of the CLI.

Every message goes through ``t()`` (directly or via step/ok/warn/die/confirm/ask) with a literal English msgid; the translations live in
``deploy/install.<lang>.tsv`` (msgid TAB translation). The tests read the script, so a new message without a translation fails here.
"""
import collections
import os
import re
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "install.sh"
CATALOG = ROOT / "deploy" / "install.fa.tsv"
PERSIAN = re.compile(r"[؀-ۿ]")
PLACEHOLDER = re.compile(r"%[sd]")
LITERAL = r'"((?:[^"\\]|\\.)*)"'


def script_msgids() -> list[str]:
    """Literal msgids used in install.sh (helper definitions and comments are skipped)."""
    ids = []
    for line in SCRIPT.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or re.match(r"\w+\(\)", stripped):
            continue
        for m in re.finditer(r"(?<![\w$-])(?:t|step|ok|warn|die|confirm)\s+" + LITERAL, line):
            ids.append(m.group(1))
        for m in re.finditer(r"(?<![\w$-])ask\s+\w+\s+" + LITERAL, line):
            ids.append(m.group(1))
        for m in re.finditer(r"trap '(?:die) " + LITERAL, line):
            ids.append(m.group(1))
    return [i for i in dict.fromkeys(ids) if not i.startswith("$")]


def catalog() -> dict[str, str]:
    entries = {}
    for n, line in enumerate(CATALOG.read_text(encoding="utf-8").splitlines(), 1):
        key, sep, value = line.partition("\t")
        assert sep and key and value, f"{CATALOG.name}:{n} is not “msgid<TAB>translation”"
        assert key not in entries, f"{CATALOG.name}:{n} repeats a msgid"
        entries[key] = value
    return entries


class InstallScriptMessagesTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ids = script_msgids()
        cls.fa = catalog()

    def test_the_script_has_messages(self):
        self.assertGreater(len(self.ids), 80)

    def test_no_persian_text_is_left_in_the_script(self):
        # the only Persian allowed is the extra “yes” the interactive prompt accepts
        offenders = [f"{n}: {line.strip()[:70]}" for n, line in enumerate(SCRIPT.read_text(encoding="utf-8").splitlines(), 1)
                     if PERSIAN.search(line) and "بله" not in line]
        self.assertEqual(offenders, [], "install.sh must be English; translate through deploy/install.fa.tsv:\n" + "\n".join(offenders))

    def test_every_message_has_a_persian_translation(self):
        missing = [i for i in self.ids if i not in self.fa]
        self.assertEqual(missing, [], "Add these to deploy/install.fa.tsv:\n" + "\n".join(missing))

    def test_catalog_has_no_unused_entries(self):
        unused = [k for k in self.fa if k not in self.ids]
        self.assertEqual(unused, [], "deploy/install.fa.tsv entries that install.sh does not use:\n" + "\n".join(unused))

    def test_placeholders_match(self):
        bad = [k for k, v in self.fa.items() if collections.Counter(PLACEHOLDER.findall(k)) != collections.Counter(PLACEHOLDER.findall(v))]
        self.assertEqual(bad, [])

    def test_translations_are_not_english(self):
        same = [k for k, v in self.fa.items() if k == v and PERSIAN.search(k) is None and re.search(r"[a-z]{4,} [a-z]{3,}", k)]
        self.assertEqual(same, [])

    def test_script_is_valid_bash(self):
        result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


@skipUnless(shutil.which("bash"), "bash is required")
class InstallScriptRunsInBothLanguagesTests(SimpleTestCase):
    def run_script(self, *args, lang=None, extra_env=None):
        env = {k: v for k, v in os.environ.items() if k != "RAVIX_CLI_LANG"}
        if lang:
            env["RAVIX_CLI_LANG"] = lang
        env.update(extra_env or {})
        return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60)

    def test_help_is_english_by_default(self):
        out = self.run_script("--help").stdout
        self.assertIn("Usage: sudo ./install.sh", out)
        self.assertFalse(PERSIAN.search(out))

    def test_help_is_persian_with_the_environment_variable(self):
        out = self.run_script("--help", lang="fa").stdout
        self.assertIn("استفاده: sudo ./install.sh", out)
        self.assertIn("--domain D", out)  # option names are not translated

    def test_lang_option_works_like_the_variable(self):
        self.assertIn("استفاده:", self.run_script("--help", "--lang", "fa").stdout)
        self.assertIn("استفاده:", self.run_script("--lang", "fa", "--help").stdout)

    def test_regional_variant_falls_back_to_the_base_language(self):
        self.assertIn("استفاده:", self.run_script("--help", lang="fa-IR").stdout)

    def test_unknown_language_stays_english(self):
        self.assertIn("Usage:", self.run_script("--help", lang="xx").stdout)

    def test_unknown_option_error_is_translated_and_exits_2(self):
        en = self.run_script("--bogus")
        fa = self.run_script("--bogus", lang="fa")
        self.assertEqual((en.returncode, fa.returncode), (2, 2))
        self.assertIn("Unknown option: --bogus", en.stderr)
        self.assertIn("گزینهٔ ناشناخته: --bogus", fa.stderr)

    def test_every_catalog_message_formats_with_printf(self):
        # a stray % or a missing argument would print garbage; render each translation with dummy arguments
        for msgid, translation in catalog().items():
            n = len(PLACEHOLDER.findall(msgid))
            for text in (msgid, translation):
                out = subprocess.run(["bash", "-c", 'printf -- "$1" "${@:2}"', "_", text, *["x"] * n], capture_output=True, text=True)
                self.assertEqual(out.returncode, 0, text)
                self.assertNotIn("%", out.stdout.replace("%%", ""), text)
