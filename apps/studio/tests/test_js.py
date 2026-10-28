"""The editor's JavaScript has no build step: check that every file at least parses, and run the pure document-model tests with Node."""
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)
NODE = shutil.which("node")
FILES = ["static/studio/js/core.js", "static/studio/js/inspector.js", "static/studio/js/editor.js", "static/studio/preview.js", "static/studio/front.js"]


class JavaScriptTests(SimpleTestCase):
    def test_the_files_parse(self):
        if not NODE:
            self.skipTest("node is not installed")
        for rel in FILES:
            result = subprocess.run([NODE, "--check", str(ROOT / rel)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"{rel}: {result.stderr}")

    def test_the_document_model_tests_pass(self):
        if not NODE:
            self.skipTest("node is not installed")
        result = subprocess.run([NODE, str(ROOT / "apps/studio/tests/js/core.test.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("core ok", result.stdout)

    def test_no_third_party_hosts_and_no_eval_in_the_editor_code(self):
        for rel in FILES:
            text = (ROOT / rel).read_text(encoding="utf-8").replace("http://www.w3.org/2000/svg", "")  # an XML namespace, not a request
            for bad in ("eval(", "new Function(", "document.write(", "innerHTML = ''+", "http://", "cdn."):
                self.assertNotIn(bad, text, f"{rel} contains {bad}")
            self.assertNotIn("jQuery", text)

    def test_the_vendor_script_is_pinned_with_its_licence(self):
        vendor = ROOT / "static/studio/vendor"
        js = (vendor / "sortable.min.js").read_text(encoding="utf-8")
        self.assertIn("1.15.6", js[:400])
        self.assertTrue((vendor / "SORTABLE-LICENSE.txt").read_text(encoding="utf-8").strip())
        self.assertIn("MIT", (vendor / "SORTABLE-LICENSE.txt").read_text(encoding="utf-8"))
