"""Pages/posts whose slug is a language code (upgrade from a version without language prefixes)."""
import importlib
from io import StringIO

from django.apps import apps as django_apps
from django.core.management import call_command
from django.test import TestCase

from apps.audit.models import AuditLog
from apps.content import slug_audit
from apps.content.models import Page, Post, PublishStatus, Redirect
from apps.core import sitehealth


def make_page(slug, **kw):
    obj = Page.objects.create(title=kw.pop("title", f"Page {slug}"), slug=slug, body="x", status="published", **kw)
    return obj


def make_post(slug):
    return Post.objects.create(title=f"Post {slug}", slug=slug, body="x", status=PublishStatus.PUBLISHED, publish_at="2020-01-01T00:00:00Z")


def run_fix(**kw):
    return slug_audit.fix(Page, Post, Redirect, AuditLog, **kw)


class SlugAuditTests(TestCase):
    def setUp(self):
        # Pages could be saved with a language slug before the reservation existed: bypass validation like old data.
        self.page = make_page("temp")
        Page.objects.filter(pk=self.page.pk).update(slug="de")
        self.post = make_post("temp-post")
        Post.objects.filter(pk=self.post.pk).update(slug="fa")
        self.ok = make_page("about")

    def test_finds_only_language_slugs(self):
        found = [(kind, obj.slug) for kind, obj in slug_audit.find(Page, Post)]
        self.assertEqual(found, [("page", "de"), ("post", "fa")])
        self.assertEqual(slug_audit.count(Page, Post), (1, 1))

    def test_slug_is_renamed_with_redirect_and_audit_log(self):
        changes = run_fix()
        self.assertEqual([(c.old_slug, c.new_slug) for c in changes], [("de", "de-page"), ("fa", "fa-post")])
        self.page.refresh_from_db()
        self.post.refresh_from_db()
        self.assertEqual((self.page.slug, self.post.slug), ("de-page", "fa-post"))
        self.assertEqual(Page.objects.get(pk=self.ok.pk).slug, "about")
        redirects = {r.old_path: (r.new_path, r.status_code, r.is_auto) for r in Redirect.objects.all()}
        self.assertEqual(redirects, {"/de/": ("/de-page/", 301, True), "/blog/fa/": ("/blog/fa-post/", 301, True)})
        events = AuditLog.objects.filter(action="update")
        self.assertEqual(events.count(), 2)
        self.assertEqual(events.filter(message__contains="/de/ → /de-page/").count() + events.filter(message__contains="/de/ ← /de-page/").count(), 1)

    def test_old_prefixed_addresses_redirect_and_new_ones_work(self):
        run_fix()
        self.assertEqual(self.client.get("/fa/de-page/").status_code, 200)
        self.assertEqual(self.client.get("/fa/blog/fa-post/").status_code, 200)
        r = self.client.get("/en/de/")  # "/de/" itself is the German home page; prefixed old addresses still redirect
        self.assertEqual((r.status_code, r["Location"]), (301, "/en/de-page/"))
        r = self.client.get("/fa/blog/fa/")
        self.assertEqual((r.status_code, r["Location"]), (301, "/fa/blog/fa-post/"))
        self.assertEqual(self.client.get("/de/").status_code, 200)  # the language home page

    def test_new_slug_is_unique(self):
        make_page("de-page")
        run_fix()
        self.page.refresh_from_db()
        self.assertEqual(self.page.slug, "de-page-2")

    def test_case_insensitive(self):
        Page.objects.filter(pk=self.ok.pk).update(slug="EN")
        self.assertEqual(sorted(c.old_slug for c in run_fix()), ["EN", "de", "fa"])
        self.ok.refresh_from_db()
        self.assertEqual(self.ok.slug, "en-page")

    def test_dry_run_and_idempotence(self):
        self.assertEqual(len(run_fix(dry_run=True)), 2)
        self.page.refresh_from_db()
        self.assertEqual(self.page.slug, "de")
        self.assertFalse(Redirect.objects.exists())
        run_fix()
        self.assertEqual(run_fix(), [])
        self.assertEqual(Redirect.objects.count(), 2)

    def test_existing_redirect_chain_is_collapsed(self):
        Redirect.objects.create(old_path="/old-de/", new_path="/de/", status_code=301)
        run_fix()
        self.assertEqual(Redirect.objects.get(old_path="/old-de/").new_path, "/de-page/")

    def test_management_command(self):
        out = StringIO()
        call_command("ravix_fix_slugs", "--dry-run", stdout=out)
        self.assertIn("/de/", out.getvalue())
        self.assertEqual(Page.objects.get(pk=self.page.pk).slug, "de")
        call_command("ravix_fix_slugs", stdout=out)
        self.assertEqual(Page.objects.get(pk=self.page.pk).slug, "de-page")

    def test_migration_runs_the_same_fix(self):
        migration = importlib.import_module("apps.content.migrations.0007_fix_language_slugs")
        migration.fix_language_slugs(django_apps, None)
        self.assertEqual(Page.objects.get(pk=self.page.pk).slug, "de-page")
        self.assertEqual(Post.objects.get(pk=self.post.pk).slug, "fa-post")

    def test_site_health_reports_and_clears(self):
        check = sitehealth.check_content()[0]
        self.assertEqual(check.status, sitehealth.CRITICAL)  # a page cannot be reached
        self.assertIn("ravix_fix_slugs", check.fix)
        Page.objects.filter(pk=self.page.pk).update(slug="de-page")
        self.assertEqual(sitehealth.check_content()[0].status, sitehealth.RECOMMENDED)  # only a post is left
        run_fix()
        self.assertEqual(sitehealth.check_content()[0].status, sitehealth.GOOD)
        self.assertIn(check.group, {str(g) for g in sitehealth.GROUP_ORDER})

    def test_health_page_lists_the_check(self):
        from apps.accounts.models import Role
        from apps.accounts.tests.test_auth import make_user

        self.client.force_login(make_user("boss", Role.SUPER_ADMIN))
        html = self.client.get("/panel/site-health/").content.decode()
        self.assertIn("ravix_fix_slugs", html)
