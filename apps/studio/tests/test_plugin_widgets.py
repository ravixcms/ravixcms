"""Plugins add widgets through ``studio_widgets`` in plugin.json: they appear while the plugin is active and leave the palette when it is not,
but pages that already used them keep their stored HTML."""
import json

from django.test import Client
from django.urls import reverse

from apps.plugins import registry as plugin_registry, services as plugin_services
from apps.plugins.manifest import ManifestError, parse_manifest
from apps.plugins.tests.helpers import PluginTestCase, base_manifest
from apps.studio import plugin_widgets, services
from apps.studio.fields import DocumentInvalid
from apps.studio.registry import all_widgets, get_widget
from apps.studio.render import render_document
from apps.studio.schema import client_specs, validate_document

from .helpers import requires_studio, column, document, section, widget

WIDGETS_PY = '''
def hello(props, ctx):
    return '<p class="hi">Hello, ' + props["name"] + '</p><script>alert(1)</script><img src=x onerror=alert(2)>'

def broken(props, ctx):
    raise RuntimeError("boom")
'''

SPEC = {"type": "hello", "title": "Hello box", "icon": "star", "render": "widgets:hello",
        "css": ".hi{color:#123456;background:url(http://evil/x)}",
        "fields": [{"name": "name", "kind": "text", "label": "Name", "default": "World", "max_len": 30},
                   {"name": "size", "kind": "int", "label": "Size", "default": 3, "min": 1, "max": 9},
                   {"name": "tone", "kind": "choice", "label": "Tone", "default": "a", "choices": [["a", "A"], ["b", "B"]]}]}


def manifest(slug="greet", **over):
    return base_manifest(slug, studio_widgets=[SPEC], **over)


class ManifestTests(PluginTestCase):
    def parse(self, **over):
        data = manifest()
        data.update(over)
        return parse_manifest(data, expected_slug="greet")

    def test_a_valid_declaration_is_accepted(self):
        m = self.parse()
        self.assertEqual(m.studio_widgets[0]["type"], "hello")
        self.assertIn("widgets:hello", m.module_refs())

    def test_no_widgets_is_the_default(self):
        data = base_manifest("greet")
        self.assertEqual(parse_manifest(data, expected_slug="greet").studio_widgets, ())

    def test_bad_declarations_are_rejected(self):
        bad = [
            [{**SPEC, "type": "Bad Type"}], [{**SPEC, "type": "hello"}, {**SPEC, "type": "hello"}], [{**SPEC, "render": "not a ref"}],
            [{**SPEC, "title": ""}], [{**SPEC, "extra": 1}], [{**SPEC, "css": "x" * 9000}], [{**SPEC, "fields": [{"name": "A", "kind": "text", "label": "x"}]}],
            [{**SPEC, "fields": [{"name": "a", "kind": "html", "label": "x"}]}],  # raw HTML fields are not available to plugins
            [{**SPEC, "fields": [{"name": "a", "kind": "text", "label": "x", "bogus": 1}]}], "nope", [SPEC] * 11,
        ]
        for value in bad:
            with self.assertRaises(ManifestError, msg=str(value)[:80]):
                self.parse(studio_widgets=value)


class LifecycleTests(PluginTestCase):
    def setUp(self):
        super().setUp()
        self.write_plugin("greet", manifest(), files={"widgets.py": WIDGETS_PY})
        plugin_widgets._signature = None  # force a fresh sync in each test

    def activate(self):
        self.install_activate("greet")
        self.invalidate()
        plugin_widgets._signature = None

    def deactivate(self):
        plugin_services.deactivate("greet", self.admin)
        self.invalidate()

    def test_the_widget_appears_when_the_plugin_is_activated_and_leaves_when_it_is_off(self):
        self.assertIsNone(get_widget("greet.hello"))
        self.activate()
        w = get_widget("greet.hello")
        self.assertIsNotNone(w)
        self.assertEqual((w.plugin, w.category, str(w.title)), ("greet", "plugin", "Hello box"))
        self.assertIn("greet.hello", [x.type for x in all_widgets()])
        self.assertIn("greet.hello", [x["type"] for x in client_specs()["widgets"]])
        self.deactivate()
        self.assertIsNone(get_widget("greet.hello"))
        self.assertNotIn("greet.hello", [x["type"] for x in client_specs()["widgets"]])

    def test_the_settings_schema_becomes_typed_fields(self):
        self.activate()
        spec = next(x for x in client_specs()["widgets"] if x["type"] == "greet.hello")
        self.assertEqual([f["name"] for f in spec["fields"]], ["name", "size", "tone"])
        doc = document(section(column(widget("greet.hello", name="Ali", size=4, tone="b"))))
        out = validate_document(doc)
        self.assertEqual(out["sections"][0]["columns"][0]["widgets"][0]["props"]["size"], 4)
        for bad in ({"size": 99}, {"tone": "zzz"}, {"name": "x" * 31}, {"size": "3"}):
            d = document(section(column(widget("greet.hello", **bad))))
            with self.assertRaises(DocumentInvalid, msg=str(bad)):
                validate_document(d)

    def test_the_output_is_cleaned_and_the_css_is_scoped(self):
        self.activate()
        out = render_document(document(section(column(widget("greet.hello", name="Ali")))))
        self.assertIn("Hello, Ali", out.html)
        self.assertNotIn("<script", out.html)
        self.assertNotIn("onerror", out.html)
        self.assertIn(".st-w-greet-hello .hi{color:#123456}", out.css)
        self.assertNotIn("evil", out.css)
        self.assertIn("st-w-greet-hello", out.html)

    def test_a_widget_that_raises_renders_nothing_not_a_500(self):
        spec = {**SPEC, "type": "oops", "render": "widgets:broken"}
        self.write_plugin("greet", base_manifest("greet", studio_widgets=[spec]), files={"widgets.py": WIDGETS_PY})
        self.activate()
        out = render_document(document(section(column(widget("greet.oops", name="x")))), validate=False)
        self.assertEqual(out.html.count("st-w-greet-oops"), 0)

    def test_a_published_page_keeps_working_after_the_plugin_is_switched_off(self):
        from apps.content.models import Page

        self.activate()
        page = Page.objects.create(title="P", slug="p", body="plain", status="published", author=self.admin, language="en")
        d = services.get_document(page)
        services.save_draft(d.pk, document(section(column(widget("greet.hello", name="Ali")))), d.draft_rev, self.admin)
        services.publish_draft(d.pk, self.admin)
        self.deactivate()
        r = Client().get("/en/p/")
        self.assertContains(r, "Hello, Ali")  # the stored HTML does not need the plugin
        d.refresh_from_db()
        self.assertEqual(d.draft["sections"][0]["columns"][0]["widgets"][0]["type"], "greet.hello")  # and the data is kept for when it returns

    @requires_studio
    def test_the_editor_shows_the_gap_but_does_not_delete_the_unavailable_widget(self):
        self.activate()
        d = services.get_document(__import__("apps.content.models", fromlist=["Page"]).Page.objects.create(title="Q", slug="q", body="x", status="published", author=self.admin, language="en"))
        services.save_draft(d.pk, document(section(column(widget("greet.hello", name="Ali")))), d.draft_rev, self.admin)
        self.deactivate()
        c = Client()
        c.force_login(self.admin)
        state = c.get(reverse("studio:api_state", args=[d.pk])).json()
        self.assertTrue(state["ok"])
        self.assertEqual(state["document"]["sections"][0]["columns"][0]["widgets"][0]["type"], "greet.hello")
        self.assertNotIn("greet.hello", [w["type"] for w in client_specs()["widgets"]])
        resaved = c.post(reverse("studio:api_draft", args=[d.pk]), json.dumps({"document": state["document"], "revision": state["revision"]}), content_type="application/json")
        self.assertEqual(resaved.status_code, 200)  # saving does not drop or break the widget of a plugin that is off

    def test_core_widgets_cannot_be_shadowed_and_names_are_namespaced(self):
        from apps.studio.registry import register_studio_widget

        with self.assertRaises(ValueError):
            register_studio_widget("heading", title="x", plugin="greet", render=lambda p, c: "")
        with self.assertRaises(ValueError):
            register_studio_widget("other.hello", title="x", plugin="greet", render=lambda p, c: "")
        with self.assertRaises(ValueError):
            register_studio_widget("a.b", title="x", render=lambda p, c: "")

    def test_a_plugin_widget_is_not_admin_only_but_cannot_carry_raw_html_fields(self):
        self.activate()
        spec = next(x for x in client_specs()["widgets"] if x["type"] == "greet.hello")
        self.assertFalse(spec["admin_only"])
        self.assertNotIn("html", [f["kind"] for f in spec["fields"]])
