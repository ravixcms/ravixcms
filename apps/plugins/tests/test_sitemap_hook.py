"""The ``public.sitemap`` hook: an active plugin adds the public pages it serves to each language's sitemap."""
import datetime
import xml.etree.ElementTree as ET

from django.test import Client

from apps.plugins import services

from .helpers import PluginTestCase, base_manifest

NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9", "x": "http://www.w3.org/1999/xhtml"}

VIEWS = '''import datetime
from django.http import HttpResponse


def home(request, api):
    return HttpResponse("home")


def page(request, api):
    return HttpResponse("ok")


def smap(entries, api, language, request):
    return [
        {"path": f"/{language}/gallery/", "lastmod": datetime.date(2026, 5, 17)},
        {"path": f"/{language}/gallery/solo/", "lastmod": "2026-06-01T10:00:00", "alternates": False},
        {"path": "/xx/gallery/"},               # another language's path: dropped
        {"path": f"/{language}/gallery/?q=1"},  # a query string: dropped
        {"path": "https://evil.example/x"},     # not a local path: dropped
        {"path": f"/{language}/gallery/", "lastmod": "2020-01-01"},  # duplicate: dropped
        {"path": f"/{language}/bad-date/", "lastmod": "yesterday"},
        "not a dict",
    ]


def broken(entries, api, language, request):
    raise RuntimeError("boom")
'''


def sitemap(client, lang):
    root = ET.fromstring(client.get(f"/{lang}/sitemap.xml").content)
    return {u.findtext("s:loc", namespaces=NS): u for u in root.findall("s:url", NS)}


class SitemapHookTests(PluginTestCase):
    def install_demo(self, hooks=None):
        manifest = base_manifest("demo", public_routes=[], site_routes=[{"path": "gallery/", "view": "views:page", "language_prefix": True}],
                                 hooks=hooks if hooks is not None else [{"name": "public.sitemap", "handler": "views:smap"}])
        self.write_plugin("demo", manifest, views=VIEWS)
        self.install_activate("demo")

    def test_pages_of_an_active_plugin_are_in_every_language_sitemap(self):
        self.install_demo()
        for lang in ("en", "fa", "de", "zh-hans"):
            urls = sitemap(Client(), lang)
            loc = f"http://testserver/{lang}/gallery/"
            self.assertIn(loc, urls, lang)
            self.assertEqual(urls[loc].findtext("s:lastmod", namespaces=NS), "2026-05-17")
            self.assertTrue(all(u.startswith(f"http://testserver/{lang}/") for u in urls), lang)

    def test_hreflang_alternates_follow_the_entry(self):
        self.install_demo()
        urls = sitemap(Client(), "de")
        links = {l.get("hreflang"): l.get("href") for l in urls["http://testserver/de/gallery/"].findall("x:link", NS)}
        self.assertEqual(len(links), 11)  # ten languages and x-default
        self.assertEqual(links["en"], "http://testserver/en/gallery/")
        self.assertEqual(links["x-default"], "http://testserver/fa/gallery/")
        solo = urls["http://testserver/de/gallery/solo/"]
        self.assertEqual(solo.findall("x:link", NS), [])  # alternates: false
        self.assertEqual(solo.findtext("s:lastmod", namespaces=NS), "2026-06-01")  # a datetime string keeps its date

    def test_unsafe_duplicate_or_foreign_entries_are_dropped(self):
        self.install_demo()
        urls = sitemap(Client(), "en")
        extra = sorted(u for u in urls if "gallery" in u or "evil" in u)
        self.assertEqual(extra, ["http://testserver/en/gallery/", "http://testserver/en/gallery/solo/"])
        self.assertEqual(urls["http://testserver/en/gallery/"].findtext("s:lastmod", namespaces=NS), "2026-05-17")  # the first one wins

    def test_an_invalid_date_only_drops_the_date(self):
        self.install_demo()
        bad = sitemap(Client(), "en")["http://testserver/en/bad-date/"]
        self.assertIsNone(bad.find("s:lastmod", NS))

    def test_a_disabled_plugin_adds_nothing(self):
        self.install_demo()
        services.deactivate("demo", self.admin)
        self.assertNotIn("http://testserver/en/gallery/", sitemap(Client(), "en"))

    def test_a_plugin_without_the_hook_adds_nothing(self):
        self.install_demo(hooks=[])
        self.assertNotIn("http://testserver/en/gallery/", sitemap(Client(), "en"))

    def test_a_failing_hook_does_not_break_the_sitemap(self):
        self.install_demo(hooks=[{"name": "public.sitemap", "handler": "views:broken"}])
        r = Client().get("/en/sitemap.xml")
        self.assertEqual(r.status_code, 200)
        self.assertIn("http://testserver/en/", sitemap(Client(), "en"))
