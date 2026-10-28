"""What visitors get: stored HTML, no JavaScript unless a video needs it, CSP frame-src only for the video host, and graceful behaviour when
Studio is off or a page is a draft."""
import re
from unittest import mock

from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse

from apps.core import modules
from apps.studio import services
from apps.studio.models import StudioVersion

from .helpers import requires_studio, StudioBase, column, document, section, simple_doc, widget


class PublicPageTests(StudioBase):
    def publish(self, doc, target=None):
        d = self.put_draft(doc, target)
        services.publish_draft(d.pk, self.admin)
        return d

    def get(self, path="/en/about/"):
        return Client().get(path)

    def test_the_published_layout_replaces_the_text_body(self):
        self.publish(simple_doc("Studio headline"))
        r = self.get()
        self.assertContains(r, "Studio headline")
        self.assertNotContains(r, "old text")

    def test_the_draft_is_never_public(self):
        self.put_draft(simple_doc("Secret draft"))
        r = self.get()
        self.assertNotContains(r, "Secret draft")
        self.assertContains(r, "old text")

    def test_a_pending_version_is_not_public(self):
        d = self.put_draft(simple_doc("Waiting"))
        services.submit_for_review(d.pk, self.admin)
        self.assertNotContains(self.get(), "Waiting")

    def test_unpublish_returns_to_the_text_content(self):
        d = self.publish(simple_doc("Gone soon"))
        services.unpublish(d.pk, self.admin)
        r = self.get()
        self.assertNotContains(r, "Gone soon")
        self.assertContains(r, "old text")

    def test_no_script_tags_and_no_editor_assets_on_a_plain_page(self):
        self.publish(simple_doc("x"))
        html = self.get().content.decode()
        self.assertNotIn("studio/front.js", html)
        self.assertNotIn("sortable", html.lower())
        self.assertNotIn("preview.js", html)
        self.assertNotRegex(html, r"jquery")
        self.assertNotRegex(re.sub(r'<script[^>]*src="[^"]+"[^>]*></script>', "", html), r"<script(?![^>]*type=\"application/(ld\+)?json\")")

    def test_the_title_and_template_options_are_honoured(self):
        self.publish(document(section(column(widget("heading", text="Hello"))), show_title=False, template="full_width"))
        html = self.get().content.decode()
        self.assertIn("Hello", html)
        self.assertNotRegex(html, r"<h1(?![^>]*sr-only)[^>]*>\s*About\s*</h1>")  # the title stays for screen readers only
        self.assertRegex(html, r'<h1 class="sr-only">About</h1>')

    def test_a_post_uses_the_layout_too(self):
        self.post.status = "published"
        self.post.save()
        d = self.put_draft(simple_doc("Post design"), self.post)
        services.publish_draft(d.pk, self.admin)
        self.assertContains(self.get("/en/blog/mine/"), "Post design")
        # the list/feed excerpt never contains the layout's CSS
        self.assertNotContains(self.get("/en/blog/"), "<style>.st-root")

    def test_csp_allows_a_frame_host_only_on_pages_with_a_video(self):
        self.publish(simple_doc("no video"))
        self.assertNotIn("frame-src", self.get()["Content-Security-Policy"])
        with mock.patch("apps.studio.video._http_get", side_effect=OSError("offline")):
            self._csp_video()

    def _csp_video(self):
        doc = document(section(column(widget("video", source="https://www.youtube.com/watch?v=dQw4w9WgXcQ"))))
        self.publish(doc)
        r = self.get()
        csp = r["Content-Security-Policy"]
        self.assertIn("frame-src 'self' https://www.youtube-nocookie.com", csp)
        self.assertNotIn("youtube.com ", csp.replace("youtube-nocookie.com", ""))
        self.assertContains(r, "studio/front.js")
        self.assertContains(r, 'data-st-video="youtube:dQw4w9WgXcQ"')
        self.assertNotRegex(r.content.decode(), r"<iframe")  # the player does not exist until the visitor clicks

    def test_large_css_moves_to_a_hashed_immutable_file(self):
        sections = [section(column(widget("heading", text=f"H{i}", style={"desktop": {"margin_top": i % 90, "padding_top": i % 70, "radius": i % 50, "color": "#%06x" % (i * 977 % 0xFFFFFF)}})), style={"desktop": {"bg_color": "#%06x" % (i * 4099 % 0xFFFFFF)}}) for i in range(40)]
        d = self.publish(document(*sections))
        v = d.published_version if d.published_version_id else StudioVersion.objects.get(document=d, number=1)
        d.refresh_from_db()
        v = d.published_version
        self.assertGreater(len(v.css.encode()), 0)
        with override_settings(STUDIO_INLINE_CSS_MAX=1000):
            html = self.get().content.decode()
            m = re.search(r'<link rel="stylesheet" href="(/studio-assets/css/[0-9a-f]+\.css)"', html)
            self.assertIsNotNone(m, "the stylesheet should be linked, not inlined")
            self.assertNotIn(".st-n-", html)
            css = Client().get(m.group(1))
            self.assertEqual(css.status_code, 200)
            self.assertEqual(css["Content-Type"].split(";")[0], "text/css")
            self.assertIn("immutable", css["Cache-Control"])
            self.assertIn("max-age=31536000", css["Cache-Control"])
            self.assertEqual(css["X-Content-Type-Options"], "nosniff")
            self.assertEqual(css.content.decode(), v.css)
        self.assertIn("<style>", self.get().content.decode())  # under the limit again: inline

    def test_the_css_file_endpoint_is_strict(self):
        for path in ("/studio-assets/css/zzzz.css", "/studio-assets/css/" + "a" * 64 + ".css", "/studio-assets/css/../../x.css"):
            self.assertEqual(Client().get(path).status_code, 404, path)

    def test_a_typical_page_stays_far_below_the_inline_css_limit(self):
        d = self.publish(document(
            section(column(widget("heading", text="Hero"), widget("text", content="x"), widget("button", label="Go", url="/x/")), style={"desktop": {"bg_color": "#112233", "padding_top": 80, "padding_bottom": 80}}),
            section(column(widget("card", title="a", text="b")), column(widget("card", title="c", text="d")), column(widget("card", title="e", text="f"))),
            section(column(widget("icon_list", items=[{"text": "one", "icon": ""}, {"text": "two", "icon": ""}])), column(widget("quote", text="q", cite="c"))),
            section(column(widget("gallery", images=[]), widget("divider"), widget("spacer"))),
        ))
        d.refresh_from_db()
        self.assertLess(len(d.published_version.css.encode()), 20_000)
        self.assertLess(len(d.published_version.html.encode()), 40_000)


class WhenStudioIsOffTests(StudioBase):
    def setUp(self):
        super().setUp()
        d = self.put_draft(simple_doc("Still here"))
        services.publish_draft(d.pk, self.admin)

    @override_settings(BUILTIN_MODULES_DEFAULT_ON=False)
    def test_pages_keep_showing_when_the_module_is_switched_off(self):
        self.assertFalse(modules.is_enabled("studio"))
        self.assertContains(Client().get("/en/about/"), "Still here")

    @override_settings(STUDIO_ENABLED=False)
    def test_pages_keep_showing_when_the_server_lock_is_on(self):
        self.assertFalse(modules.is_installed("studio"))
        self.assertContains(Client().get("/en/about/"), "Still here")
        c = Client()
        c.force_login(self.admin)
        self.assertEqual(c.get(reverse("studio:list")).status_code, 404)

    @override_settings(STUDIO_ENABLED=False)
    def test_the_stored_page_is_not_rebuilt_while_off(self):
        d = self.document_for()
        v = d.published_version
        StudioVersion.objects.filter(pk=v.pk).update(renderer_version=0)
        self.assertContains(Client().get("/en/about/"), "Still here")
        v.refresh_from_db()
        self.assertEqual(v.renderer_version, 0)  # off: nothing is re-drawn, the stored HTML is simply served

    @override_settings(BUILTIN_MODULES_DEFAULT_ON=False)
    @requires_studio
    def test_the_module_card_lists_studio_and_can_be_switched(self):
        from apps.core.modules import get_module

        self.assertEqual(get_module("studio").name, "Ravix Studio")
        modules.set_enabled("studio", True)
        self.assertTrue(modules.is_enabled("studio"))
        modules.set_enabled("studio", False)
        self.assertFalse(modules.is_enabled("studio"))

    @override_settings(STUDIO_ENABLED=False)
    def test_the_server_lock_refuses_to_enable(self):
        with self.assertRaises(ValueError):
            modules.set_enabled("studio", True)
