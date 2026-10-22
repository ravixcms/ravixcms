"""ریدایرکت: مدل و اعتبارسنجی، middleware (فقط روی ۴۰۴)، پنل و capability، ساخت خودکار ۳۰۱ هنگام تغییر نامک."""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.audit.models import AuditLog
from apps.content.models import Page, Post, PublishStatus, Redirect


def make(old, new, code=301, **kw):
    return Redirect.objects.create(old_path=old, new_path=new, status_code=code, **kw)


class RedirectModelTests(TestCase):
    def check(self, **kw):
        r = Redirect(**{"old_path": "/old/", "new_path": "/new/", **kw})
        r.full_clean()
        return r

    def test_valid_internal_and_external(self):
        self.check()
        self.check(new_path="https://example.com/x?a=1")

    def test_old_path_rules(self):
        for bad in ("old/", "//evil.com/", "/", "/a?b=1", "/a#x", "/panel/x/", "/accounts/login/", "/api/x", "/media/a.png", "/static/a.css"):
            with self.assertRaises(ValidationError, msg=bad):
                self.check(old_path=bad)

    def test_new_path_rules(self):
        for bad in ("new/", "//evil.com", "javascript:alert(1)", "data:text/html,x", "ftp://x.com/", "https://", "/a\\b", "/old"):
            with self.assertRaises(ValidationError, msg=bad):
                self.check(new_path=bad)

    def test_percent_encoded_old_path_is_decoded(self):
        self.assertEqual(self.check(old_path="/blog/%D8%A7/").old_path, "/blog/ا/")

    def test_direct_loop_rejected(self):
        make("/b/", "/a/")
        with self.assertRaises(ValidationError):
            self.check(old_path="/a/", new_path="/b/")

    def test_old_path_unique(self):
        make("/x/", "/y/")
        with self.assertRaises(ValidationError):
            self.check(old_path="/x/", new_path="/z/")


class RedirectMiddlewareTests(TestCase):
    def test_redirects_404_with_status_and_query(self):
        make("/gone/", "/blog/", 301)
        r = self.client.get("/gone/?utm=1")
        self.assertEqual((r.status_code, r["Location"]), (301, "/blog/?utm=1"))
        make("/tmp-old/", "/blog/", 302)
        self.assertEqual(self.client.get("/tmp-old/").status_code, 302)

    def test_trailing_slash_variants(self):
        make("/gone/", "/blog/")
        self.assertEqual(self.client.get("/gone").status_code, 301)
        make("/noslash", "/blog/")
        self.assertEqual(self.client.get("/noslash/").status_code, 301)

    def test_head_works_post_does_not(self):
        make("/gone/", "/blog/")
        self.assertEqual(self.client.head("/gone/").status_code, 301)
        self.assertNotEqual(self.client.post("/gone/").status_code, 301)

    def test_existing_page_not_hijacked(self):
        Page.objects.create(title="هست", slug="exists", body="x", status="published")
        make("/exists/", "/blog/")
        r = self.client.get("/exists/")
        self.assertEqual(r.status_code, 200)

    def test_unknown_path_still_404(self):
        self.assertEqual(self.client.get("/nothing-here/").status_code, 404)

    def test_persian_old_path_and_encoded_location(self):
        make("/درباره-قدیمی/", "/درباره/")
        r = self.client.get("/" + "درباره-قدیمی".encode().decode() + "/")
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r["Location"], "/%D8%AF%D8%B1%D8%A8%D8%A7%D8%B1%D9%87/")

    def test_external_target(self):
        make("/go/", "https://example.com/x")
        r = self.client.get("/go/?a=1")
        self.assertEqual((r.status_code, r["Location"]), (301, "https://example.com/x"))

    def test_self_target_does_not_loop(self):
        Redirect.objects.bulk_create([Redirect(old_path="/loop/", new_path="/loop/")])
        self.assertEqual(self.client.get("/loop/").status_code, 404)

    def test_panel_paths_never_redirected(self):
        Redirect.objects.bulk_create([Redirect(old_path="/panel/zzz/", new_path="/blog/")])
        self.assertEqual(self.client.get("/panel/zzz/").status_code, 404)  # ریدایرکت اعمال نمی‌شود


class RedirectPanelTests(TestCase):
    def setUp(self):
        self.admin = make_user("adm", Role.ADMIN)
        self.author = make_user("aut", Role.AUTHOR)
        self.sa = make_user("sa", Role.SUPER_ADMIN)
        self.url = reverse("content:redirect_list")

    def test_capability_gate(self):
        for url in (self.url, reverse("content:redirect_create")):
            self.assertEqual(self.client.get(url).status_code, 302)  # ناشناس ← ورود
        self.client.force_login(self.author)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(reverse("content:redirect_create"), {"old_path": "/a/", "new_path": "/b/", "status_code": 301}).status_code, 403)
        self.assertFalse(Redirect.objects.exists())
        for u in (self.admin, self.sa):
            self.assertTrue(u.has_cap("redirects.manage"))
        self.assertFalse(self.author.has_cap("redirects.manage"))

    def test_create_update_delete_with_audit(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("content:redirect_create"), {"old_path": "/a/", "new_path": "/b/", "status_code": 302, "note": "n"})
        self.assertEqual(r.status_code, 302)
        obj = Redirect.objects.get()
        self.assertEqual((obj.status_code, obj.is_auto), (302, False))
        self.assertTrue(AuditLog.objects.filter(action="create", object_type="ریدایرکت", object_id=str(obj.pk)).exists())
        self.client.post(reverse("content:redirect_update", args=[obj.pk]), {"old_path": "/a/", "new_path": "/c/", "status_code": 301})
        obj.refresh_from_db()
        self.assertEqual((obj.new_path, obj.status_code), ("/c/", 301))
        self.assertTrue(AuditLog.objects.filter(action="update", object_type="ریدایرکت").exists())
        self.client.post(reverse("content:redirect_delete", args=[obj.pk]))
        self.assertFalse(Redirect.objects.exists())
        self.assertTrue(AuditLog.objects.filter(action="delete", object_type="ریدایرکت").exists())

    def test_invalid_input_rejected_in_form(self):
        self.client.force_login(self.admin)
        for old, new in (("/panel/x/", "/b/"), ("/a/", "javascript:alert(1)"), ("/a/", "/a/")):
            r = self.client.post(reverse("content:redirect_create"), {"old_path": old, "new_path": new, "status_code": 301})
            self.assertEqual(r.status_code, 200, (old, new))
        self.assertFalse(Redirect.objects.exists())

    def test_status_code_limited_to_301_302(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("content:redirect_create"), {"old_path": "/a/", "new_path": "/b/", "status_code": 307})
        self.assertEqual(r.status_code, 200)

    def test_list_search_and_nav(self):
        make("/alpha/", "/x/")
        make("/beta/", "/y/")
        self.client.force_login(self.admin)
        r = self.client.get(self.url, {"q": "alpha"})
        self.assertContains(r, "/alpha/")
        self.assertNotContains(r, "/beta/")
        self.assertContains(self.client.get(reverse("dashboard:home")), "ریدایرکت‌ها")


class AutoRedirectOnSlugChangeTests(TestCase):
    def post(self, **kw):
        d = dict(title="نوشته", slug="old", body="x", status=PublishStatus.PUBLISHED, publish_at=timezone.now() - timedelta(days=1))
        d.update(kw)
        return Post.objects.create(**d)

    def test_post_slug_change_creates_301(self):
        p = self.post()
        p.slug = "new"
        p.save()
        r = Redirect.objects.get()
        self.assertEqual((r.old_path, r.new_path, r.status_code, r.is_auto), ("/blog/old/", "/blog/new/", 301, True))
        resp = self.client.get("/blog/old/")
        self.assertEqual((resp.status_code, resp["Location"]), (301, "/blog/new/"))
        self.assertEqual(self.client.get("/blog/new/").status_code, 200)
        self.assertTrue(AuditLog.objects.filter(action="create", object_type="ریدایرکت", message__contains="خودکار").exists())

    def test_page_slug_change_creates_301(self):
        pg = Page.objects.create(title="درباره", slug="about", body="x", status="published")
        pg.slug = "about-us"
        pg.save()
        self.assertEqual(self.client.get("/about/")["Location"], "/about-us/")

    def test_persian_slug_change(self):
        p = self.post(slug="قدیم")
        p.slug = "جدید"
        p.save()
        r = Redirect.objects.get()
        self.assertEqual((r.old_path, r.new_path), ("/blog/قدیم/", "/blog/جدید/"))
        self.assertEqual(self.client.get("/blog/قدیم/").status_code, 301)

    def test_no_redirect_when_slug_unchanged_or_on_create(self):
        p = self.post()
        p.title = "عنوان جدید"
        p.save()
        self.assertFalse(Redirect.objects.exists())

    def test_draft_slug_never_leaks(self):
        p = self.post(status=PublishStatus.DRAFT, publish_at=None)
        p.slug = "other"
        p.save()
        self.assertFalse(Redirect.objects.exists())

    def test_no_redirect_if_becomes_unpublished_in_same_save(self):
        p = self.post()
        p.slug, p.status = "other", PublishStatus.DRAFT
        p.save()
        self.assertFalse(Redirect.objects.exists())

    def test_chain_is_collapsed(self):
        p = self.post()
        p.slug = "b"
        p.save()
        p.slug = "c"
        p.save()
        self.assertEqual(set(Redirect.objects.values_list("old_path", "new_path")), {("/blog/old/", "/blog/c/"), ("/blog/b/", "/blog/c/")})
        self.assertEqual(self.client.get("/blog/old/")["Location"], "/blog/c/")

    def test_reverting_slug_removes_conflicting_redirect(self):
        p = self.post()
        p.slug = "b"
        p.save()
        p.slug = "old"
        p.save()
        self.assertFalse(Redirect.objects.filter(old_path="/blog/old/").exists())
        self.assertEqual(self.client.get("/blog/old/").status_code, 200)
        self.assertEqual(self.client.get("/blog/b/")["Location"], "/blog/old/")

    def test_manual_redirect_for_same_old_path_is_updated(self):
        make("/blog/old/", "/somewhere/", 302)
        p = self.post()
        p.slug = "new"
        p.save()
        r = Redirect.objects.get(old_path="/blog/old/")
        self.assertEqual((r.new_path, r.status_code, r.is_auto), ("/blog/new/", 301, True))

    def test_scheduled_post_that_is_live_counts(self):
        p = self.post(status=PublishStatus.SCHEDULED, publish_at=timezone.now() - timedelta(minutes=5))
        p.slug = "new"
        p.save()
        self.assertTrue(Redirect.objects.filter(old_path="/blog/old/").exists())

    def test_panel_edit_changes_slug_and_redirects(self):
        admin = make_user("adm", Role.ADMIN)
        self.client.force_login(admin)
        p = self.post()
        r = self.client.post(reverse("content:post_update", args=[p.pk]),
                             {"title": "نوشته", "slug": "renamed", "excerpt": "", "body": "x", "status": "published", "author": admin.pk})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Redirect.objects.filter(old_path="/blog/old/", new_path="/blog/renamed/").exists())
