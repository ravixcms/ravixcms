"""``languages`` (the language switcher data): the same page in another language when it exists there, the blog home for a post, home for a separated page."""
from django.test import RequestFactory, TestCase
from django.urls import resolve
from django.utils import translation

from apps.core.models import SiteSettings
from apps.public.builders import language_links


def links_for(path):
    request = RequestFactory().get(path)
    code = path.split("/")[1]
    with translation.override(code):
        request.resolver_match = resolve(path)
        return {l.code: l for l in language_links(request)}


class LanguageLinksTests(TestCase):
    def setUp(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={"content_per_language": True})

    def test_ten_languages_with_native_names(self):
        links = links_for("/en/")
        self.assertEqual(list(links), ["fa", "en", "ar", "tr", "es", "fr", "de", "ru", "pt-br", "zh-hans"])
        self.assertEqual((links["pt-br"].name, links["pt-br"].short_name, links["pt-br"].hreflang), ("Português (Brasil)", "Português", "pt-BR"))
        self.assertEqual((links["fa"].name, links["zh-hans"].name, links["ar"].dir, links["en"].dir), ("فارسی", "简体中文", "rtl", "ltr"))
        self.assertTrue(links["en"].current)
        self.assertFalse(links["fa"].current)

    def test_home_blog_and_search_keep_their_page(self):
        self.assertEqual(links_for("/en/")["de"].url, "/de/")
        self.assertEqual(links_for("/en/blog/")["pt-br"].url, "/pt-br/blog/")
        self.assertEqual(links_for("/ar/search/")["fa"].url, "/fa/search/")

    def test_post_category_and_tag_go_to_the_blog_of_the_new_language(self):
        for path in ("/en/blog/some-post/", "/en/category/x/", "/en/tag/y/"):
            links = links_for(path)
            self.assertEqual((links["fa"].url, links["zh-hans"].url, links["en"].url), ("/fa/blog/", "/zh-hans/blog/", "/en/blog/"), path)

    def test_static_page_goes_home_when_languages_are_separated_else_keeps_its_page(self):
        self.assertEqual(links_for("/en/about/")["de"].url, "/de/")
        SiteSettings.objects.filter(pk=1).update(content_per_language=False)
        self.assertEqual(links_for("/en/about/")["de"].url, "/de/about/")

    def test_unprefixed_address_keeps_its_path(self):
        request = RequestFactory().get("/ext/x/page/")
        self.assertEqual({l.url for l in language_links(request)}, {"/ext/x/page/"})


class ThemeContextTests(TestCase):
    def test_every_theme_page_gets_the_languages(self):
        from apps.public import rendering

        request = RequestFactory().get("/en/blog/")
        with translation.override("en"):
            request.resolver_match = resolve("/en/blog/")
            context = rendering.base_context(request, rendering.resolve(request))
        self.assertEqual([l.code for l in context["languages"]][:3], ["fa", "en", "ar"])
        self.assertEqual({l.code: l.url for l in context["languages"]}["de"], "/de/blog/")
