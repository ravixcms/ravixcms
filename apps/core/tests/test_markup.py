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


class AutoDirectionTests(SimpleTestCase):
    def test_text_blocks_get_dir_auto(self):
        html = render_markdown("## عنوان\n\nمتن فارسی\n\n- یک\n- دو\n\n> نقل\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```\ncode\n```")
        for tag in ("h2", "p", "ul", "blockquote", "table"):
            self.assertIn(f'<{tag} dir="auto"', html, tag)
        self.assertNotIn('<pre dir', html)  # code stays left-to-right (CSS)

    def test_user_cannot_set_direction_themselves_to_break_layout(self):
        self.assertNotIn('dir="rtl"', render_markdown('<p dir="rtl">x</p>'))
