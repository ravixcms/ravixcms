"""Video: lite embeds (no provider request before a click), local cover fetched once, safe fixed hosts, MP4 from the media library."""
import io
import json
import re
import urllib.error
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image

from apps.core.testing import png_bytes
from apps.media_library.models import Media
from apps.studio import video
from apps.studio.render import render_document

from .helpers import StudioTestCase, column, document, section, widget

YT = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
REAL_GET = video._http_get  # the real fetcher, before the tests replace it


def jpeg(size=(1280, 720)):
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 90, 160)).save(buf, "JPEG")
    return buf.getvalue()


def vid_doc(**props):
    return document(section(column(widget("video", **props))))


class LiteEmbedTests(StudioTestCase):
    def setUp(self):
        self.calls = []

        def fake_get(url):
            self.calls.append(url)
            if "ytimg.com" in url:
                return jpeg()
            if "vimeo.com/api/oembed" in url:
                return json.dumps({"thumbnail_url": "https://i.vimeocdn.com/video/1_640.jpg"}).encode()
            if "vimeocdn.com" in url:
                return jpeg()
            raise urllib.error.URLError("unexpected " + url)

        p = mock.patch.object(video, "_http_get", side_effect=fake_get)
        p.start()
        self.addCleanup(p.stop)
        for name in ("studio/posters/youtube-dQw4w9WgXcQ.webp", "studio/posters/vimeo-12345678.webp"):
            if default_storage.exists(name):
                default_storage.delete(name)

    def render(self, doc, **kw):
        return render_document(doc, make_files=True, **kw)

    def test_the_page_holds_a_cover_and_a_link_not_an_iframe(self):
        out = self.render(vid_doc(source=YT, title="Intro"))
        self.assertNotIn("<iframe", out.html)
        self.assertIn('data-st-video="youtube:dQw4w9WgXcQ"', out.html)
        self.assertIn('class="st-video-btn"', out.html)
        self.assertIn('aria-label="Intro"', out.html)
        self.assertIn("https://www.youtube.com/watch?v=dQw4w9WgXcQ", out.html)  # without JavaScript the link opens the provider
        self.assertNotRegex(out.html, r'src="https?://(?!testserver)[^"]*(youtube|ytimg|vimeo|aparat)')  # no provider asset is loaded by the page
        self.assertEqual(out.scripts, ["video"])
        self.assertEqual(out.frame_hosts, ["www.youtube-nocookie.com"])

    def test_the_cover_is_fetched_once_stored_locally_and_reused(self):
        out = self.render(vid_doc(source=YT))
        m = re.search(r'<img src="([^"]+)" alt="" width="(\d+)" height="(\d+)"', out.html)
        self.assertIsNotNone(m)
        self.assertIn("/studio/posters/youtube-dQw4w9WgXcQ.webp", m.group(1))
        self.assertEqual((m.group(2), m.group(3)), ("960", "540"))  # scaled down, ratio kept
        self.assertTrue(default_storage.exists("studio/posters/youtube-dQw4w9WgXcQ.webp"))
        first = len(self.calls)
        self.render(vid_doc(source=YT))
        self.assertEqual(len(self.calls), first)  # not fetched again

    def test_preview_and_stored_pages_never_hit_the_network(self):
        render_document(vid_doc(source=YT), make_files=False)
        self.assertEqual(self.calls, [])

    def test_a_failed_fetch_gives_a_plain_background_and_play_button(self):
        self.calls.clear()
        with mock.patch.object(video, "_http_get", side_effect=urllib.error.URLError("down")):
            out = self.render(vid_doc(source="https://youtu.be/AAAAAAAAAAA"))
        self.assertIn("st-video-play", out.html)
        self.assertNotIn("<img", out.html.split('class="st-video-btn"')[1].split("</a>")[0])
        self.assertFalse(default_storage.exists("studio/posters/youtube-AAAAAAAAAAA.webp"))

    def test_a_garbage_response_is_not_stored(self):
        with mock.patch.object(video, "_http_get", return_value=b"<html>not an image</html>"):
            out = self.render(vid_doc(source="https://youtu.be/BBBBBBBBBBB"))
        self.assertNotIn("<img", out.html.split('class="st-video-btn"')[1].split("</a>")[0])
        self.assertFalse(default_storage.exists("studio/posters/youtube-BBBBBBBBBBB.webp"))

    def test_vimeo_uses_its_oembed_thumbnail_and_the_dnt_host(self):
        out = self.render(vid_doc(source="https://vimeo.com/12345678"))
        self.assertIn('data-st-video="vimeo:12345678"', out.html)
        self.assertEqual(out.frame_hosts, ["player.vimeo.com"])
        self.assertTrue(default_storage.exists("studio/posters/vimeo-12345678.webp"))
        self.assertTrue(all(u.startswith("https://") for u in self.calls))

    def test_aparat_is_supported_with_a_fixed_host(self):
        out = self.render(vid_doc(source="https://www.aparat.com/v/abc123"))
        self.assertIn('data-st-video="aparat:abc123"', out.html)
        self.assertEqual(out.frame_hosts, ["www.aparat.com"])

    def test_unknown_links_render_nothing(self):
        for src in ("https://evil.example/watch?v=dQw4w9WgXcQ", "javascript:alert(1)", "https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ", ""):
            self.assertNotIn("st-video", render_document(vid_doc(source=src), validate=False).html.replace("st-video-", ""), src)

    def test_an_own_cover_image_wins_and_nothing_is_fetched(self):
        m = self.make_image("cover.png", size=(64, 36))
        self.calls.clear()
        out = self.render(vid_doc(source=YT, poster=m.pk))
        self.assertIn(m.file.url, out.html)
        self.assertEqual(self.calls, [])
        self.assertIn(m.pk, out.media)  # the page is rebuilt if that image changes

    def test_the_id_is_checked_before_any_request(self):
        self.assertIsNone(video.poster("youtube", "../../etc/passwd", fetch=True))
        self.assertIsNone(video.poster("vimeo", "abc", fetch=True))
        self.assertEqual(self.calls, [])

    def test_the_fetcher_itself_allows_only_https_and_no_redirects(self):
        with self.assertRaises(ValueError):
            REAL_GET("http://example.com/x")
        handler = video._NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "https://evil/"))


class FileVideoTests(StudioTestCase):
    def make_video(self):
        m = Media(original_name="clip.mp4", kind=Media.Kind.VIDEO, mime_type="video/mp4", title="Clip")
        m.file.save("clip.mp4", ContentFile(b"\x00\x00\x00\x18ftypmp42" + b"0" * 64), save=True)
        return m

    def test_an_mp4_from_the_library_is_a_video_element_that_preloads_nothing(self):
        m = self.make_video()
        out = render_document(vid_doc(mode="file", file=m.pk, title="Clip"))
        self.assertIn("<video", out.html)
        self.assertIn('preload="none"', out.html)
        self.assertIn(m.file.url, out.html)
        self.assertEqual(out.scripts, [])  # no JavaScript is needed
        self.assertEqual(out.frame_hosts, [])  # and no CSP exception

    def test_a_non_video_media_is_ignored(self):
        img = self.make_image("not-video.png")
        out = render_document(vid_doc(mode="file", file=img.pk), validate=False)
        self.assertNotIn("<video", out.html)
