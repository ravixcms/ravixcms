"""Translation groups: model rules, panel, hreflang, language switcher, sitemap, management command, multisite guard."""
import re
import uuid
from datetime import timedelta
from io import StringIO

from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.content import translations
from apps.content.models import Page, Post, PublishStatus
from apps.core.models import SiteSettings


def post(slug, language, **kw):
    kw.setdefault("status", PublishStatus.PUBLISHED)
    return Post.objects.create(title=f"T {slug}", slug=slug, body="b", language=language, **kw)


def page(slug, language, **kw):
    kw.setdefault("status", PublishStatus.PUBLISHED)
    return Page.objects.create(title=f"P {slug}", slug=slug, body="b", language=language, **kw)


def hreflangs(html):
    return dict(re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)">', html))


class ModelAndServiceTests(TestCase):
    def test_a_language_appears_once_per_group_in_the_database_too(self):
        group = uuid.uuid4()
        post("a", "en", translation_group=group)
        with self.assertRaises(IntegrityError), transaction.atomic():
            post("b", "en", translation_group=group)
        post("c", "fa", translation_group=group)  # another language is fine

    def test_items_without_a_group_never_collide(self):
        post("a", "en")
        post("b", "en")  # both have no group
        page("c", "en")
        page("d", "en")

    def test_link_makes_one_group_and_members_see_each_other(self):
        a, b, c = post("a", "en"), post("b", "fa"), post("c", "de")
        group = translations.link([a, b])
        self.assertEqual({p.slug for p in translations.members(Post.objects.get(slug="a"))}, {"a", "b"})
        translations.link([c, a])  # joins the existing group
        self.assertEqual(Post.objects.get(slug="c").translation_group, group)
        self.assertEqual([p.language for p in translations.members(a)], ["fa", "en", "de"])

    def test_two_items_of_the_same_language_cannot_be_linked(self):
        with self.assertRaises(translations.TranslationError):
            translations.link([post("a", "en"), post("b", "en")])
        self.assertFalse(Post.objects.exclude(translation_group=None).exists())

    def test_a_post_cannot_be_linked_to_a_page(self):
        with self.assertRaises(translations.TranslationError):
            translations.link([post("a", "en"), page("b", "fa")])

    def test_items_of_two_different_groups_are_not_merged(self):
        a, b, c, d = post("a", "en"), post("b", "fa"), post("c", "de"), post("d", "tr")
        translations.link([a, b])
        translations.link([c, d])
        with self.assertRaises(translations.TranslationError):
            translations.link([a, c])

    def test_a_language_clash_with_an_existing_member_is_refused_and_nothing_changes(self):
        a, b, c = post("a", "en"), post("b", "fa"), post("c", "fa")
        translations.link([a, b])
        with self.assertRaises(translations.TranslationError):
            translations.link([a, c])
        self.assertIsNone(Post.objects.get(slug="c").translation_group)

    def test_unlink_dissolves_a_group_that_is_left_with_one_item(self):
        a, b = post("a", "en"), post("b", "fa")
        translations.link([a, b])
        translations.unlink(Post.objects.get(slug="a"))
        self.assertIsNone(Post.objects.get(slug="a").translation_group)
        self.assertIsNone(Post.objects.get(slug="b").translation_group)

    def test_unlink_keeps_a_group_of_three_together(self):
        a, b, c = post("a", "en"), post("b", "fa"), post("c", "de")
        translations.link([a, b, c])
        translations.unlink(Post.objects.get(slug="a"))
        self.assertEqual(Post.objects.get(slug="b").translation_group, Post.objects.get(slug="c").translation_group)
        self.assertIsNotNone(Post.objects.get(slug="b").translation_group)

    @override_settings(RAVIX_MULTISITE_ENABLED=True)
    def test_nothing_is_linked_while_several_sites_are_enabled(self):
        with self.assertRaises(translations.TranslationError):
            translations.link([post("a", "en"), post("b", "fa")])

    def test_existing_data_is_untouched_by_the_migration_state(self):
        self.assertIsNone(post("a", "en").translation_group)


class PanelTests(TestCase):
    def setUp(self):
        self.sa = make_user("sa", Role.SUPER_ADMIN)
        self.client.force_login(self.sa)

    def test_the_edit_form_shows_the_translations_card_and_create_links(self):
        en = page("about", "en")
        resp = self.client.get(reverse("content:page_update", args=[en.pk]))
        self.assertContains(resp, 'id="translations"')
        self.assertContains(resp, f"/panel/pages/new/?translation_of={en.pk}&amp;language=fa")
        self.assertNotContains(resp, f"translation_of={en.pk}&amp;language=en")  # its own language is not offered

    def test_create_a_translation_opens_the_form_with_the_language_and_saves_into_the_group(self):
        en = post("hello", "en")
        url = reverse("content:post_create") + f"?translation_of={en.pk}&language=de"
        form = self.client.get(url)
        self.assertContains(form, f'name="translation_of" value="{en.pk}"')
        self.assertEqual(form.context["form"].initial["language"], "de")
        resp = self.client.post(reverse("content:post_create"), {"title": "Hallo", "slug": "hallo", "body": "x", "status": "draft", "language": "de",
                                                                  "translation_of": en.pk, "author": self.sa.pk})
        self.assertEqual(resp.status_code, 302, getattr(resp, "context", None) and resp.context["form"].errors)
        de = Post.objects.get(slug="hallo")
        self.assertEqual(de.translation_group, Post.objects.get(pk=en.pk).translation_group)
        self.assertIsNotNone(de.translation_group)

    def test_a_new_translation_in_a_language_the_group_already_has_is_rejected(self):
        en, fa = post("hello", "en"), post("salam", "fa")
        translations.link([en, fa])
        resp = self.client.post(reverse("content:post_create"), {"title": "Again", "slug": "again", "body": "x", "status": "draft", "language": "fa",
                                                                   "translation_of": en.pk, "author": self.sa.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("language", resp.context["form"].errors)
        self.assertFalse(Post.objects.filter(slug="again").exists())

    def test_attach_an_existing_item_by_slug(self):
        en, fa = page("about", "en"), page("darbare", "fa")
        resp = self.client.post(reverse("content:page_translation_link", args=[en.pk]), {"slug": "darbare"}, follow=True)
        self.assertContains(resp, "darbare")
        self.assertEqual(Page.objects.get(pk=en.pk).translation_group, Page.objects.get(pk=fa.pk).translation_group)

    def test_a_duplicate_language_is_refused_with_a_message_and_changes_nothing(self):
        en, other_en = post("a", "en"), post("b", "en")
        resp = self.client.post(reverse("content:post_translation_link", args=[en.pk]), {"slug": "b"}, follow=True)
        from django.contrib.messages import constants

        self.assertIn(constants.ERROR, [m.level for m in resp.context["messages"]])
        self.assertIsNone(Post.objects.get(pk=other_en.pk).translation_group)

    def test_unknown_slug_self_and_other_kind_are_refused(self):
        en = post("a", "en")
        page("a-page", "fa")
        for slug in ("nope", "a", "a-page"):
            resp = self.client.post(reverse("content:post_translation_link", args=[en.pk]), {"slug": slug}, follow=True)
            self.assertEqual(resp.status_code, 200)
            self.assertIsNone(Post.objects.get(pk=en.pk).translation_group, slug)

    def test_detach(self):
        a, b = post("a", "en"), post("b", "fa")
        translations.link([a, b])
        self.client.post(reverse("content:post_translation_unlink", args=[a.pk]))
        self.assertIsNone(Post.objects.get(pk=a.pk).translation_group)

    def test_changing_the_language_to_one_the_group_has_is_a_form_error(self):
        a, b = post("a", "en"), post("b", "fa")
        translations.link([a, b])
        resp = self.client.post(reverse("content:post_update", args=[a.pk]), {"title": "T a", "slug": "a", "body": "b", "status": "published", "language": "fa", "author": self.sa.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("language", resp.context["form"].errors)

    def test_actions_are_post_only_and_need_a_login(self):
        a = post("a", "en")
        url = reverse("content:post_translation_unlink", args=[a.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.logout()
        self.assertNotEqual(self.client.post(url).status_code, 200)

    def test_an_author_cannot_attach_someone_elses_published_post(self):
        author = make_user("aut", Role.AUTHOR)
        mine = post("mine", "en", status=PublishStatus.DRAFT, author=author)
        theirs = post("theirs", "fa")
        self.client.force_login(author)
        resp = self.client.post(reverse("content:post_translation_link", args=[mine.pk]), {"slug": "theirs"})
        self.assertEqual(resp.status_code, 403)
        self.assertIsNone(Post.objects.get(pk=theirs.pk).translation_group)

    @override_settings(RAVIX_MULTISITE_ENABLED=True)
    def test_the_card_is_hidden_and_actions_refuse_while_several_sites_are_enabled(self):
        a, b = post("a", "en"), post("b", "fa")
        self.assertNotContains(self.client.get(reverse("content:post_update", args=[a.pk])), 'id="translations"')
        self.client.post(reverse("content:post_translation_link", args=[a.pk]), {"slug": "b"})
        self.assertIsNone(Post.objects.get(pk=a.pk).translation_group)


class PublicTests(TestCase):
    def setUp(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={"content_per_language": True, "language": "fa"})
        self.en, self.fa, self.de = post("hello", "en"), post("salam", "fa"), post("hallo", "de")
        translations.link([self.en, self.fa, self.de])

    def head(self, url):
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, url)
        return resp.content.decode()

    def test_every_live_translation_is_listed_with_absolute_urls_and_standard_codes(self):
        links = hreflangs(self.head("/en/blog/hello/"))
        self.assertEqual(set(links), {"en", "fa", "de", "x-default"})
        self.assertEqual(links["en"], "http://testserver/en/blog/hello/")
        self.assertEqual(links["fa"], "http://testserver/fa/blog/salam/")
        self.assertEqual(links["x-default"], links["en"])  # x-default is the English version

    def test_the_same_list_is_in_every_translation(self):
        self.assertEqual(hreflangs(self.head("/fa/blog/salam/")), hreflangs(self.head("/en/blog/hello/")))

    def test_codes_use_bcp47_case(self):
        pt = post("ola", "pt-br")
        zh = post("nihao", "zh-hans")
        translations.link([self.en, pt, zh])
        links = hreflangs(self.head("/en/blog/hello/"))
        self.assertIn("pt-BR", links)
        self.assertIn("zh-Hans", links)

    def test_a_draft_a_scheduled_and_a_noindex_translation_are_left_out(self):
        draft = post("draft-fr", "fr", status=PublishStatus.DRAFT)
        later = post("later-es", "es", status=PublishStatus.SCHEDULED, publish_at=timezone.now() + timedelta(days=2))
        hidden = post("hidden-tr", "tr", noindex=True)
        translations.link([self.en, draft, later, hidden])
        links = hreflangs(self.head("/en/blog/hello/"))
        self.assertEqual(set(links), {"en", "fa", "de", "x-default"})

    def test_a_noindex_post_has_no_hreflang_at_all(self):
        Post.objects.filter(pk=self.en.pk).update(noindex=True)
        self.assertEqual(hreflangs(self.head("/en/blog/hello/")), {})

    def test_x_default_falls_back_to_the_site_default_language_then_is_left_out(self):
        Post.objects.filter(pk=self.en.pk).update(status=PublishStatus.DRAFT)  # no live English version
        links = hreflangs(self.head("/fa/blog/salam/"))
        self.assertEqual(links["x-default"], links["fa"])  # the site language is Persian
        SiteSettings.objects.filter(pk=1).update(language="ru")
        self.assertNotIn("x-default", hreflangs(self.head("/fa/blog/salam/")))

    def test_a_single_live_item_has_no_hreflang(self):
        Post.objects.filter(pk__in=[self.fa.pk, self.de.pk]).update(status=PublishStatus.DRAFT)
        self.assertEqual(hreflangs(self.head("/en/blog/hello/")), {})

    def test_pages_work_the_same_way(self):
        a, b = page("about", "en"), page("darbare", "fa")
        translations.link([a, b])
        links = hreflangs(self.head("/en/about/"))
        self.assertEqual(links["fa"], "http://testserver/fa/darbare/")
        self.assertEqual(links["x-default"], "http://testserver/en/about/")

    @override_settings(RAVIX_MULTISITE_ENABLED=True)
    def test_nothing_is_published_while_several_sites_are_enabled(self):
        self.assertEqual(hreflangs(self.head("/en/blog/hello/")), {})

    def test_the_language_switcher_goes_to_the_translation_else_behaves_as_before(self):
        from django.test import RequestFactory
        from django.urls import resolve
        from django.utils import translation as dj_translation

        from apps.public.builders import language_links

        request = RequestFactory().get("/en/blog/hello/")
        request.ravix_translation_paths = translations.switcher_paths(self.en)
        with dj_translation.override("en"):
            request.resolver_match = resolve("/en/blog/hello/")
            links = {l.code: l.url for l in language_links(request)}
        self.assertEqual(links["fa"], "/fa/blog/salam/")
        self.assertEqual(links["de"], "/de/blog/hallo/")
        self.assertEqual(links["fr"], "/fr/blog/")  # no French version: the blog of that language, as before

    def test_the_language_view_redirects_to_the_translation(self):
        resp = self.client.post(reverse("set_language"), {"language": "fa", "next": "/en/blog/hello/"})
        self.assertEqual(resp["Location"], "/fa/blog/salam/")
        resp = self.client.post(reverse("set_language"), {"language": "fr", "next": "/en/blog/hello/"})
        self.assertEqual(resp["Location"], "/fr/blog/hello/")  # unchanged behaviour when there is no translation

    def test_the_sitemap_lists_the_alternates_of_a_group(self):
        xml = self.client.get("/en/sitemap.xml").content.decode()
        block = re.search(r"<url><loc>http://testserver/en/blog/hello/</loc>.*?</url>", xml, re.S).group(0)
        self.assertIn('<xhtml:link rel="alternate" hreflang="fa" href="http://testserver/fa/blog/salam/"/>', block)
        self.assertIn('hreflang="x-default" href="http://testserver/en/blog/hello/"', block)
        self.assertEqual(block.count("<xhtml:link"), 4)


@override_settings(RAVIX_CLI_LANG="en")
class CommandTests(TestCase):
    def run_command(self, *args):
        out = StringIO()
        call_command("ravix_link_translations", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_reports_and_changes_nothing(self):
        post("a", "en"), post("b", "fa")
        out = self.run_command("--type", "post", "--slugs", "a", "b", "--dry-run")
        self.assertIn("Dry run", out)
        self.assertFalse(Post.objects.exclude(translation_group=None).exists())

    def test_the_real_run_links_them(self):
        post("a", "en"), post("b", "fa"), post("c", "de")
        out = self.run_command("--type", "post", "--slugs", "a", "b", "c")
        self.assertIn("3 items", out)
        self.assertEqual(Post.objects.exclude(translation_group=None).values("translation_group").distinct().count(), 1)

    def test_pages_are_linked_with_type_page(self):
        page("a", "en"), page("b", "fa")
        self.run_command("--type", "page", "--slugs", "a", "b")
        self.assertEqual(Page.objects.exclude(translation_group=None).count(), 2)

    def test_errors_are_reported_and_nothing_changes(self):
        post("a", "en"), post("b", "en")
        for args in (("--slugs", "a", "b"), ("--slugs", "a", "missing")):
            with self.assertRaises(CommandError):
                self.run_command("--type", "post", *args)
        with self.assertRaises(CommandError):  # the dry run applies the same checks
            self.run_command("--type", "post", "--slugs", "a", "b", "--dry-run")
        self.assertFalse(Post.objects.exclude(translation_group=None).exists())

    @override_settings(RAVIX_MULTISITE_ENABLED=True)
    def test_it_refuses_while_several_sites_are_enabled(self):
        post("a", "en"), post("b", "fa")
        with self.assertRaises(CommandError):
            self.run_command("--type", "post", "--slugs", "a", "b")
