"""Official project addresses: ravixcms.com, the GitHub repository, update and registry hosts; all overridable by environment variables."""
import json
import re
import subprocess
import sys
from pathlib import Path

from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.core.version import RAVIX_VERSION

ROOT = Path(__file__).resolve().parents[3]


class ShippedFilesTests(SimpleTestCase):
    def test_shipped_manifests_have_the_official_homepage(self):
        for path in [ROOT / "themes/default/theme.json", *sorted((ROOT / "plugins").glob("*/plugin.json"))]:
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")).get("homepage"), "https://ravixcms.com", path.name)

    def test_theme_homepage_is_validated(self):
        from apps.themes.manifest import inspect_theme_dir

        info = inspect_theme_dir(ROOT / "themes" / "default")
        self.assertEqual(info.homepage, "https://ravixcms.com")

    def test_no_project_placeholders_are_left(self):
        pattern = re.compile(r"updates\.example\.com|registry\.example\.com|<owner>/<repo>|<آدرس-مخزن-کد-شما>")
        offenders = []
        for rel in subprocess.run(["git", "ls-files", "README.md", "SECURITY.md", "CONTRIBUTING.md", "docs", ".env.example", "apps/core/official.py", "config", "deploy"],
                                  cwd=ROOT, capture_output=True, text=True).stdout.split():
            path = ROOT / rel
            if path.is_file() and pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(rel)
        self.assertEqual(offenders, [])

    def test_docs_point_to_the_real_repository(self):
        for name in ("README.md", "SECURITY.md", "CONTRIBUTING.md"):
            self.assertIn("github.com/ravixcms/ravixcms", (ROOT / name).read_text(encoding="utf-8"), name)
            self.assertIn("ravixcms.com", (ROOT / name).read_text(encoding="utf-8"), name)

    def test_settings_come_from_official_module_and_environment(self):
        code = ("from django.conf import settings as s; print(s.RAVIX_WEBSITE_URL, s.RAVIX_DOCS_URL, s.RAVIX_REPO_URL, s.RAVIX_UPDATE_URL, s.RAVIX_OFFICIAL_REGISTRY_URL)")
        base = {"PATH": "/usr/bin:/bin", "DJANGO_SETTINGS_MODULE": "config.settings.development", "PYTHONDONTWRITEBYTECODE": "1"}
        run = lambda extra: subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**base, **extra}, capture_output=True, text=True).stdout.split()  # noqa: E731
        self.assertEqual(run({}), ["https://ravixcms.com", "https://ravixcms.com/docs", "https://github.com/ravixcms/ravixcms", "https://updates.ravixcms.com", "https://plugins.ravixcms.com"])
        self.assertEqual(run({"RAVIX_WEBSITE_URL": "https://my.example.org/", "RAVIX_UPDATE_URL": "https://u.example.org", "RAVIX_OFFICIAL_REGISTRY_URL": ""}),
                         ["https://my.example.org", "https://ravixcms.com/docs", "https://github.com/ravixcms/ravixcms", "https://u.example.org"])


class LinksInTheInterfaceTests(TestCase):
    def test_panel_footer_shows_version_site_and_docs(self):
        c = Client()
        c.force_login(make_user("boss", Role.SUPER_ADMIN))
        html = c.get(reverse("dashboard:home")).content.decode()
        self.assertIn(RAVIX_VERSION, html)
        self.assertIn('href="https://ravixcms.com"', html)
        self.assertIn('href="https://ravixcms.com/docs"', html)

    def test_overridden_and_unsafe_addresses(self):
        c = Client()
        c.force_login(make_user("boss2", Role.SUPER_ADMIN))
        with override_settings(RAVIX_WEBSITE_URL="https://mine.example.org", RAVIX_DOCS_URL="javascript:alert(1)"):
            html = c.get(reverse("dashboard:home")).content.decode()
        self.assertIn('href="https://mine.example.org"', html)
        self.assertNotIn("javascript:alert", html)
