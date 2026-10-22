from django.test import SimpleTestCase

from apps.core.markup import render_markdown


class MarkdownSanitizingTests(SimpleTestCase):
    def test_basic_markdown_rendered(self):
        html = render_markdown("**پررنگ** و [پیوند](https://example.com)")
        self.assertIn("<strong>پررنگ</strong>", html)
        self.assertIn('href="https://example.com"', html)
        self.assertIn("noopener", html)

    def test_script_tags_removed(self):
        html = render_markdown("سلام <script>alert(1)</script> دنیا")
        self.assertNotIn("<script", html)
        self.assertNotIn("alert(1)</script>", html)

    def test_event_handlers_removed(self):
        html = render_markdown('<img src="https://e.com/a.png" onerror="alert(1)" onload="x()">')
        self.assertNotIn("onerror", html)
        self.assertNotIn("onload", html)

    def test_dangerous_url_schemes_removed(self):
        for md in ("[x](javascript:alert(1))", "[x](data:text/html;base64,PHNjcmlwdD4=)", "![x](javascript:alert(1))",
                   '<a href="JaVaScRiPt:alert(1)">x</a>', '<a href="vbscript:msgbox(1)">x</a>'):
            html = render_markdown(md).lower()
            self.assertNotIn("javascript:", html, md)
            self.assertNotIn("data:text", html, md)
            self.assertNotIn("vbscript:", html, md)

    def test_iframe_style_form_removed(self):
        html = render_markdown('<iframe src="https://evil.com"></iframe><form action="/x"><input></form><style>*{}</style>')
        for tag in ("<iframe", "<form", "<input", "<style"):
            self.assertNotIn(tag, html)

    def test_empty(self):
        self.assertEqual(render_markdown(""), "")
        self.assertEqual(render_markdown(None), "")
