import io
import tempfile
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, override_settings

from apps.pluginhub.models import HubPlugin, HubVersion, Publisher, PublisherToken

from .helpers import HubTestCase, make_zip, manifest


def upfile(data):
    f = io.BytesIO(data)
    f.name = "p.zip"
    return f


class DirectoryAccessTests(HubTestCase):
    def setUp(self):
        super().setUp()
        self.publish_version("alpha_plugin", "1.0.0", man=manifest("alpha_plugin", category="seo", description="فرم تماس پیشرفته"))
        self.publish_version("beta_plugin", "1.0.0", man=manifest("beta_plugin", ravix=">=9.0.0"))

    def test_anonymous_redirected_to_login(self):
        r = Client().get("/panel/plugin-directory/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("login", r["Location"])

    def test_author_forbidden_admin_allowed(self):
        self.assertEqual(self.login(self.author).get("/panel/plugin-directory/").status_code, 403)
        self.assertEqual(self.login(self.pubuser).get("/panel/plugin-directory/").status_code, 403)  # ناشر بودن مجوز مدیریت سایت نمی‌دهد
        self.assertEqual(self.login(self.admin).get("/panel/plugin-directory/").status_code, 200)

    def test_search_filter_and_compat(self):
        c = self.login(self.admin)
        r = c.get("/panel/plugin-directory/?q=تماس")
        self.assertContains(r, "alpha_plugin")
        self.assertNotContains(r, "beta_plugin")
        self.assertNotContains(c.get("/panel/plugin-directory/?category=seo"), "beta_plugin")
        r = c.get("/panel/plugin-directory/?compatible=1")
        self.assertContains(r, "alpha_plugin")
        self.assertNotContains(r, "beta_plugin")

    def test_detail_shows_compat_problems_and_fetch_command(self):
        c = self.login(self.admin)
        r = c.get("/panel/plugin-directory/beta_plugin/")
        self.assertContains(r, "ناسازگار")
        self.assertNotContains(r, "plugin_fetch beta_plugin")  # نسخهٔ سازگار ندارد ⇒ دستور دریافت نشان داده نمی‌شود
        r = c.get("/panel/plugin-directory/alpha_plugin/")
        self.assertContains(r, "plugin_fetch alpha_plugin")
        self.assertContains(r, HubVersion.objects.get(plugin__slug="alpha_plugin").sha256)

    def test_detail_404_for_unpublished_or_unlisted(self):
        from apps.pluginhub import services
        services.upload_version(self.pub, self.pubuser, make_zip("draft_only", "1.0.0"))
        c = self.login(self.admin)
        self.assertIn(c.get("/panel/plugin-directory/draft_only/").status_code, (403, 404))
        HubPlugin.objects.filter(slug="alpha_plugin").update(is_listed=False)
        self.assertEqual(c.get("/panel/plugin-directory/alpha_plugin/").status_code, 404)

    def test_installed_state_shown(self):
        from apps.plugins.models import PluginRecord
        PluginRecord.objects.create(slug="alpha_plugin", name="x", version="0.9.0", fingerprint="f" * 64)
        self.assertContains(self.login(self.admin).get("/panel/plugin-directory/alpha_plugin/"), "نصب‌شده")


class PublisherPanelTests(HubTestCase):
    def test_non_publisher_forbidden_even_for_admin(self):
        for u in (self.admin, self.author, self.super):
            c = self.login(u)
            for url in ("/panel/publish/", "/panel/publish/upload/", "/panel/publish/tokens/"):
                self.assertEqual(c.get(url).status_code, 403, (u.username, url))
            r = c.post("/panel/publish/upload/", {"package": upfile(make_zip())})
            self.assertEqual(r.status_code, 403)
        self.assertEqual(HubPlugin.objects.count(), 0)

    def test_revoked_publisher_loses_access(self):
        self.pub.is_active = False
        self.pub.save()
        self.assertEqual(self.login(self.pubuser).get("/panel/publish/").status_code, 403)

    def test_upload_publish_and_yank_flow(self):
        c = self.login(self.pubuser)
        r = c.post("/panel/publish/upload/", {"package": upfile(make_zip()), "changelog": "اولین"})
        self.assertEqual(r.status_code, 302)
        v = HubVersion.objects.get()
        self.assertEqual(v.status, "draft")
        c.post("/panel/publish/demo_plugin/1.0.0/publish/")
        v.refresh_from_db()
        self.assertEqual(v.status, "published")
        c.post("/panel/publish/demo_plugin/1.0.0/yank/", {"reason": "باگ"})
        v.refresh_from_db()
        self.assertEqual(v.status, "yanked")
        # پس‌گرفته‌شده دوباره منتشر نمی‌شود
        c.post("/panel/publish/demo_plugin/1.0.0/publish/")
        v.refresh_from_db()
        self.assertEqual(v.status, "yanked")

    def test_upload_rejects_non_zip_and_bad_package(self):
        c = self.login(self.pubuser)
        f = upfile(b"x")
        f.name = "x.txt"
        self.assertEqual(c.post("/panel/publish/upload/", {"package": f}).status_code, 400)
        self.assertEqual(c.post("/panel/publish/upload/", {"package": upfile(b"junk")}).status_code, 400)

    def test_cannot_touch_other_publishers_plugin(self):
        self.publish_version("mine_plugin", "1.0.0")
        c2 = self.login(self.pubuser2)
        self.assertEqual(c2.get("/panel/publish/mine_plugin/").status_code, 404)
        self.assertEqual(c2.post("/panel/publish/mine_plugin/", {"summary": "هک"}).status_code, 404)
        self.assertEqual(c2.post("/panel/publish/mine_plugin/1.0.0/yank/").status_code, 404)
        self.assertEqual(HubVersion.objects.get().status, "published")
        r = c2.post("/panel/publish/upload/", {"package": upfile(make_zip("mine_plugin", "2.0.0"))})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(HubVersion.objects.count(), 1)

    def test_edit_metadata(self):
        self.publish_version("mine_plugin", "1.0.0")
        c = self.login(self.pubuser)
        r = c.post("/panel/publish/mine_plugin/", {"summary": "خلاصهٔ جدید", "description": "d", "category": self.tool_cat.pk, "homepage": ""})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(HubPlugin.objects.get().summary, "خلاصهٔ جدید")

    def test_token_shown_once_and_revocable(self):
        c = self.login(self.pubuser)
        r = c.post("/panel/publish/tokens/", {"name": "ci"})
        self.assertContains(r, "rvn_")
        import re

        raw = re.search(r"rvn_[A-Za-z0-9_\-]{20,}", r.content.decode()).group(0)  # توکن کامل فقط یک‌بار نمایش داده می‌شود
        self.assertNotContains(c.get("/panel/publish/tokens/"), raw)  # صفحهٔ لیست فقط پیشوند دارد (مستقل از حروف تصادفی توکن)
        tok = PublisherToken.objects.get()
        self.assertEqual(self.login(self.pubuser2).post(f"/panel/publish/tokens/{tok.pk}/revoke/").status_code, 404)
        c.post(f"/panel/publish/tokens/{tok.pk}/revoke/")
        tok.refresh_from_db()
        self.assertIsNotNone(tok.revoked_at)

    def test_csrf_enforced_on_panel_post(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.pubuser)
        self.assertEqual(c.post("/panel/publish/upload/", {"package": upfile(make_zip())}).status_code, 403)


class RegistryAdminTests(HubTestCase):
    def test_only_superadmin(self):
        for u in (self.admin, self.author, self.pubuser):
            c = self.login(u)
            for url in ("/panel/registry/publishers/", "/panel/registry/categories/", "/panel/registry/plugins/"):
                self.assertEqual(c.get(url).status_code, 403, (u.username, url))
        self.assertEqual(self.login(self.admin).post("/panel/registry/publishers/", {"op": "add", "username": "writer", "display_name": "x"}).status_code, 403)
        self.assertFalse(Publisher.objects.filter(user=self.author).exists())
        c = self.login(self.super)
        for url in ("/panel/registry/publishers/", "/panel/registry/categories/", "/panel/registry/plugins/"):
            self.assertEqual(c.get(url).status_code, 200)

    def test_grant_and_revoke_publisher(self):
        c = self.login(self.super)
        r = c.post("/panel/registry/publishers/", {"op": "add", "username": "writer", "display_name": "نویسنده", "website": ""})
        self.assertEqual(r.status_code, 302)
        pub = Publisher.objects.get(user=self.author)
        self.assertEqual(self.login(self.author).get("/panel/publish/").status_code, 200)
        c.post("/panel/registry/publishers/", {"op": "toggle_active", "pk": pub.pk})
        self.assertEqual(self.login(self.author).get("/panel/publish/").status_code, 403)

    def test_unknown_user_rejected(self):
        r = self.login(self.super).post("/panel/registry/publishers/", {"op": "add", "username": "ghost", "display_name": "x"})
        self.assertEqual(r.status_code, 400)

    def test_admin_unlist_and_yank(self):
        v = self.publish_version("mine_plugin", "1.0.0")
        c = self.login(self.super)
        c.post("/panel/registry/plugins/", {"op": "yank", "pk": v.pk})
        v.refresh_from_db()
        self.assertEqual(v.status, "yanked")
        p = v.plugin
        c.post("/panel/registry/plugins/", {"op": "toggle_listed", "pk": p.pk})
        p.refresh_from_db()
        self.assertFalse(p.is_listed)

    def test_categories_crud(self):
        c = self.login(self.super)
        c.post("/panel/registry/categories/", {"slug": "newcat", "name": "جدید", "order": 5})
        from apps.pluginhub.models import Category
        cat = Category.objects.get(slug="newcat")
        c.post("/panel/registry/categories/", {"op": "delete", "pk": cat.pk})
        self.assertFalse(Category.objects.filter(slug="newcat").exists())


class FetchCommandTests(HubTestCase):
    """plugin_fetch همان نصب‌کنندهٔ پنل را به کار می‌برد: HTTPS، SHA-256، امضا؛ و نصب/فعال‌سازی نمی‌کند."""

    def setUp(self):
        super().setUp()
        from apps.plugins import signing
        self.seed, self.pubkey = signing.generate_keypair()
        self._ov2 = override_settings(RAVIX_REGISTRY_SIGNING_KEY=self.seed, RAVIX_TRUSTED_PUBLIC_KEYS=[f"t:{self.pubkey}"])
        self._ov2.enable()
        self.addCleanup(self._ov2.disable)
        self.v = self.publish_version("alpha_plugin", "1.0.0")
        self.data = make_zip("alpha_plugin", "1.0.0")
        self.dest = tempfile.mkdtemp(prefix="fetch-dest-")

    def fake_open(self, data=None, mutate=None):
        import json

        from django.test import Client
        c = Client()
        detail = c.get("/registry/v1/plugins/alpha_plugin/", secure=True).json()
        for v in detail["versions"]:
            v["download_url"] = v["download_url"].replace("https://testserver", "https://reg.example.com")
        if mutate:
            mutate(detail)
        pkg = data if data is not None else b"".join(c.get("/registry/v1/plugins/alpha_plugin/versions/1.0.0/download/", secure=True).streaming_content)

        def _open(url, max_bytes, allow_insecure=False):
            return (json.dumps(detail).encode() if url.endswith("/alpha_plugin/") else pkg), {}
        return _open

    def run_cmd(self, registry="https://reg.example.com", **kw):
        call_command("plugin_fetch", "alpha_plugin", registry=registry, dest=self.dest, stdout=io.StringIO(), **kw)

    def test_downloads_verifies_and_does_not_install(self):
        from apps.plugins.models import PluginRecord
        with mock.patch("apps.plugins.sources.http_get", self.fake_open()):
            self.run_cmd(sha256=self.v.sha256)
        self.assertTrue((Path(self.dest) / "alpha_plugin" / "plugin.json").is_file())
        self.assertFalse(PluginRecord.objects.exists())  # نصب/فعال‌سازی خودکار نیست

    def test_rejects_tampered_download(self):
        with mock.patch("apps.plugins.sources.http_get", self.fake_open(data=self.data + b"x")):
            with self.assertRaisesMessage(CommandError, "SHA-256"):
                self.run_cmd()
        self.assertFalse((Path(self.dest) / "alpha_plugin").exists())

    def test_rejects_wrong_expected_hash(self):
        with mock.patch("apps.plugins.sources.http_get", self.fake_open()):
            with self.assertRaises(CommandError):
                self.run_cmd(sha256="0" * 64)

    def test_rejects_unsigned_or_untrusted(self):
        def strip(d):
            for v in d["versions"]:
                v["signature"] = None
        with mock.patch("apps.plugins.sources.http_get", self.fake_open(mutate=strip)):
            with self.assertRaisesMessage(CommandError, "امضا"):
                self.run_cmd()
        from apps.plugins import signing
        with override_settings(RAVIX_TRUSTED_PUBLIC_KEYS=[f"o:{signing.generate_keypair()[1]}"]), mock.patch("apps.plugins.sources.http_get", self.fake_open()):
            with self.assertRaises(CommandError):
                self.run_cmd()
        self.assertFalse((Path(self.dest) / "alpha_plugin").exists())

    def test_refuses_http_and_overwrite(self):
        with self.assertRaisesMessage(CommandError, "HTTPS"):
            self.run_cmd(registry="http://reg.example.com")
        with mock.patch("apps.plugins.sources.http_get", self.fake_open()):
            self.run_cmd()
            with self.assertRaisesMessage(CommandError, "نمی‌کنیم"):
                self.run_cmd()

    def test_insecure_flag_requires_debug(self):
        with self.assertRaises(CommandError):
            self.run_cmd(registry="http://localhost:8000", allow_insecure=True)
