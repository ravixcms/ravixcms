"""Rendering: escaping, per-device CSS, only-used widget CSS, images, icons, video, determinism."""
import re

from django.test import TestCase

from apps.studio.css import assert_safe_css, scope_plugin_css
from apps.studio.render import BASE_CSS, render_document

from .helpers import StudioTestCase, column, document, section, simple_doc, widget


def render(doc, **kw):
    return render_document(doc, **kw)


class OutputTests(TestCase):
    def test_text_is_escaped_everywhere(self):
        evil = '<script>alert(1)</script>"\'&'
        doc = document(section(column(
            widget("heading", text=evil, link_url="/x/"), widget("button", label=evil, url="/x/"), widget("quote", text=evil, cite=evil),
            widget("card", title=evil, text=evil, button_label=evil, button_url="/x/"), widget("icon_list", items=[{"text": evil, "icon": ""}]),
        )))
        out = render(doc)
        self.assertNotIn("<script>alert", out.html)
        self.assertIn("&lt;script&gt;", out.html)

    def test_the_output_has_no_event_handlers_or_inline_scripts(self):
        out = render(simple_doc("x"))
        self.assertFalse(re.search(r"\son[a-z]+\s*=", out.html))
        self.assertNotIn("<script", out.html)
        self.assertNotIn("javascript:", out.html)

    def test_markdown_is_sanitised_at_render_time_too(self):
        doc = simple_doc()
        doc["sections"][0]["columns"][0]["widgets"] = [widget("text", content="[x](javascript:alert(1)) **b**")]
        html = render(doc, validate=False).html
        self.assertNotIn("javascript:", html)
        self.assertIn("<strong>b</strong>", html)

    def test_deterministic(self):
        d = simple_doc("same")
        self.assertEqual(render(d).html, render(d).html)
        self.assertEqual(render(d).css, render(d).css)

    def test_invalid_document_renders_empty_not_an_error(self):
        out = render({"schema_version": 2, "sections": [{"bad": 1}]})
        self.assertEqual(out.html, "")

    def test_structure_classes(self):
        out = render(document(section(column(widget("heading")), column(), content_width="full", anchor="intro")))
        self.assertIn('class="st-sec st-full st-n-', out.html)
        self.assertIn('id="intro"', out.html)
        self.assertEqual(out.html.count('class="st-col st-n-'), 2)


class CssTests(TestCase):
    def test_only_the_used_widgets_css_is_included(self):
        out = render(simple_doc("x"))
        self.assertIn(".st-w-heading", out.css)
        self.assertNotIn(".st-btn{", out.css)
        self.assertNotIn(".st-gallery", out.css)
        self.assertNotIn(".st-video", out.css)
        out2 = render(document(section(column(widget("button")))))
        self.assertIn(".st-btn{", out2.css)
        self.assertEqual(out2.widgets, ["button"])

    def test_devices_become_media_queries_desktop_first(self):
        w = widget("heading", style={"desktop": {"margin_top": 10, "color": "#111111"}, "tablet": {"margin_top": 20}, "mobile": {"margin_top": 30, "hidden": True}})
        css = render(document(section(column(w)))).css
        n = w["id"]
        self.assertIn(f".st-n-{n}{{margin-block-start:10px}}", css)
        self.assertIn(f".st-n-{n} .st-core{{color:#111111}}", css)
        self.assertLess(css.index("margin-block-start:10px"), css.index("@media (max-width:1023px){"))
        self.assertIn(f"@media (max-width:1023px){{.st-n-{n}{{margin-block-start:20px}}}}", css)
        self.assertIn(f"@media (max-width:767px){{.st-n-{n}{{margin-block-start:30px}}}}", css)
        self.assertIn(f"@media (max-width:767px){{.st-n-{n}{{display:none!important}}}}", css)  # hidden on mobile only

    def test_hidden_on_each_size(self):
        w = widget("heading", style={"desktop": {"hidden": True}, "tablet": {"hidden": True}})
        css = render(document(section(column(w)))).css
        self.assertIn("@media (min-width:1024px){", css)
        self.assertIn("@media (min-width:768px) and (max-width:1023px){", css)

    def test_preview_keeps_hidden_nodes_selectable(self):
        w = widget("heading", style={"mobile": {"hidden": True}})
        css = render(document(section(column(w))), preview=True).css
        self.assertNotIn("display:none", css)
        self.assertIn("opacity:.35", css)

    def test_column_spans_per_device(self):
        c = column(widget("heading"), style={"desktop": {"span": 4}, "tablet": {"span": 6}, "mobile": {"span": 12}})
        css = render(document(section(c, column(), column()))).css
        self.assertIn(f".st-n-{c['id']}{{grid-column:span 4}}", css)
        self.assertIn("grid-column:span 6", css)
        self.assertIn("grid-column:span 12", css)

    def test_background_gradient_and_image(self):
        w = widget("heading", style={"desktop": {"bg_grad_from": "#000000", "bg_grad_to": "#ffffff", "bg_grad_angle": 90, "shadow": "md"}})
        css = render(document(section(column(w)))).css
        self.assertIn("background-image:linear-gradient(90deg,#000000,#ffffff)", css)
        self.assertIn("box-shadow:", css)

    def test_generated_css_passes_the_safety_check(self):
        doc = document(section(column(widget("heading", style={"desktop": {"margin_top": -20, "radius": 12, "border_width": 2, "border_color": "#ff0000"}}))))
        assert_safe_css(render(doc).css[len(BASE_CSS):])

    def test_safety_check_rejects_injection(self):
        for bad in ("a{color:red}</style><script>", "a{background:url(javascript:x)}", "@import url(x);a{}", "a{b:expression(1)}", "a{color:red", "a{x:y\\9}"):
            with self.assertRaises(ValueError):
                assert_safe_css(bad)

    def test_plugin_css_is_scoped_and_stripped(self):
        css = scope_plugin_css("h2,.x{color:#123;background:url(http://e/x)}body{color:red}@import 'x';.y{margin:0}", "st-w-p-w")
        self.assertIn(".st-w-p-w h2,.st-w-p-w .x{color:#123}", css)
        self.assertNotIn("url(", css)
        self.assertNotIn("@import", css)
        self.assertIn(".st-w-p-w body{color:red}", css)  # forced under the widget's own class, so it cannot reach the page body
        self.assertNotIn(".y{margin", css)  # the broken @import rule took its neighbour with it: dropped, never "repaired"


class MediaRenderTests(StudioTestCase):
    def test_image_has_dimensions_lazy_and_library_alt(self):
        m = self.make_image(size=(40, 30))
        doc = document(section(column(widget("heading"), widget("image", media=m.pk), widget("image", media=m.pk))))
        html = render(doc).html
        self.assertIn('width="40" height="30"', html)
        self.assertIn('loading="lazy"', html)
        self.assertIn('alt="library alt"', html)
        self.assertIn('decoding="async"', html)

    def test_the_first_image_of_the_page_is_not_lazy(self):
        a, b = self.make_image("a.png"), self.make_image("b.png")
        html = render(document(section(column(widget("image", media=a.pk), widget("image", media=b.pk))))).html
        imgs = re.findall(r"<img [^>]*>", html)
        self.assertIn('loading="eager" fetchpriority="high"', imgs[0])
        self.assertIn('loading="lazy"', imgs[1])

    def test_a_missing_image_renders_nothing(self):
        html = render(document(section(column(widget("image", media=99999))))).html
        self.assertNotIn("<img", html)

    def test_srcset_is_built_from_renditions(self):
        m = self.make_image("big.png", size=(1200, 600))
        out = render(document(section(column(widget("image", media=m.pk)))), make_files=True)
        self.assertIn("srcset=", out.html)
        self.assertIn("480w", out.html)
        self.assertIn("960w", out.html)
        self.assertIn("1200w", out.html)
        self.assertIn('sizes="', out.html)
        self.assertEqual(out.media, [m.pk])
        # not made while previewing / without make_files, but reused when they exist
        again = render(document(section(column(widget("image", media=m.pk)))), make_files=False)
        self.assertIn("480w", again.html)

    def test_gallery_skips_missing_images(self):
        m = self.make_image()
        html = render(document(section(column(widget("gallery", images=[{"media": m.pk, "alt": ""}, {"media": 99999, "alt": ""}]))))).html
        self.assertEqual(html.count("<li>"), 1)

    def test_icons_are_shared_through_one_sprite(self):
        html = render(document(section(column(widget("icon", icon="star"), widget("icon", icon="star"), widget("icon_list", items=[{"text": "a", "icon": "check"}]))))).html
        self.assertEqual(html.count('<symbol id="st-i-star"'), 1)
        self.assertEqual(html.count('href="#st-i-star"'), 2)
        self.assertIn('href="#st-i-check"', html)
        self.assertNotIn("<script", html)

    def test_preview_adds_selection_hooks_only_in_preview(self):
        doc = simple_doc()
        self.assertNotIn("data-st-id", render(doc).html)
        pv = render(doc, preview=True).html
        self.assertIn('data-st-id="', pv)
        self.assertIn('data-st-kind="widget"', pv)
        self.assertIn('data-st-list="sections"', pv)

    def test_empty_columns_get_a_drop_zone_only_in_preview(self):
        doc = document(section(column()))
        self.assertNotIn("st-empty", render(doc).html)
        self.assertIn("st-empty", render(doc, preview=True).html)
