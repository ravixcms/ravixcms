"""Themes declare full-width support for Studio pages with ``supports.studio_full_width``; without it the page uses the theme's normal layout."""
import dataclasses
import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import Client, SimpleTestCase

from apps.public import rendering
from apps.studio import services
from apps.themes.manifest import inspect_theme_dir

from .helpers import StudioBase, column, document, section, widget

BUILTIN = Path(settings.BASE_DIR) / "themes"


class ManifestTests(SimpleTestCase):
    def theme_with(self, supports):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = tmp / "default"
        shutil.copytree(BUILTIN / "default", root)
        data = json.loads((root / "theme.json").read_text(encoding="utf-8"))
        if supports is None:
            data.get("supports", {}).pop("studio_full_width", None)
        else:
            data.setdefault("supports", {})["studio_full_width"] = supports
        (root / "theme.json").write_text(json.dumps(data), encoding="utf-8")
        return inspect_theme_dir(root)

    def test_the_bundled_themes_support_it(self):
        for slug in ("default", "nova"):
            self.assertTrue(inspect_theme_dir(BUILTIN / slug).studio_full_width, slug)

    def test_the_flag_is_read_strictly(self):
        self.assertTrue(self.theme_with(True).studio_full_width)
        self.assertFalse(self.theme_with(False).studio_full_width)
        self.assertFalse(self.theme_with(None).studio_full_width)
        self.assertFalse(self.theme_with("yes").studio_full_width)  # only a real true counts
        self.assertFalse(self.theme_with(1).studio_full_width)


class RenderTests(StudioBase):
    def setUp(self):
        super().setUp()
        d = self.put_draft(document(section(column(widget("heading", text="Wide"))), template="full_width", show_title=False))
        services.publish_draft(d.pk, self.admin)

    def test_a_supporting_theme_gets_the_full_width_layout(self):
        html = Client().get("/en/about/").content.decode()
        self.assertIn("page-built", html)
        self.assertNotIn('class="studio-body"', html)

    def test_a_theme_without_support_falls_back_to_its_normal_container(self):
        real = rendering.resolve

        def without_support(request, **kw):
            r = real(request, **kw)
            r.theme = dataclasses.replace(r.theme, studio_full_width=False)
            return r

        with mock.patch.object(rendering, "resolve", without_support):
            html = Client().get("/en/about/").content.decode()
        self.assertNotIn("page-built", html)
        self.assertIn('class="studio-body"', html)
        self.assertIn("Wide", html)

    def test_the_default_template_never_goes_full_width(self):
        d = self.put_draft(document(section(column(widget("heading", text="Boxed"))), template="default", show_title=True))
        services.publish_draft(d.pk, self.admin)
        html = Client().get("/en/about/").content.decode()
        self.assertNotIn("page-built", html)
        self.assertIn("Boxed", html)
