import re
from html.parser import HTMLParser

from apps.builder.render import BASE_CSS, render_document

from .helpers import BuilderTestCase, col, document, el, row, section, simple_doc

ALLOWED_TAGS = {"div", "section", "style", "h2", "h3", "h4", "h5", "h6", "p", "br", "img", "a"}
ALLOWED_ATTRS = {"class", "href", "src", "alt", "width", "height", "loading", "decoding", "target", "rel"}


class _Collect(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.attrs = [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs += [k for k, _ in attrs]


def parse(html):
    c = _Collect()
    c.feed(html)
    return c


PAYLOADS = ['<script>alert(1)</script>', '"><img src=x onerror=alert(1)>', "</style><script>alert(1)</script>", "'; alert(1); '", "<svg onload=alert(1)>"]


class SafeRenderTests(BuilderTestCase):
    def assert_inert(self, html):
        self.assertNotIn("<script", html.lower())
        parsed = parse(html)
        self.assertLessEqual(set(parsed.tags), ALLOWED_TAGS)  # هیچ تگ/ویژگی غیرمجاز (script, svg, on*, style=…) ساخته نمی‌شود
        self.assertLessEqual(set(parsed.attrs), ALLOWED_ATTRS)
        self.assertEqual(html.lower().count("<style"), 1)  # فقط یک بلوک سبک خودِ سیستم
        self.assertEqual(html.lower().count("</style>"), 1)

    def test_all_text_fields_are_escaped(self):
        for payload in PAYLOADS:
            doc = document(section(row(col(
                el("heading", text=payload), el("text", text=payload), el("button", label=payload, url="/x/"),
                el("image", media=self.make_image().pk, alt=payload)))))
            html = render_document(doc)
            self.assertNotIn(payload, html)  # هرگز خام نیست؛ همیشه escape شده
            self.assert_inert(html)

    def test_text_newlines_become_paragraphs_and_br(self):
        html = render_document(document(section(row(col(el("text", text="الف\nب\n\nج"))))))
        self.assertIn("<p", html)
        self.assertIn("الف<br>ب", html)
        self.assertEqual(html.count('class="bx-core"'), 2)

    def test_javascript_urls_never_render(self):
        for url in ("javascript:alert(1)", "data:text/html;base64,AAAA", "//evil.example"):
            doc = document(section(row(col(el("button", url=url)))))
            self.assertEqual(render_document(doc), "")  # سند نامعتبر ← هیچ خروجی

    def test_invalid_document_renders_nothing_not_error(self):
        self.assertEqual(render_document({"schema_version": 1, "sections": [{"id": "abcd", "type": "script"}]}), "")
        self.assertEqual(render_document(None), "")
        self.assertEqual(render_document({"schema_version": 7}), "")

    def test_heading_level_tag_is_allowlisted(self):
        html = render_document(document(section(row(col(el("heading", level="4"))))))
        self.assertIn("<h4 ", html)
        self.assertNotIn("<h1", html)

    def test_button_attributes(self):
        html = render_document(document(section(row(col(el("button", url="https://ex.com/a?b=1&c=2", new_tab=True, variant="outline"))))))
        self.assertIn('href="https://ex.com/a?b=1&amp;c=2"', html)
        self.assertIn('target="_blank" rel="noopener noreferrer"', html)
        self.assertIn("bx-btn-outline", html)

    def test_image_uses_media_url_and_library_alt_fallback(self):
        m = self.make_image()
        html = render_document(document(section(row(col(el("image", media=m.pk, link_url="/go/"))))))
        self.assertIn(f'src="{m.file.url}"', html)
        self.assertIn('alt="متن جایگزین کتابخانه"', html)
        self.assertIn('width="40" height="30"', html)
        self.assertIn('<a href="/go/">', html)

    def test_missing_or_non_image_media_is_skipped(self):
        html = render_document(document(section(row(col(el("image", media=99999), el("image", media=None), el("heading", text="باقی می‌ماند"))))))
        self.assertNotIn("<img", html)
        self.assertIn("باقی می‌ماند", html)

    def test_css_declarations_come_only_from_validated_props(self):
        doc = document(section(
            row(col(el("heading", color="#D6334A", font_size=40, align="center", border_width=2, border_color="#000", radius=8, bg_color="#eee"), span=6), col(span=6), gap=10),
            bg_color="#112233", padding_y=20, text_color="#ffffff"))
        html = render_document(doc)
        css = html.split("<style>", 1)[1].split("</style>", 1)[0]
        for expected in ("background-color:#112233", "padding-block:20px", "color:#ffffff", "gap:10px", "font-size:40px", "color:#d6334a",
                         "text-align:center", "border-width:2px", "border-radius:8px", "--bx-span:6"):
            self.assertIn(expected, css)
        # هر قاعده‌ی تولیدشده فقط ویژگی/مقدارهای مجاز دارد
        for rule in css.split(BASE_CSS)[-1].splitlines():
            if rule.startswith(".bx-page .bx-n-"):
                self.assertRegex(rule, r"^\.bx-page \.bx-n-[a-z0-9]+(?: \.bx-core)?\{[a-z-]+:[#a-z0-9%\-\(\)\"/\._ ]+(;[a-z-]+:[#a-z0-9%\-\(\)\"/\._ ]+)*\}$")

    def test_background_image_url_from_media(self):
        m = self.make_image()
        html = render_document(document(section(row(col()), bg_media=m.pk)))
        self.assertIn(f'background-image:url("{m.file.url}")', html)

    def test_structure_classes_and_stacking(self):
        html = render_document(document(section(row(col(span=6), col(span=6), stack_at="tablet"), width="full")))
        for cls in ("bx-sec-full", "bx-stack-tablet", "bx-col"):
            self.assertIn(cls, html)
        html = render_document(document(section(row(col(), stack_at="never"))))
        self.assertNotIn("bx-stack-", html.split("</style>")[1])

    def test_output_has_no_inline_style_attribute_and_no_event_attrs(self):
        html = render_document(simple_doc())
        self.assertNotRegex(html.split("</style>", 1)[1], r'\sstyle\s*=')
        self.assertTrue(re.match(r'^<div class="bx-page"><style>', html))

    def test_render_is_deterministic(self):
        d = simple_doc()
        self.assertEqual(render_document(d), render_document(d))
