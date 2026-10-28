"""The document schema: what is accepted, what is refused, upgrades from the first page builder."""
from django.test import SimpleTestCase, TestCase

from apps.studio.fields import DocumentInvalid, parse_video
from apps.studio.legacy_import import v1_to_v2
from apps.studio.schema import LIMITS, SCHEMA_VERSION, migrate_document, validate_document

from .helpers import clone, column, document, section, simple_doc, widget


class ValidDocumentTests(TestCase):
    def test_every_builtin_widget_validates_with_its_defaults(self):
        from apps.studio.registry import all_widgets

        for spec in all_widgets():
            w = widget(spec.type)
            if spec.container:
                w["columns"] = [column()]
            out = validate_document(document(section(column(w))), can_html=True)
            self.assertEqual(out["sections"][0]["columns"][0]["widgets"][0]["type"], spec.type)

    def test_round_trip_is_stable(self):
        doc = simple_doc("Hello")
        once = validate_document(doc)
        self.assertEqual(validate_document(once), once)

    def test_style_stores_only_what_was_set_per_device(self):
        w = widget("heading", style={"desktop": {"font_size": 40, "color": "#ABCDEF", "text_align": ""}, "mobile": {"font_size": 28}})
        out = validate_document(document(section(column(w))))
        style = out["sections"][0]["columns"][0]["widgets"][0]["style"]
        self.assertEqual(style, {"desktop": {"font_size": 40, "color": "#abcdef"}, "mobile": {"font_size": 28}})

    def test_inner_sections_nest_up_to_the_limit_only(self):
        def nest(levels):
            inner = widget("inner_section")
            inner["columns"] = [column(widget("heading"))]
            for _ in range(levels - 1):
                outer = widget("inner_section")
                outer["columns"] = [column(inner)]
                inner = outer
            return document(section(column(inner)))

        validate_document(nest(2))  # section > inner > inner
        with self.assertRaises(DocumentInvalid):
            validate_document(nest(3))

    def test_unknown_plugin_widget_is_kept_but_core_unknown_is_refused(self):
        ok = document(section(column({"id": "t99999", "type": "gone.widget", "props": {"a": 1}, "style": {}})))
        self.assertEqual(validate_document(ok)["sections"][0]["columns"][0]["widgets"][0]["type"], "gone.widget")
        bad = document(section(column({"id": "t99998", "type": "script", "props": {}, "style": {}})))
        with self.assertRaises(DocumentInvalid):
            validate_document(bad)


class RefusedInputTests(TestCase):
    def errors(self, doc, **kw):
        with self.assertRaises(DocumentInvalid) as cm:
            validate_document(doc, **kw)
        return cm.exception.as_json()

    def test_unknown_keys_are_refused_everywhere(self):
        d = simple_doc()
        d["evil"] = 1
        self.errors(d)
        d = simple_doc()
        d["sections"][0]["onclick"] = "x"
        self.errors(d)
        d = simple_doc()
        d["sections"][0]["columns"][0]["widgets"][0]["props"]["onerror"] = "x"
        self.errors(d)
        d = simple_doc()
        d["sections"][0]["columns"][0]["widgets"][0]["style"] = {"desktop": {"behavior": "url(x)"}}
        self.errors(d)

    def test_css_values_are_typed(self):
        for key, value in (("color", "red"), ("color", "#12"), ("color", "url(javascript:1)"), ("font_size", "20px"), ("font_size", 9999), ("font_size", True),
                           ("text_align", "left; x:y"), ("border_style", "solid;}body{display:none")):
            d = simple_doc()
            d["sections"][0]["columns"][0]["widgets"][0]["style"] = {"desktop": {key: value}}
            self.errors(d)

    def test_urls_must_be_safe(self):
        for url in ("javascript:alert(1)", "data:text/html,x", "//evil.example/", "http://u:p@h/", "vbscript:x", "/ok path"):
            d = document(section(column(widget("button", url=url))))
            self.errors(d)
        validate_document(document(section(column(widget("button", url="/fa/about/"), widget("button", url="https://example.com/x?y=1"),
                                                 widget("button", url="mailto:a@b.co"), widget("button", url="#top")))))

    def test_ids_must_be_unique_and_well_formed(self):
        d = simple_doc()
        d["sections"][0]["id"] = "bad id!"
        self.errors(d)
        d = document(section(column(widget("heading"))), section(column(widget("heading"))))
        d["sections"][1]["id"] = d["sections"][0]["id"]
        self.errors(d)

    def test_limits(self):
        d = document(*[section(column(widget("heading"))) for _ in range(LIMITS["sections"] + 1)])
        self.errors(d)
        d = document(section(*[column() for _ in range(LIMITS["columns_per_section"] + 1)]))
        self.errors(d)
        d = document(section(column(*[widget("heading") for _ in range(LIMITS["widgets_per_column"] + 1)])))
        self.errors(d)
        d = simple_doc("x" * 400)
        self.errors(d)
        big = simple_doc()
        big["meta"]["pad"] = "x" * (LIMITS["max_json_bytes"] + 10)
        self.errors(big)

    def test_markdown_text_loses_raw_html(self):
        d = document(section(column(widget("text", content="Hi <script>alert(1)</script> **bold** <img src=x onerror=alert(1)>"))))
        text = validate_document(d)["sections"][0]["columns"][0]["widgets"][0]["props"]["content"]
        self.assertNotIn("<", text)
        self.assertIn("**bold**", text)

    def test_strict_mode_needs_required_fields(self):
        d = document(section(column(widget("heading", text=""))))
        validate_document(d)
        self.errors(d, strict=True)

    def test_video_link_must_be_a_known_provider(self):
        self.errors(document(section(column(widget("video", source="https://evil.example/watch?v=aaaaaaaaaaa")))))
        self.errors(document(section(column(widget("video", source="javascript:alert(1)")))))
        validate_document(document(section(column(widget("video", source="https://youtu.be/dQw4w9WgXcQ")))))

    def test_parse_video(self):
        self.assertEqual(parse_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=3"), ("youtube", "dQw4w9WgXcQ"))
        self.assertEqual(parse_video("https://youtu.be/dQw4w9WgXcQ"), ("youtube", "dQw4w9WgXcQ"))
        self.assertEqual(parse_video("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"), ("youtube", "dQw4w9WgXcQ"))
        self.assertEqual(parse_video("https://vimeo.com/76979871"), ("vimeo", "76979871"))
        self.assertEqual(parse_video("https://player.vimeo.com/video/76979871?h=abc"), ("vimeo", "76979871"))
        self.assertEqual(parse_video("https://www.aparat.com/v/abC12"), ("aparat", "abC12"))
        for bad in ("", "https://example.com/x", "https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ", "https://vimeo.com/abc", "javascript:alert(1)"):
            self.assertIsNone(parse_video(bad), bad)


class CustomHtmlPermissionTests(TestCase):
    def doc(self, html):
        return document(section(column(widget("custom_html", html=html))))

    def test_html_is_cleaned_when_stored(self):
        out = validate_document(self.doc('<p onclick="x()">hi</p><script>alert(1)</script><iframe src="//e"></iframe><a href="javascript:1">l</a><style>b{}</style>'))
        html = out["sections"][0]["columns"][0]["widgets"][0]["props"]["html"]
        for needle in ("onclick", "<script", "alert", "<iframe", "javascript:", "<style"):
            self.assertNotIn(needle, html)
        self.assertIn("hi", html)

    def test_non_admin_cannot_add_or_change_but_can_keep(self):
        doc = self.doc("<p>mine</p>")
        with self.assertRaises(DocumentInvalid):
            validate_document(doc, can_html=False, existing_html={})
        stored = validate_document(doc)
        wid = stored["sections"][0]["columns"][0]["widgets"][0]["id"]
        existing = {wid: stored["sections"][0]["columns"][0]["widgets"][0]["props"]["html"]}
        validate_document(stored, can_html=False, existing_html=existing)  # unchanged: fine
        changed = clone(stored)
        changed["sections"][0]["columns"][0]["widgets"][0]["props"]["html"] = "<p>other</p>"
        with self.assertRaises(DocumentInvalid):
            validate_document(changed, can_html=False, existing_html=existing)


class MigrationFromFirstBuilderTests(SimpleTestCase):
    def v1(self):
        return {"schema_version": 1, "meta": {"show_title": False}, "sections": [
            {"id": "n0000001", "props": {"width": "full", "padding_y": 60, "bg_color": "#112233", "bg_media": None, "text_color": "", "border_width": 0, "border_style": "solid", "border_color": "", "radius": 0},
             "rows": [{"id": "n0000002", "props": {"gap": 30, "valign": "center", "stack_at": "tablet"}, "columns": [
                 {"id": "n0000003", "props": {"span": 6, "bg_color": "", "padding": 10, "border_width": 0, "border_style": "solid", "border_color": "", "radius": 0},
                  "elements": [
                      {"id": "n0000004", "type": "heading", "props": {"text": "Title", "level": "2", "font_size": 40, "color": "#ffffff", "align": "center", "bg_color": "", "padding": 0, "border_width": 0, "border_style": "solid", "border_color": "", "radius": 0, "margin_bottom": 16}},
                      {"id": "n0000005", "type": "text", "props": {"text": "Line *one*\n\nSecond", "font_size": 17, "color": "", "align": "start", "bg_color": "", "padding": 0, "border_width": 0, "border_style": "solid", "border_color": "", "radius": 0, "margin_bottom": 16}},
                      {"id": "n0000006", "type": "button", "props": {"label": "Go", "url": "/x/", "new_tab": False, "variant": "outline", "bg_color": "#ff0000", "text_color": "", "font_size": 16, "radius": 4, "align": "end", "margin_bottom": 8}},
                  ]},
                 {"id": "n0000007", "props": {"span": 6, "bg_color": "", "padding": 0, "border_width": 0, "border_style": "solid", "border_color": "", "radius": 0}, "elements": []},
             ]}]},
            {"id": "n0000008", "props": {"width": "boxed", "padding_y": 10, "bg_color": "", "bg_media": None, "text_color": "", "border_width": 0, "border_style": "solid", "border_color": "", "radius": 0},
             "rows": [{"id": "n0000009", "props": {"gap": 24, "valign": "start", "stack_at": "mobile"}, "columns": [{"id": "n000000a", "props": {"span": 12}, "elements": []}]},
                      {"id": "n000000b", "props": {"gap": 12, "valign": "start", "stack_at": "never"}, "columns": [{"id": "n000000c", "props": {"span": 12}, "elements": []}]}]},
        ]}

    def test_conversion_validates_and_keeps_the_content(self):
        out = validate_document(self.v1())
        self.assertEqual(out["schema_version"], SCHEMA_VERSION)
        self.assertFalse(out["meta"]["show_title"])
        first = out["sections"][0]
        self.assertEqual(first["props"]["content_width"], "full")
        self.assertEqual(first["props"]["gap"], 30)
        self.assertEqual(first["style"]["desktop"]["padding_top"], 60)
        self.assertEqual(first["style"]["desktop"]["bg_color"], "#112233")
        col = first["columns"][0]
        self.assertEqual(col["style"]["desktop"]["span"], 6)
        self.assertEqual(col["style"]["tablet"]["span"], 12)  # stack_at tablet
        self.assertEqual(col["style"]["mobile"]["span"], 12)
        h, t, b = col["widgets"]
        self.assertEqual((h["props"]["tag"], h["style"]["desktop"]["font_size"], h["style"]["desktop"]["text_align"]), ("h2", 40, "center"))
        self.assertIn("*one*".replace("*", "\\*"), t["props"]["content"])  # the text stays literal, as it was
        self.assertEqual((b["props"]["variant"], b["style"]["desktop"]["btn_color"], b["style"]["desktop"]["btn_radius"], b["style"]["desktop"]["text_align"]),
                         ("outline", "#ff0000", 4, "end"))

    def test_a_section_with_several_rows_becomes_inner_sections(self):
        out = validate_document(self.v1())
        second = out["sections"][1]
        widgets = second["columns"][0]["widgets"]
        self.assertEqual([w["type"] for w in widgets], ["inner_section", "inner_section"])
        self.assertEqual(widgets[1]["props"]["gap"], 12)

    def test_newer_or_missing_versions_are_refused(self):
        for bad in ({"schema_version": 99, "sections": []}, {"sections": []}, {"schema_version": 0}, {"schema_version": True}, [], "x"):
            with self.assertRaises(DocumentInvalid):
                migrate_document(bad)

    def test_converter_is_pure_and_deterministic(self):
        a, b = v1_to_v2(self.v1()), v1_to_v2(self.v1())
        self.assertEqual(a, b)
