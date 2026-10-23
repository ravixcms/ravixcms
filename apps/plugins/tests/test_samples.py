import re

from django.test import Client
from django.urls import reverse

from apps.content.models import Page, Post, PublishStatus
from apps.plugins import hooks, services
from apps.plugins.models import PluginEvent, PluginRecord
from apps.plugins.tests.helpers import PluginTestCase, base_manifest


class ContactFormPluginTests(PluginTestCase):
    copy_samples = True

    def setUp(self):
        super().setUp()
        self.install_activate("contact_form")
        self.url = "/ext/contact_form/"
        self.data = {"name": "علی رضایی", "email": "ali@example.com", "subject": "سلام", "message": "این یک پیام آزمایشی است.", "website": ""}

    def model(self):
        from django.apps import apps

        return apps.get_model("plugin_contact_form", "ContactMessage")

    def csrf_client(self):
        c = Client(enforce_csrf_checks=True)
        r = c.get(self.url)
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', r.content.decode()).group(1)
        return c, token

    def test_form_renders_inside_site_theme(self):
        r = Client().get(self.url)
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn("<form", html)
        self.assertIn("csrfmiddlewaretoken", html)
        self.assertIn("Content-Security-Policy", r.headers)
        self.assertIn("no-store", r["Cache-Control"])  # توکن CSRF نباید کش شود
        self.assertIn("noindex", html)

    def test_submit_stores_message(self):
        c, token = self.csrf_client()
        r = c.post(self.url, {**self.data, "csrfmiddlewaretoken": token})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], self.url + "?sent=1")
        msg = self.model().objects.get()
        self.assertEqual((msg.name, msg.email, msg.is_read), ("علی رضایی", "ali@example.com", False))
        self.assertContains(c.get(r["Location"]), "پیام شما ثبت شد")

    def test_csrf_required(self):
        c, _ = self.csrf_client()
        self.assertEqual(c.post(self.url, self.data).status_code, 403)
        self.assertFalse(self.model().objects.exists())

    def test_validation_errors(self):
        for bad in ({"email": "nope"}, {"name": ""}, {"message": "ab"}, {"message": "x" * 2001}, {"name": "x" * 81}):
            r = Client().post(self.url, {**self.data, **bad})
            self.assertEqual(r.status_code, 400, bad)
        self.assertFalse(self.model().objects.exists())

    def test_honeypot_discards_silently(self):
        r = Client().post(self.url, {**self.data, "website": "http://spam.example"})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(self.model().objects.exists())

    def test_rate_limit(self):
        PluginRecord.objects.filter(slug="contact_form").update(config={"max_per_hour": 2})
        self.invalidate()
        codes = [Client().post(self.url, self.data).status_code for _ in range(4)]
        self.assertEqual(codes, [302, 302, 429, 429])
        self.assertEqual(self.model().objects.count(), 2)

    def test_html_in_message_is_escaped_in_inbox(self):
        Client().post(self.url, {**self.data, "message": "<script>alert(1)</script> سلام", "name": "<b>x</b>y"})
        self.login(self.admin)
        html = self.client.get(reverse("plugins:page", args=["contact_form", "inbox"])).content.decode()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<b>x</b>", html)

    def test_inbox_permissions_and_actions(self):
        Client().post(self.url, self.data)
        inbox = reverse("plugins:page", args=["contact_form", "inbox"])
        self.assertEqual(Client().get(inbox).status_code, 302)
        self.login(self.author)
        self.assertEqual(self.client.get(inbox).status_code, 403)
        self.assertEqual(self.client.post(inbox, {"id": 1, "action": "delete"}).status_code, 403)
        self.assertEqual(self.model().objects.count(), 1)
        self.login(self.admin)
        r = self.client.get(inbox)
        self.assertContains(r, "ali@example.com")
        self.assertContains(r, "جدید")
        pk = self.model().objects.get().pk
        self.client.post(inbox, {"id": pk, "action": "read"})
        self.assertTrue(self.model().objects.get().is_read)
        self.client.post(inbox, {"id": pk, "action": "delete"})
        self.assertFalse(self.model().objects.exists())

    def test_notification_email_and_failure_isolation(self):
        from django.core import mail

        PluginRecord.objects.filter(slug="contact_form").update(config={"notify_email": "owner@example.com"})
        self.invalidate()
        Client().post(self.url, self.data)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("owner@example.com", mail.outbox[0].to)

    def test_notification_is_written_in_the_owners_language(self):
        """The owner's account language wins over the visitor's interface language; without an account: the site language."""
        from django.core import mail
        from django.utils import translation

        from apps.accounts.models import User

        PluginRecord.objects.filter(slug="contact_form").update(config={"notify_email": "owner@example.com"})
        self.invalidate()
        c = Client()
        c.cookies["ravix_lang"] = "ru"
        c.post(self.url, self.data)
        with translation.override("fa"):
            from django.utils.translation import gettext as fa_gettext
            site_language_subject = fa_gettext("New contact form message: %(value)s").split("%")[0]
        self.assertTrue(mail.outbox[-1].subject.startswith(site_language_subject))  # no account: the site language (fa), not ru
        mail.outbox.clear()
        owner = self.admin
        User.objects.filter(pk=owner.pk).update(email="owner@example.com", language="de")
        c.post(self.url, self.data)
        with translation.override("de"):
            from django.utils.translation import gettext as de_gettext
            german = de_gettext("New contact form message: %(value)s").split("%")[0]
        self.assertTrue(mail.outbox[-1].subject.startswith(german))
        self.assertNotEqual(german, site_language_subject)

    def test_settings_page_for_contact_form(self):
        self.login(self.admin)
        url = reverse("plugins:settings", args=["contact_form"])
        self.assertEqual(self.client.post(url, {"success_message": "ok", "notify_email": "bad", "max_per_hour": "5"}).status_code, 400)
        self.assertEqual(self.client.post(url, {"success_message": "ok", "notify_email": "a@b.com", "max_per_hour": "500"}).status_code, 400)
        self.assertEqual(self.client.post(url, {"success_message": "ممنون", "notify_email": "a@b.com", "max_per_hour": "7"}).status_code, 302)
        self.assertEqual(PluginRecord.objects.get(slug="contact_form").config["max_per_hour"], 7)

    def test_deactivating_hides_form_but_keeps_data(self):
        Client().post(self.url, self.data)
        services.deactivate("contact_form", self.admin)
        self.assertEqual(Client().get(self.url).status_code, 404)
        services.activate("contact_form", self.admin)
        self.assertEqual(self.model().objects.count(), 1)


class SeoToolkitPluginTests(PluginTestCase):
    copy_samples = True

    def setUp(self):
        super().setUp()
        self.install_activate("seo_toolkit")
        self.page = Page.objects.create(title="درباره ما", slug="about", body="## ما\n\nمتن", status="published")
        self.post = Post.objects.create(title="نوشته", slug="p1", body="متن", status=PublishStatus.PUBLISHED, publish_at="2020-01-01T00:00:00Z")
        self.edit = reverse("plugins:page", args=["seo_toolkit", "edit"])
        self.meta = {"kind": "page", "id": self.page.pk}

    def head(self, path="/fa/about/"):
        return Client().get(path).content.decode()

    def test_per_page_settings_applied_to_public_head(self):
        before = self.head()
        self.assertNotIn("noindex", before)
        self.login(self.admin)
        r = self.client.post(self.edit, {**self.meta, "title": "عنوان سفارشی", "description": "توضیح متا برای صفحه", "noindex": "on",
                                         "canonical": "https://example.com/about/", "og_image": "/media/x.jpg"})
        self.assertEqual(r.status_code, 302)
        html = self.head()
        self.assertIn("<title>عنوان سفارشی | ", html)
        self.assertIn('content="توضیح متا برای صفحه"', html)
        self.assertIn("noindex", html)
        self.assertIn('href="https://example.com/about/"', html)

    def test_post_supported(self):
        self.login(self.admin)
        self.client.post(self.edit, {"kind": "post", "id": self.post.pk, "title": "سئو پست", "description": ""})
        self.assertIn("سئو پست", self.head("/fa/blog/p1/"))

    def test_invalid_urls_rejected(self):
        self.login(self.admin)
        for field in ("canonical", "og_image"):
            r = self.client.post(self.edit, {**self.meta, "title": "t", field: "javascript:alert(1)"})
            self.assertEqual(r.status_code, 400, field)
        self.assertNotIn("javascript:", self.head())

    def test_permissions(self):
        self.login(self.author)
        self.assertEqual(self.client.get(self.edit + "?kind=page&id=1").status_code, 403)
        self.assertEqual(self.client.post(self.edit, {**self.meta, "title": "hack"}).status_code, 403)
        self.assertEqual(Client().get(reverse("plugins:page", args=["seo_toolkit", "overview"])).status_code, 302)

    def test_unknown_target_404(self):
        self.login(self.admin)
        self.assertEqual(self.client.get(self.edit + "?kind=page&id=99999").status_code, 404)
        self.assertEqual(self.client.get(self.edit + "?kind=evil&id=1").status_code, 404)
        self.assertEqual(self.client.get(self.edit + "?kind=page&id=abc").status_code, 404)

    def test_overview_lists_pages_and_posts(self):
        self.login(self.admin)
        r = self.client.get(reverse("plugins:page", args=["seo_toolkit", "overview"]))
        self.assertContains(r, "درباره ما")
        self.assertContains(r, "نوشته")

    def test_title_pattern_setting(self):
        PluginRecord.objects.filter(slug="seo_toolkit").update(config={"title_pattern": "{site} » {title}"})
        self.invalidate()
        self.login(self.admin)
        self.client.post(self.edit, {**self.meta, "title": "عنوان"})
        self.invalidate()
        self.assertRegex(self.head(), r"<title>[^<]+ » عنوان</title>")

    def test_disabled_plugin_restores_core_output(self):
        self.login(self.admin)
        self.client.post(self.edit, {**self.meta, "title": "عنوان سفارشی", "noindex": "on"})
        self.assertIn("noindex", self.head())
        services.deactivate("seo_toolkit", self.admin)
        self.assertNotIn("noindex", self.head())
        self.assertNotIn("عنوان سفارشی", self.head())


class HookSanitizeTests(PluginTestCase):
    HOOK_VIEWS = '''from django.http import HttpResponse


def home(request, api):
    return HttpResponse("x")


def evil(seo, api, **ctx):
    return {"title": "<script>alert(1)</script>T", "jsonld": [{"@type": "Evil"}], "canonical": "javascript:alert(1)", "robots": "<x>",
            "og_image": "data:text/html,boo", "description": "ok  desc", "unknown": "x", "og_type": "weird"}


def crash(seo, api, **ctx):
    raise RuntimeError("hook-boom")
'''

    def setUp(self):
        super().setUp()
        Page.objects.create(title="درباره ما", slug="about", body="متن", status="published")

    def make(self, slug, handler):
        m = base_manifest(slug, public_routes=[], hooks=[{"name": "public.seo", "handler": handler}])
        self.write_plugin(slug, manifest=m, views=self.HOOK_VIEWS)
        self.install_activate(slug)

    def test_core_sanitizes_plugin_seo_output(self):
        self.make("evilseo", "views:evil")
        html = Client().get("/fa/about/").content.decode()
        self.assertNotIn("Evil", html)  # jsonld توسط افزونه قابل‌تزریق نیست
        self.assertNotIn("javascript:", html)
        self.assertNotIn("data:text/html", html)
        self.assertNotIn("<script>alert(1)", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;T", html)  # عنوان فقط به‌صورت متن (escape‌شده)
        self.assertIn("ok desc", html)

    def test_sanitize_unit(self):
        base = {"title": "T", "description": "D", "canonical": "http://x/", "robots": "", "og_type": "website", "og_image": "", "jsonld": [1]}
        out = hooks.sanitize_seo(base, {"robots": "noindex,nofollow", "og_type": "article", "jsonld": ["x"], "canonical": "/p/"})
        self.assertEqual(out["robots"], "noindex,nofollow")
        self.assertEqual(out["og_type"], "article")
        self.assertEqual(out["jsonld"], [1])
        self.assertEqual(out["canonical"], "/p/")
        self.assertEqual(hooks.sanitize_seo(base, "not a dict"), base)
        self.assertEqual(hooks.sanitize_seo(base, {"title": 5})["title"], "T")

    def test_hook_crash_does_not_break_public_page(self):
        self.make("crashy", "views:crash")
        r = Client().get("/fa/about/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(PluginEvent.objects.filter(slug="crashy", kind="error").exists())

