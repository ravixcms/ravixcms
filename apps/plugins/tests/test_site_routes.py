"""Plugin site routes (pages under the language prefix, or plain URLs), the private data folder and file downloads."""
import tempfile
from pathlib import Path

from django.test import Client, override_settings

from apps.plugins import services
from apps.plugins.manifest import ManifestError, parse_manifest

from .helpers import PluginTestCase, base_manifest

VIEWS = '''from django.http import HttpResponse


def home(request, api):
    return HttpResponse("HOME")


def page(request, api):
    return HttpResponse("PAGE:" + request.LANGUAGE_CODE)


def plain(request, api):
    return HttpResponse("PLAIN")


def boom(request, api):
    raise RuntimeError("boom")


def save(request, api):
    (api.data_dir() / "a.bin").write_bytes(b"0123456789")
    return HttpResponse("saved")


def get_file(request, api):
    return api.file_response(request, request.GET.get("p", "a.bin"), filename=request.GET.get("n", "ravix 1.0.zip"), content_type="application/zip")
'''


def manifest(**over):
    return base_manifest("demo", public_routes=[], site_routes=[
        {"path": "hello/", "view": "views:page"},
        {"path": "get/latest/", "view": "views:plain", "language_prefix": False},
        {"path": "boom/", "view": "views:boom"},
        {"path": "save/", "view": "views:save", "language_prefix": False},
        {"path": "file/", "view": "views:get_file", "language_prefix": False},
    ], **over)


class SiteRouteManifestTests(PluginTestCase):
    def parse(self, routes):
        return parse_manifest(base_manifest("demo", site_routes=routes), expected_slug="demo")

    def test_valid_routes_are_kept_with_defaults(self):
        m = self.parse([{"path": "download/", "view": "views:page"}])
        self.assertEqual(m.site_routes[0], {"path": "download/", "view": "views:page", "methods": ["GET"], "language_prefix": True})

    def test_reserved_first_segments_are_refused(self):
        for path in ("panel/x/", "api/", "blog/", "ext/demo/", "sitemap.xml", "install/", "shop/"):
            with self.assertRaises(ManifestError, msg=path):
                self.parse([{"path": path, "view": "views:page"}])

    def test_duplicates_unknown_keys_and_empty_paths_are_refused(self):
        with self.assertRaises(ManifestError):
            self.parse([{"path": "a/", "view": "views:page"}, {"path": "a/", "view": "views:page"}])
        with self.assertRaises(ManifestError):
            self.parse([{"path": "a/", "view": "views:page", "x": 1}])
        with self.assertRaises(ManifestError):
            self.parse([{"path": "", "view": "views:page"}])
        with self.assertRaises(ManifestError):
            self.parse([{"path": "a/", "view": "views:page", "language_prefix": "yes"}])

    def test_same_path_may_exist_with_and_without_prefix(self):
        m = self.parse([{"path": "a/", "view": "views:page"}, {"path": "a/", "view": "views:page", "language_prefix": False}])
        self.assertEqual(len(m.site_routes), 2)


class SiteRouteServingTests(PluginTestCase):
    def setUp(self):
        super().setUp()
        self.data = Path(tempfile.mkdtemp(prefix="ravix-plugin-data-"))
        self.addCleanup(__import__("shutil").rmtree, self.data, ignore_errors=True)
        ov = override_settings(RAVIX_PLUGIN_DATA_DIR=self.data, RAVIX_ACCEL_REDIRECT_PREFIX="")
        ov.enable()
        self.addCleanup(ov.disable)
        self.write_plugin("demo", manifest(), views=VIEWS)
        self.install_activate("demo")

    def test_localized_route_is_served_under_the_language_prefix(self):
        r = Client().get("/en/hello/")
        self.assertEqual((r.status_code, r.content), (200, b"PAGE:en"))
        r = Client().get("/de/hello/")
        self.assertEqual(r.content, b"PAGE:de")

    def test_unprefixed_url_of_a_localized_route_redirects_to_a_prefixed_one(self):
        r = Client().get("/hello/")
        self.assertEqual(r.status_code, 301)
        self.assertRegex(r["Location"], r"^/[a-z-]+/hello/$")

    def test_plain_route_has_no_prefix_and_is_not_reachable_with_one(self):
        self.assertEqual(Client().get("/get/latest/").content, b"PLAIN")
        self.assertEqual(Client().get("/en/get/latest/").status_code, 404)
        self.assertEqual(Client().get("/en/hello-nothing/").status_code, 404)

    def test_wrong_method_gets_405_and_unknown_paths_fall_through(self):
        self.assertEqual(Client().post("/en/hello/").status_code, 405)
        self.assertEqual(Client().get("/en/hello/extra/").status_code, 404)

    def test_head_is_answered_like_get(self):
        self.assertEqual(Client().head("/get/latest/").status_code, 200)
        self.assertEqual(Client().head("/en/hello/").status_code, 200)

    def test_inactive_plugin_serves_nothing(self):
        services.deactivate("demo", self.admin)
        self.invalidate()
        self.assertEqual(Client().get("/en/hello/").status_code, 404)
        self.assertEqual(Client().get("/get/latest/").status_code, 404)

    def test_a_failing_route_is_contained(self):
        r = Client().get("/en/boom/")
        self.assertEqual(r.status_code, 500)
        self.assertEqual(Client().get("/en/hello/").status_code, 200)  # the rest of the site is fine

    def test_core_pages_are_not_shadowed(self):
        self.assertEqual(Client().get("/en/blog/").status_code, 200)

    # ---- private files
    def test_data_dir_is_private_to_the_plugin_and_outside_media(self):
        Client().get("/save/")
        self.assertTrue((self.data / "demo" / "a.bin").is_file())

    def test_file_response_is_an_attachment_with_nosniff(self):
        Client().get("/save/")
        r = Client().get("/file/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b"".join(r.streaming_content), b"0123456789")
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertIn("attachment;", r["Content-Disposition"])
        self.assertIn('filename="ravix_1.0.zip"', r["Content-Disposition"])
        self.assertEqual(r["Content-Type"], "application/zip")

    def test_file_response_header_cannot_be_broken_by_the_name(self):
        Client().get("/save/")
        r = Client().get("/file/", {"n": 'a";\r\nX-Evil: 1.zip'})
        self.assertNotIn("X-Evil", r)
        self.assertNotIn("\n", r["Content-Disposition"])

    def test_file_response_refuses_paths_outside_the_plugin_folder(self):
        Client().get("/save/")
        (self.data / "secret.txt").write_text("x")
        for p in ("../secret.txt", "/etc/passwd", "missing.bin", ".."):
            self.assertEqual(Client().get("/file/", {"p": p}).status_code, 404, p)

    def test_file_response_refuses_symlinks(self):
        Client().get("/save/")
        (self.data / "outside.bin").write_bytes(b"x")
        (self.data / "demo" / "link.bin").symlink_to(self.data / "outside.bin")
        self.assertEqual(Client().get("/file/", {"p": "link.bin"}).status_code, 404)

    def test_accel_redirect_hands_the_file_to_nginx(self):
        Client().get("/save/")
        with override_settings(RAVIX_ACCEL_REDIRECT_PREFIX="/_plugin_files/"):
            r = Client().get("/file/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["X-Accel-Redirect"], "/_plugin_files/demo/a.bin")
        self.assertEqual(r.content, b"")
        self.assertIn("attachment;", r["Content-Disposition"])
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
