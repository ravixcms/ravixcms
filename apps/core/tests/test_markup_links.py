"""Links in rendered Markdown: links to the site itself are not nofollow; every other link keeps noopener noreferrer nofollow."""
import importlib

from django.test import SimpleTestCase, TestCase, override_settings

from apps.core.markup import is_internal_href, own_hosts, render_markdown

EXTERNAL = 'rel="noopener noreferrer nofollow"'
INTERNAL = 'rel="noopener noreferrer"'


@override_settings(SITE_URL="https://ravixcms.com", ALLOWED_HOSTS=["ravixcms.com", "www.ravixcms.com", "*.wild.example", ".dot.example", "testserver"])
class RelTests(SimpleTestCase):
    def link(self, href):
        return render_markdown(f"[x]({href})")

    def test_relative_links_are_internal(self):
        for href in ("/download/latest/", "#section", "../up/", "page/", "?q=1", "/a?b=1&c=2#d"):
            html = self.link(href)
            self.assertIn(INTERNAL, html, href)
            self.assertNotIn("nofollow", html, href)

    def test_absolute_links_to_the_site_are_internal_with_and_without_www_and_scheme_relative(self):
        for href in ("https://ravixcms.com/download/latest/", "http://ravixcms.com/", "https://www.ravixcms.com/x", "https://RAVIXCMS.com:8443/x", "//ravixcms.com/p"):
            self.assertNotIn("nofollow", self.link(href), href)

    def test_other_sites_keep_nofollow(self):
        for href in ("https://example.com/", "https://ravixcms.com.evil.example/", "https://notravixcms.com/", "http://sub.ravixcms.com/", "//example.com/x",
                     "https://x.wild.example/", "https://dot.example/", "mailto:a@b.example"):
            html = self.link(href)
            self.assertIn(EXTERNAL, html, href)

    def test_wildcard_allowed_hosts_do_not_make_everything_internal(self):
        self.assertEqual(own_hosts(), {"ravixcms.com", "testserver"})

    def test_a_look_alike_user_info_trick_is_external(self):
        self.assertFalse(is_internal_href("https://ravixcms.com@evil.example/"))
        self.assertIn(EXTERNAL, self.link("https://ravixcms.com@evil.example/"))

    def test_sanitizing_is_unchanged(self):
        for href in ("javascript:alert(1)", "data:text/html;base64,PHNjcmlwdD4=", "JaVaScRiPt:alert(1)", " javascript:alert(1)"):
            html = render_markdown(f"[x]({href})")
            self.assertNotIn("javascript", html.lower(), href)
            self.assertNotIn("data:", html.lower(), href)
        html = render_markdown('<a href="/ok" onclick="x()" target="_blank">k</a> <script>alert(1)</script>')
        self.assertNotIn("onclick", html)
        self.assertNotIn("<script", html)
        self.assertNotIn("target=", html)

    def test_a_raw_html_link_is_treated_the_same(self):
        self.assertIn(INTERNAL, render_markdown('<a href="/a">a</a>'))
        self.assertIn(EXTERNAL, render_markdown('<a href="https://example.com/a">a</a>'))

    def test_several_links_in_one_text_are_decided_one_by_one(self):
        html = render_markdown("[a](/a) and [b](https://example.com/b) and [c](https://ravixcms.com/c)")
        self.assertEqual(html.count(INTERNAL), 2)
        self.assertEqual(html.count(EXTERNAL), 1)

    def test_a_link_text_that_looks_like_markup_cannot_confuse_the_rewrite(self):
        html = render_markdown('[<a href="/x">](https://example.com/y)')
        self.assertIn('<a href="https://example.com/y" rel="noopener noreferrer nofollow">', html)  # the outer link stays external
        self.assertIn('<a href="/x" rel="noopener noreferrer">', html)  # the raw inner one is judged on its own address


@override_settings(SITE_URL="", ALLOWED_HOSTS=["example.org"])
class NoSiteUrlTests(SimpleTestCase):
    def test_allowed_hosts_alone_define_the_own_hosts(self):
        self.assertNotIn("nofollow", render_markdown("[x](https://www.example.org/a)"))
        self.assertIn("nofollow", render_markdown("[x](https://example.com/a)"))


class CommentsAndStudioTests(TestCase):
    def test_user_comments_keep_nofollow_ugc_even_for_internal_links(self):
        from apps.comments.services import clean_body

        html = clean_body('<a href="/inside">i</a> <a href="https://example.com/">e</a>')
        self.assertEqual(html.count("noopener noreferrer nofollow ugc"), 2)

    @override_settings(SITE_URL="https://ravixcms.com", ALLOWED_HOSTS=["ravixcms.com"])
    def test_a_studio_text_widget_follows_the_same_rule(self):
        from apps.studio.render import RenderCtx

        ctx = RenderCtx.__new__(RenderCtx)  # only its markdown() helper is used
        self.assertNotIn("nofollow", ctx.markdown("[x](/a)"))
        self.assertIn("nofollow", ctx.markdown("[x](https://example.com/a)"))


class RerenderMigrationTests(TestCase):
    @override_settings(SITE_URL="https://ravixcms.com", ALLOWED_HOSTS=["ravixcms.com"])
    def test_stored_bodies_lose_nofollow_on_internal_links_only(self):
        from django.apps import apps as django_apps

        from apps.content.models import Page, Post

        post = Post.objects.create(title="t", body="[a](/x) [b](https://example.com/)")
        Post.objects.filter(pk=post.pk).update(body_html='<p><a href="/x" rel="noopener noreferrer nofollow">a</a> <a href="https://example.com/" rel="noopener noreferrer nofollow">b</a></p>')
        page = Page.objects.create(title="p", body="[c](/y)")
        Page.objects.filter(pk=page.pk).update(body_html='<p><a href="/y" rel="noopener noreferrer nofollow">c</a></p>')
        migration = importlib.import_module("apps.content.migrations.0011_rerender_bodies_internal_links")
        migration.forwards(django_apps, None)
        migration.forwards(django_apps, None)  # idempotent
        html = Post.objects.get(pk=post.pk).body_html
        self.assertIn('href="/x" rel="noopener noreferrer"', html)
        self.assertIn('href="https://example.com/" rel="noopener noreferrer nofollow"', html)
        self.assertNotIn("nofollow", Page.objects.get(pk=page.pk).body_html)
