from django.test import SimpleTestCase

from apps.builder import schema
from apps.builder.schema import DocumentInvalid, validate_document

from .helpers import clone, col, document, el, row, section, simple_doc


def errors_of(doc, **kw):
    with_ = None
    try:
        validate_document(doc, **kw)
    except DocumentInvalid as exc:
        with_ = exc
    assert with_ is not None, "سند باید رد می‌شد"
    return with_.errors


class ValidDocumentTests(SimpleTestCase):
    def test_valid_document_round_trips_and_is_canonical(self):
        doc = simple_doc()
        out = validate_document(doc)
        self.assertEqual(out["schema_version"], schema.SCHEMA_VERSION)
        self.assertEqual(out["sections"][0]["rows"][0]["columns"][0]["elements"][0]["props"]["text"], "سلام دنیا")
        self.assertEqual(validate_document(out), out)  # idempotent

    def test_missing_props_get_defaults(self):
        doc = {"schema_version": 1, "sections": [{"id": "abcd", "rows": []}]}
        out = validate_document(doc)
        self.assertEqual(out["sections"][0]["props"]["padding_y"], 48)
        self.assertTrue(out["meta"]["show_title"])

    def test_colors_normalized_to_lowercase(self):
        d = document(section(row(col(el("text", color="#ABCDEF"))), bg_color="#FFF"))
        out = validate_document(d)
        self.assertEqual(out["sections"][0]["props"]["bg_color"], "#fff")

    def test_text_is_stripped_of_control_chars(self):
        d = document(section(row(col(el("text", text="a\x00b\x07c\r\nd")))))
        text = validate_document(d)["sections"][0]["rows"][0]["columns"][0]["elements"][0]["props"]["text"]
        self.assertEqual(text, "abc\nd")


class RejectionTests(SimpleTestCase):
    def test_document_must_be_object_with_known_version(self):
        for bad in (None, [], "x", {"sections": []}, {"schema_version": "1"}, {"schema_version": True}, {"schema_version": 99}, {"schema_version": 0}):
            with self.assertRaises(DocumentInvalid, msg=repr(bad)):
                validate_document(bad)

    def test_unknown_top_level_and_node_keys_rejected(self):
        d = simple_doc(); d["script"] = "x"
        self.assertTrue(errors_of(d))
        d = simple_doc(); d["sections"][0]["onclick"] = "x"
        self.assertTrue(errors_of(d))

    def test_unknown_element_type_rejected(self):
        for t in ("script", "html", "iframe", "", None, 5):
            d = simple_doc()
            d["sections"][0]["rows"][0]["columns"][0]["elements"][0]["type"] = t
            self.assertTrue(errors_of(d), t)

    def test_unknown_property_rejected_including_style_and_events(self):
        for key in ("style", "onclick", "html", "class", "href", "script"):
            d = simple_doc()
            d["sections"][0]["rows"][0]["columns"][0]["elements"][0]["props"][key] = "x"
            self.assertTrue(errors_of(d), key)

    def test_bad_colors_rejected(self):
        for bad in ("red", "#12", "#12345", "#gggggg", "url(x)", "#fff;x:y", "expression(1)", 5, ["#fff"]):
            d = document(section(row(col(el("text", color=bad)))))
            self.assertTrue(errors_of(d), bad)

    def test_bad_ints_rejected(self):
        for bad in (-1, 9999, 1.5, "10", True, None):
            d = document(section(row(col()), padding_y=bad))
            self.assertTrue(errors_of(d), repr(bad))

    def test_bad_choice_and_bool(self):
        self.assertTrue(errors_of(document(section(row(col(el("heading", level="1")))))))
        self.assertTrue(errors_of(document(section(row(col(el("heading", level=2)))))))
        self.assertTrue(errors_of(document(section(width="huge"))))
        self.assertTrue(errors_of(document(section(row(col(el("button", new_tab="yes")))))))
        self.assertTrue(errors_of(simple_doc(show_title=1)))

    def test_urls_allowlist(self):
        good = ["/", "/about/", "#top", "https://example.com/x?y=1", "http://example.com", "mailto:a@b.com", "tel:+98912"]
        bad = ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,x", "vbscript:x", "//evil.com", "/\\evil", "http://", "https:///x",
               "http://user:pw@evil.com", "ftp://x", "/a b", "/a\nb", "x.com", "/<script>", '/"onmouseover=1', "https://a.com/'x", "mailto:"]
        for url in good:
            validate_document(document(section(row(col(el("button", url=url))))), strict=True)
        for url in bad:
            self.assertFalse(schema.is_safe_url(url), url)
            self.assertTrue(errors_of(document(section(row(col(el("button", url=url)))))), url)

    def test_length_limits(self):
        self.assertTrue(errors_of(document(section(row(col(el("heading", text="x" * 301)))))))
        self.assertTrue(errors_of(document(section(row(col(el("text", text="x" * 5001)))))))
        validate_document(document(section(row(col(el("text", text="x" * 5000))))))

    def test_structure_limits(self):
        many = document(*[section() for _ in range(schema.LIMITS["sections"] + 1)])
        self.assertTrue(errors_of(many))
        self.assertTrue(errors_of(document(section(row(*[col(span=1) for _ in range(7)])))))
        self.assertTrue(errors_of(document(section(row(col(span=8), col(span=8))))))  # مجموع > ۱۲
        validate_document(document(section(row(col(span=8), col(span=4)))))

    def test_total_node_limit(self):
        big = document(*[section(*[row(*[col(*[el("text") for _ in range(30)], span=6) for _ in range(2)]) for _ in range(10)]) for _ in range(5)])
        self.assertTrue(any("کل اجزا" in m for _, m in errors_of(big)))

    def test_duplicate_and_malformed_ids(self):
        d = document(section(), section())
        d["sections"][1]["id"] = d["sections"][0]["id"]
        self.assertTrue(errors_of(d))
        for bad in ("A!", "x", "a" * 40, "ab cd", None, 5):
            d = simple_doc(); d["sections"][0]["id"] = bad
            self.assertTrue(errors_of(d), repr(bad))

    def test_non_dict_children(self):
        for mutate in (lambda d: d.__setitem__("sections", "x"), lambda d: d["sections"].append(5),
                       lambda d: d["sections"][0].__setitem__("rows", {}), lambda d: d["sections"][0].__setitem__("props", [])):
            d = simple_doc(); mutate(d)
            self.assertTrue(errors_of(d))

    def test_error_list_is_capped(self):
        d = document(*[section(padding_y=-5) for _ in range(40)])
        self.assertLessEqual(len(errors_of(d)), schema.MAX_ERRORS)

    def test_media_id_must_be_positive_int(self):
        for bad in (0, -3, "5", True, 1.5, 2 ** 60):
            self.assertTrue(errors_of(document(section(row(col(el("image", media=bad)))))), repr(bad))


class StrictModeTests(SimpleTestCase):
    def test_draft_allows_incomplete_but_strict_does_not(self):
        d = document(section(row(col(el("image"), el("heading", text=""), el("button", label="", url="")))))
        validate_document(d)  # پیش‌نویس
        msgs = [m for _, m in errors_of(d, strict=True)]
        self.assertGreaterEqual(len(msgs), 4)


class SpecsTests(SimpleTestCase):
    def test_client_specs_expose_every_type_and_no_unknown_kinds(self):
        specs = schema.client_specs()
        self.assertEqual(set(specs["types"]), {"section", "row", "column", "heading", "text", "image", "button"})
        kinds = {f["kind"] for fields in specs["types"].values() for f in fields}
        self.assertLessEqual(kinds, {"text", "textarea", "color", "int", "choice", "bool", "media", "url"})

    def test_every_field_default_is_valid(self):
        for type_, fields in schema.SPECS.items():
            for f in fields:
                errs = schema._Collector()
                schema._validate_value(f, f.default, "x", errs)
                self.assertEqual(errs.errors, [], f"{type_}.{f.name}")

    def test_css_properties_are_allowlisted(self):
        allowed = {"border-width", "border-style", "border-color", "border-radius", "background-color", "background-image", "padding", "padding-block",
                   "margin-bottom", "color", "font-size", "text-align", "width", "gap", "align-items", "--bx-span", "--bx-c", "--bx-t"}
        used = {f.css for fields in schema.SPECS.values() for f in fields if f.css}
        self.assertLessEqual(used, allowed)

    def test_media_ids_collected(self):
        d = validate_document(document(section(row(col(el("image", media=7), el("image", media=9))), bg_media=3)))
        self.assertEqual(schema.media_ids(d), {3, 7, 9})
