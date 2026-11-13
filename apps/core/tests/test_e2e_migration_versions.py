"""scripts/e2e_server_migration.py must not hard-code the versions it updates to: with a fixed number the test breaks as soon as RAVIX_VERSION reaches it
(0.10.1 was “not newer than the current version 0.10.1”)."""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

SCRIPT = Path(settings.BASE_DIR) / "scripts" / "e2e_server_migration.py"


class MigrationScriptVersionsTests(SimpleTestCase):
    def test_target_versions_come_from_the_installed_version(self):
        text = SCRIPT.read_text(encoding="utf-8")
        body = text.split("base_version = current_version()", 1)[1]
        self.assertIn("_patch + n", body)
        self.assertEqual(re.findall(r"""["']\d+\.\d+\.\d+["']""", body), [], "a fixed version number in the update scenarios")
        self.assertFalse(re.search(r"(?<![\w.])\d+\.\d+\.\d+(?![\w.])", re.sub(r"\(\\d\+\)[^\n]*", "", body)), "a fixed version number in a message or a file name")
