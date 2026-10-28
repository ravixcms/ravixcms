"""XSS and CSS-injection attempts through every door: the draft API, the preview, import, plugin widgets and stored documents."""
import json

from django.test import Client
from django.urls import reverse

from apps.studio import services
from apps.studio.css import assert_safe_css
from apps.studio.fields import DocumentInvalid
from apps.studio.render import render_document
from apps.studio.schema import validate_document

from .helpers import requires_studio, StudioBase, clone, column, document, section, simple_doc, widget

XSS = [
    "<script>alert(1)</script>", "\"><img src=x onerror=alert(1)>", "<svg/onload=alert(1)>", "javascript:alert(1)", "<iframe src=//evil></iframe>",
    "'-alert(1)-'", "</style><script>alert(1)</script>", "<a href=javascript:alert(1)>x</a>",
]
CSS_EVIL = [
    "red;}body{display:none", "red;background:url(javascript:alert(1))", "expression(alert(1))", "url(http://evil/x)", "red}</style><script>alert(1)</script>",
    "#fff;position:fixed", "{}", "\\72ed", "red/*", "var(--x)", "@import 'x'",
]


from html.parser import HTMLParser  # noqa: E402

FORBIDDEN_TAGS = {"script", "iframe", "object", "embed", "form", "input", "style", "link", "meta", "base", "frame"}


class _Inert(HTMLParser):
    """Collects every real element/attribute: escaped text such as ``&lt;script&gt;`` is text, not an element, so it is fine."""

    def __init__(self):
        super().__init__()
        self.bad = []

    def handle_starttag(self, tag, attrs):
        if tag in FORBIDDEN_TAGS:
            self.bad.append(f"<{tag}>")
        for name, value in attrs:
            if name.startswith("on"):
                self.bad.append(f"{tag}[{name}]")
            if name in ("href", "src", "xlink:href", "action", "formaction") and (value or "").strip().lower().startswith(("javascript:", "data:text", "vbscript:")):
                self.bad.append(f"{tag}[{name}={value}]")

    handle_startendtag = handle_starttag


def assert_inert(testcase, html, msg=""):
    p = _Inert()
    p.feed(html)
    testcase.assertEqual(p.bad, [], msg)


def strings_everywhere(doc):
    """A document with the evil string in every text-like prop of every widget."""
    out = clone(doc)
    for sec in out["sections"]:
        for col in sec["columns"]:
            for w in col["widgets"]:
                for k, v in list(w["props"].items()):
                    if isinstance(v, str) and k not in ("tag", "icon", "align"):
                        w["props"][k] = XSS[0]
    return out


class StoredXssTests(StudioBase):
    def test_no_payload_survives_in_the_rendered_page(self):
        for payload in XSS:
            doc = document(section(column(
                widget("heading", text=payload), widget("text", content=payload), widget("button", label=payload, url="/x/"),
                widget("quote", text=payload, cite=payload), widget("card", title=payload, text=payload, button_label=payload, button_url="/x/"),
                widget("icon_list", items=[{"text": payload, "icon": ""}]), widget("icon", label=payload),
            ), anchor="ok"))
            try:
                clean = validate_document(doc, can_html=False)
            except DocumentInvalid:
                continue  # refused outright is fine too
            html = render_document(clean, validate=False).html
            assert_inert(self, html, payload)
            self.assertNotIn("<img src=x", html, payload)  # escaped to &lt;img ..., never a real element

    def test_urls_with_dangerous_schemes_are_refused_everywhere(self):
        for url in ("javascript:alert(1)", "data:text/html,<script>alert(1)</script>", "vbscript:x", " javascript:alert(1)", "JaVaScRiPt:alert(1)", "//evil.example/x\r\nSet-Cookie: a=b"):
            doc = document(section(column(widget("button", label="x", url=url), widget("heading", text="h", link_url=url))))
            with self.assertRaises(DocumentInvalid, msg=url):
                validate_document(doc)

    def test_xss_in_attribute_positions(self):
        doc = document(section(column(widget("image", alt='"><script>alert(1)</script>', media=None), widget("icon", label='" onmouseover="alert(1)', icon="star")), anchor='x" onclick="y'))
        with self.assertRaises(DocumentInvalid):
            validate_document(doc)  # the anchor is not a valid id
        doc["sections"][0]["props"]["anchor"] = "ok"
        html = render_document(validate_document(doc), validate=False).html
        assert_inert(self, html)  # the quote in the label is escaped, so it cannot open a new attribute

    def test_a_tampered_stored_document_is_still_rendered_safely(self):
        """validate=False is what the public path uses for stored versions: the renderer escapes on its own."""
        doc = simple_doc(XSS[0])
        doc["sections"][0]["columns"][0]["widgets"].append(widget("text", content="[x](javascript:alert(1)) <script>alert(1)</script>"))
        html = render_document(doc, validate=False).html
        assert_inert(self, html)


class CssInjectionTests(StudioBase):
    def test_free_text_never_reaches_a_style_value(self):
        for evil in CSS_EVIL:
            for field in ("bg_color", "color", "border_color", "bg_grad_from", "bg_grad_to"):
                doc = simple_doc()
                doc["sections"][0]["style"] = {"desktop": {field: evil}}
                with self.assertRaises(DocumentInvalid, msg=f"{field}={evil}"):
                    validate_document(doc)

    def test_numbers_choices_and_media_are_typed(self):
        base = simple_doc()
        for style in ({"padding_top": "10px;color:red"}, {"padding_top": 10**9}, {"padding_top": -5}, {"shadow": "0 0 99px red"}, {"border_style": "solid;x"},
                      {"bg_size": "url(x)"}, {"bg_media": "x"}, {"bg_media": -3}, {"bg_media": True}, {"radius": "5px"}, {"nope": 1}, {"hidden": "yes"}):
            doc = clone(base)
            doc["sections"][0]["style"] = {"desktop": style}
            with self.assertRaises(DocumentInvalid, msg=str(style)):
                validate_document(doc)
        doc = clone(base)
        doc["sections"][0]["style"] = {"watch": {"padding_top": 1}}
        with self.assertRaises(DocumentInvalid):
            validate_document(doc)  # only desktop / tablet / mobile

    def test_generated_css_never_contains_markup_or_urls_to_other_hosts(self):
        doc = simple_doc()
        doc["sections"][0]["style"] = {"desktop": {"bg_color": "#abc", "padding_top": 10, "shadow": "md", "radius": 8}, "mobile": {"hidden": True}}
        from apps.studio.render import BASE_CSS

        css = render_document(doc).css
        assert_safe_css(css[len(BASE_CSS):])
        self.assertNotRegex(css, r"<|expression|javascript|@import|http")

    def test_the_safety_check_catches_a_tampered_generator_output(self):
        for bad in ("a{b:c}</style><script>", "a{b:expression(1)}", "@import 'x';", "a{b:c;}/* x */"):
            with self.assertRaises(ValueError, msg=bad):
                assert_safe_css(bad)


class CustomHtmlTests(StudioBase):
    def doc_with_html(self, html):
        return document(section(column(widget("custom_html", html=html))))

    def test_stored_html_is_cleaned_with_the_allowlist(self):
        evil = '<p onclick="x()">hi</p><script>alert(1)</script><iframe src="//e"></iframe><a href="javascript:alert(1)">l</a><style>*{}</style><form action="/x"><input></form>'
        out = validate_document(self.doc_with_html(evil), can_html=True)["sections"][0]["columns"][0]["widgets"][0]["props"]["html"]
        for needle in ("<script", "onclick", "<iframe", "javascript:", "<style", "<form", "<input"):
            self.assertNotIn(needle, out.lower(), needle)
        self.assertIn("hi", out)

    @requires_studio
    def test_only_administrators_can_add_it_through_the_api(self):
        doc = self.document_for(self.post)
        c = Client()
        c.force_login(self.author)
        r = c.post(reverse("studio:api_draft", args=[doc.pk]), json.dumps({"document": self.doc_with_html("<p>x</p>"), "revision": doc.draft_rev}), content_type="application/json")
        self.assertEqual(r.status_code, 422)
        doc.refresh_from_db()
        self.assertEqual(doc.draft["sections"], [])
        c.force_login(self.admin)
        r = c.post(reverse("studio:api_draft", args=[doc.pk]), json.dumps({"document": self.doc_with_html("<p>x</p>"), "revision": doc.draft_rev}), content_type="application/json")
        self.assertEqual(r.status_code, 200)

    def test_an_author_cannot_alter_html_an_admin_wrote_but_can_keep_it(self):
        d = self.put_draft(self.doc_with_html("<p>admin text</p>"), self.post, self.admin)
        keep = clone(d.draft)
        keep["sections"][0]["columns"][0]["widgets"][0]["props"]["html"] = d.draft["sections"][0]["columns"][0]["widgets"][0]["props"]["html"]
        services.save_draft(d.pk, keep, d.draft_rev, self.admin)  # unchanged: fine for the admin
        d.refresh_from_db()
        edited = clone(d.draft)
        edited["sections"][0]["columns"][0]["widgets"][0]["props"]["html"] = "<p>tampered</p>"
        with self.assertRaises(DocumentInvalid):
            services.validate_for(d, edited, self.author)
        services.validate_for(d, clone(d.draft), self.author)  # keeping it unchanged passes

    def test_the_html_widget_is_admin_only_in_the_palette_specs(self):
        from apps.studio.schema import client_specs

        spec = next(w for w in client_specs()["widgets"] if w["type"] == "custom_html")
        self.assertTrue(spec["admin_only"])

    @requires_studio
    def test_import_cannot_smuggle_html_past_a_non_admin(self):
        payload = {"format": "ravix-studio", "schema_version": 2, "kind": "section", "content": self.doc_with_html("<p>x</p>")["sections"][0]}
        d = self.document_for(self.post)
        c = Client()
        c.force_login(self.author)
        r = c.post(reverse("studio:api_import", args=[d.pk]), json.dumps({"payload": payload, "mode": "append"}), content_type="application/json")
        self.assertEqual(r.status_code, 422)
        d.refresh_from_db()
        self.assertEqual(d.draft["sections"], [])

    def test_the_rendered_html_widget_is_sanitised_again_at_render_time(self):
        doc = self.doc_with_html("<p>ok</p>")
        doc["sections"][0]["columns"][0]["widgets"][0]["props"]["html"] = '<img src=x onerror=alert(1)><script>alert(2)</script><p>kept</p>'
        html = render_document(doc, validate=False).html
        assert_inert(self, html)
        self.assertNotIn("onerror", html)
        self.assertIn("kept", html)


class ResourceLimitTests(StudioBase):
    def test_deeply_nested_json_is_rejected_not_a_recursion_error(self):
        node = {"id": "n0000001", "type": "section", "props": {}, "style": {}, "columns": []}
        deep = node
        for i in range(2000):
            child = {"id": f"n{i:07d}", "type": "section", "props": {}, "style": {}, "columns": []}
            deep["columns"] = [child]
            deep = child
        with self.assertRaises(DocumentInvalid):
            validate_document({"schema_version": 2, "meta": {}, "sections": [node]})

    def test_a_huge_text_is_rejected(self):
        doc = document(section(column(widget("text", content="x" * 25_000))))
        with self.assertRaises(DocumentInvalid):
            validate_document(doc)

    def test_media_that_does_not_exist_is_refused(self):
        d = self.document_for()
        doc = document(section(column(widget("image", media=999999))))
        with self.assertRaises(DocumentInvalid):
            services.validate_for(d, doc, self.admin)
        doc = simple_doc()
        doc["sections"][0]["style"] = {"desktop": {"bg_media": 999999}}
        with self.assertRaises(DocumentInvalid):
            services.validate_for(d, doc, self.admin)
