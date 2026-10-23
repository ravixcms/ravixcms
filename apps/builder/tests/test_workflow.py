import json

from django.test import Client
from django.urls import reverse

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.audit.models import AuditLog
from apps.builder import services
from apps.builder.models import DesignVersion, PageDesign
from apps.content.models import Page

from .helpers import BuilderTestCase, clone, col, document, el, row, section, simple_doc


class Base(BuilderTestCase):
    def setUp(self):
        self.admin = make_user("adm", Role.ADMIN)
        self.author = make_user("aut", Role.AUTHOR)
        self.user = make_user("usr", Role.USER)
        self.page = Page.objects.create(title="درباره ما", slug="about", body="متن قدیمی **مارک‌داون**", status="published", author=self.admin)
        self.design = services.get_design(self.page)

    def url(self, name, *extra):
        return reverse(f"builder:{name}", args=[self.page.pk, *extra])

    def post(self, name, data, *extra, client=None, raw=None, ctype="application/json"):
        body = raw if raw is not None else json.dumps(data)
        return (client or self.client).post(self.url(name, *extra), body, content_type=ctype)

    def save_draft(self, doc, user=None):
        self.client.force_login(user or self.author)
        d = PageDesign.objects.get(pk=self.design.pk)
        return self.post("api_draft", {"document": doc, "revision": d.revision})

    def act(self, action, user, note="", number=None):
        self.client.force_login(user)
        if number is None:
            return self.post("api_versions", {"action": action, "note": note})
        return self.post("api_version", {"action": action, "note": note}, number)


class PermissionTests(Base):
    def test_anonymous_gets_json_401_and_redirect_for_pages(self):
        for name in ("api_state", "api_draft", "api_versions"):
            r = self.client.get(self.url(name)) if name == "api_state" else self.post(name, {})
            self.assertEqual(r.status_code, 401, name)
            self.assertFalse(r.json()["ok"])
        self.assertEqual(self.client.get(self.url("editor")).status_code, 302)
        self.assertEqual(self.client.get(self.url("preview")).status_code, 302)
        self.assertEqual(self.client.get(reverse("builder:list")).status_code, 302)

    def test_plain_user_forbidden_everywhere(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("builder:list")).status_code, 403)
        self.assertEqual(self.client.get(self.url("editor")).status_code, 403)
        self.assertEqual(self.client.get(self.url("preview")).status_code, 403)
        self.assertEqual(self.client.get(self.url("api_state")).status_code, 403)
        self.assertEqual(self.post("api_draft", {"document": simple_doc(), "revision": 1}).status_code, 403)
        self.assertEqual(self.client.get(reverse("builder:api_media")).status_code, 403)

    def test_author_can_edit_but_not_publish_or_review(self):
        self.client.force_login(self.author)
        self.assertEqual(self.client.get(self.url("editor")).status_code, 200)
        self.assertEqual(self.save_draft(simple_doc()).status_code, 200)
        self.assertEqual(self.post("api_versions", {"action": "publish"}).status_code, 403)
        self.assertEqual(self.post("api_versions", {"action": "unpublish"}).status_code, 403)
        self.act("submit", self.author)
        n = DesignVersion.objects.get().number
        self.assertEqual(self.act("approve", self.author, number=n).status_code, 403)
        self.assertEqual(self.act("reject", self.author, number=n).status_code, 403)
        self.assertIsNone(PageDesign.objects.get().published_version)

    def test_services_enforce_permission_even_if_view_is_bypassed(self):
        self.save_draft(simple_doc())
        v = services.submit_for_review(self.design.pk, self.author)
        from django.core.exceptions import PermissionDenied
        for fn in (lambda: services.approve(self.design.pk, v.number, self.author), lambda: services.publish_draft(self.design.pk, self.author),
                   lambda: services.reject(self.design.pk, v.number, self.author), lambda: services.unpublish(self.design.pk, self.author)):
            with self.assertRaises(PermissionDenied):
                fn()

    def test_csrf_enforced_on_all_writes(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.admin)
        for name, extra in (("api_draft", ()), ("api_versions", ()), ("api_version", (1,))):
            r = c.post(self.url(name, *extra), json.dumps({"document": simple_doc(), "revision": 1}), content_type="application/json")
            self.assertEqual(r.status_code, 403, name)
        self.assertEqual(PageDesign.objects.get().revision, 1)

    def test_csrf_token_header_is_accepted(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.admin)
        token = c.get(self.url("editor")).context["config"]["csrf"]
        r = c.post(self.url("api_draft"), json.dumps({"document": simple_doc(), "revision": 1}), content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(r.status_code, 200)

    def test_get_not_allowed_on_write_endpoints(self):
        self.client.force_login(self.admin)
        for name in ("api_draft", "api_versions"):
            self.assertEqual(self.client.get(self.url(name)).status_code, 405)

    def test_media_api_lists_images_only(self):
        m = self.make_image()
        self.client.force_login(self.author)
        data = self.client.get(reverse("builder:api_media")).json()
        self.assertEqual([i["id"] for i in data["items"]], [m.pk])
        data = self.client.get(reverse("builder:api_media") + f"?ids={m.pk},999,abc").json()
        self.assertEqual([i["id"] for i in data["items"]], [m.pk])


class DraftApiTests(Base):
    def test_save_draft_roundtrip_and_revision_increments(self):
        doc = simple_doc("عنوان من")
        r = self.save_draft(doc)
        self.assertEqual(r.json(), {"ok": True, "revision": 2})
        state = self.client.get(self.url("api_state")).json()
        self.assertEqual(state["revision"], 2)
        self.assertEqual(state["document"]["sections"][0]["rows"][0]["columns"][0]["elements"][0]["props"]["text"], "عنوان من")
        self.assertEqual(PageDesign.objects.get().updated_by, self.author)

    def test_stale_revision_returns_409_and_keeps_data(self):
        self.save_draft(simple_doc("اول"))
        self.client.force_login(self.admin)
        r = self.post("api_draft", {"document": simple_doc("دوم"), "revision": 1})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["revision"], 2)
        self.assertIn("اول", json.dumps(PageDesign.objects.get().draft, ensure_ascii=False))

    def test_invalid_document_is_422_with_paths_and_not_saved(self):
        bad = simple_doc()
        bad["sections"][0]["rows"][0]["columns"][0]["elements"][0]["props"]["onclick"] = "x"
        bad["sections"][0]["props"]["bg_color"] = "red"
        r = self.save_draft(bad)
        self.assertEqual(r.status_code, 422)
        paths = [e["path"] for e in r.json()["errors"]]
        self.assertTrue(any(p.endswith(".onclick") for p in paths))
        self.assertTrue(any(p.endswith("bg_color") for p in paths))
        self.assertEqual(PageDesign.objects.get().revision, 1)
        self.assertEqual(PageDesign.objects.get().draft["sections"], [])

    def test_draft_rejects_missing_or_foreign_media(self):
        r = self.save_draft(document(section(row(col(el("image", media=424242))))))
        self.assertEqual(r.status_code, 422)

    def test_malformed_requests(self):
        self.client.force_login(self.author)
        self.assertEqual(self.post("api_draft", None, raw="{not json").status_code, 400)
        self.assertEqual(self.post("api_draft", None, raw="[1,2]").status_code, 400)
        self.assertEqual(self.post("api_draft", {"document": simple_doc()}).status_code, 400)  # بدون revision
        self.assertEqual(self.post("api_draft", {"document": simple_doc(), "revision": "1"}).status_code, 400)
        self.assertEqual(self.post("api_draft", {"document": simple_doc(), "revision": True}).status_code, 400)
        self.assertEqual(self.post("api_draft", {"document": simple_doc(), "revision": 1}, ctype="text/plain").status_code, 400)
        self.assertEqual(self.post("api_draft", {"document": None, "revision": 1}).status_code, 422)

    def test_oversized_body_rejected(self):
        self.client.force_login(self.author)
        big = simple_doc("x" * 290)
        for _ in range(0):
            pass
        payload = json.dumps({"document": big, "revision": 1, "pad": "x" * 500_000})
        self.assertEqual(self.post("api_draft", None, raw=payload).status_code, 400)

    def test_unknown_page_is_404(self):
        self.client.force_login(self.author)
        self.assertEqual(self.client.get(reverse("builder:api_state", args=[9999])).status_code, 404)

    def test_draft_does_not_affect_public_page(self):
        self.save_draft(simple_doc("فقط پیش‌نویس"))
        r = self.client.get("/fa/about/")
        self.assertContains(r, "مارک‌داون")
        self.assertNotContains(r, "فقط پیش‌نویس")


class WorkflowTests(Base):
    def test_author_submit_then_admin_approve_publishes(self):
        self.save_draft(simple_doc("نسخه تأییدی"))
        r = self.act("submit", self.author, "لطفاً بررسی کنید")
        self.assertEqual(r.status_code, 200)
        v = DesignVersion.objects.get()
        self.assertEqual((v.state, v.created_by), ("pending", self.author))
        self.assertNotContains(self.client.get("/fa/about/"), "نسخه تأییدی")  # هنوز منتشر نشده
        r = self.act("approve", self.admin, "عالی", number=v.number)
        self.assertEqual(r.status_code, 200)
        v.refresh_from_db()
        self.assertEqual((v.state, v.reviewed_by, v.review_note), ("approved", self.admin, "عالی"))
        self.assertEqual(r.json()["live"], v.number)
        page = self.client.get("/fa/about/")
        self.assertContains(page, "نسخه تأییدی")
        self.assertContains(page, 'class="bx-page"')
        self.assertNotContains(page, "مارک‌داون")  # جایگزین بدنه‌ی Markdown
        self.assertContains(page, "<title>")  # قالب/سئو دست‌نخورده

    def test_reject_keeps_site_unchanged(self):
        self.save_draft(simple_doc("رد می‌شود"))
        self.act("submit", self.author)
        v = DesignVersion.objects.get()
        self.act("reject", self.admin, "ناقص", number=v.number)
        v.refresh_from_db()
        self.assertEqual((v.state, v.review_note), ("rejected", "ناقص"))
        self.assertIsNone(PageDesign.objects.get().published_version)
        self.assertNotContains(self.client.get("/fa/about/"), "رد می‌شود")
        self.assertEqual(self.act("approve", self.admin, number=v.number).status_code, 409)  # ردشده قابل تأیید نیست

    def test_only_one_pending_version(self):
        self.save_draft(simple_doc("یک"))
        self.act("submit", self.author)
        self.save_draft(simple_doc("دو"))
        self.act("submit", self.author)
        states = list(DesignVersion.objects.order_by("number").values_list("state", flat=True))
        self.assertEqual(states, ["saved", "pending"])

    def test_submit_requires_complete_design(self):
        self.client.force_login(self.author)
        self.assertEqual(self.act("submit", self.author).status_code, 409)  # صفحه‌ی خالی
        self.save_draft(document(section(row(col(el("image")))))); r = self.act("submit", self.author)  # تصویر بدون انتخاب
        self.assertEqual(r.status_code, 422)
        self.assertEqual(DesignVersion.objects.count(), 0)

    def test_admin_direct_publish_and_unpublish(self):
        self.save_draft(simple_doc("انتشار مستقیم"), user=self.admin)
        r = self.act("publish", self.admin)
        self.assertEqual(r.status_code, 200)
        self.assertContains(self.client.get("/fa/about/"), "انتشار مستقیم")
        self.assertEqual(self.act("unpublish", self.admin).status_code, 200)
        page = self.client.get("/fa/about/")
        self.assertContains(page, "مارک‌داون")
        self.assertNotContains(page, "انتشار مستقیم")
        self.assertEqual(self.act("unpublish", self.admin).status_code, 409)

    def test_editing_draft_after_publish_does_not_change_live_page(self):
        self.save_draft(simple_doc("منتشرشده"), user=self.admin)
        self.act("publish", self.admin)
        self.save_draft(simple_doc("پیش‌نویس جدید"), user=self.admin)
        page = self.client.get("/fa/about/")
        self.assertContains(page, "منتشرشده")
        self.assertNotContains(page, "پیش‌نویس جدید")

    def test_unpublished_page_status_still_controls_visibility(self):
        self.save_draft(simple_doc("مخفی"), user=self.admin)
        self.act("publish", self.admin)
        Page.objects.filter(pk=self.page.pk).update(status="draft")
        self.assertEqual(self.client.get("/fa/about/").status_code, 404)

    def test_unknown_action_and_unknown_version(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.post("api_versions", {"action": "explode"}).status_code, 400)
        self.assertEqual(self.post("api_version", {"action": "restore"}, 777).status_code, 404)
        self.assertEqual(self.post("api_versions", {"action": "save", "note": 5}).status_code, 400)

    def test_audit_trail(self):
        self.save_draft(simple_doc("ثبت رویداد"), user=self.admin)
        self.act("save", self.admin)
        self.act("publish", self.admin)
        events = AuditLog.objects.filter(object_type="طرح صفحه‌ساز")
        self.assertEqual(set(events.values_list("action", flat=True)), {"create", "publish"})
        self.assertTrue(events.filter(actor=self.admin).exists())


class RestoreTests(Base):
    def test_restore_replaces_draft_and_snapshots_current_state_first(self):
        self.save_draft(simple_doc("نسخه الف"))
        self.act("save", self.author, "الف")
        self.save_draft(simple_doc("نسخه ب (ذخیره‌نشده)"))
        r = self.act("restore", self.author, number=1)
        self.assertEqual(r.status_code, 200)
        design = PageDesign.objects.get()
        self.assertIn("نسخه الف", json.dumps(design.draft, ensure_ascii=False))
        auto = DesignVersion.objects.get(is_auto=True)
        self.assertIn("ذخیره‌نشده", json.dumps(auto.document, ensure_ascii=False))  # هیچ کاری از دست نمی‌رود
        self.assertEqual(design.revision, 4)

    def test_restore_does_not_touch_live_page(self):
        self.save_draft(simple_doc("زنده"), user=self.admin)
        self.act("publish", self.admin)
        self.save_draft(simple_doc("بعدی"), user=self.admin)
        self.act("save", self.admin)
        self.act("restore", self.admin, number=1)
        self.assertContains(self.client.get("/fa/about/"), "زنده")

    def test_restored_versions_are_immutable_snapshots(self):
        self.save_draft(simple_doc("ثابت"))
        self.act("save", self.author)
        before = clone(DesignVersion.objects.get().document)
        self.save_draft(simple_doc("تغییر"))
        self.assertEqual(DesignVersion.objects.get().document, before)

    def test_restore_of_corrupt_version_is_refused(self):
        self.save_draft(simple_doc())
        self.act("save", self.author)
        DesignVersion.objects.update(document={"schema_version": 1, "sections": [{"id": "abcd", "rows": [], "evil": 1}]})
        r = self.act("restore", self.author, number=1)
        self.assertEqual(r.status_code, 422)

    def test_stale_editor_cannot_overwrite_after_restore(self):
        self.save_draft(simple_doc("الف"))
        self.act("save", self.author)
        self.act("restore", self.author, number=1)
        r = self.post("api_draft", {"document": simple_doc("کهنه"), "revision": 2})
        self.assertEqual(r.status_code, 409)


class PublicIntegrationTests(Base):
    def publish(self, doc):
        self.save_draft(doc, user=self.admin)
        self.act("publish", self.admin)

    def test_builder_page_uses_theme_chrome_and_title_toggle(self):
        self.publish(simple_doc("محتوای ساخته‌شده", show_title=True))
        r = self.client.get("/fa/about/")
        self.assertContains(r, "site-header")
        self.assertContains(r, "page-built")
        self.assertContains(r, "<h1>درباره ما</h1>", html=False)
        self.publish(simple_doc("محتوای ساخته‌شده", show_title=False))
        r = self.client.get("/fa/about/")
        self.assertContains(r, '<h1 class="sr-only">درباره ما</h1>')

    def test_csp_still_blocks_scripts_and_page_has_no_script_tags(self):
        self.publish(document(section(row(col(el("heading", text="<script>alert(1)</script>"))))))
        r = self.client.get("/fa/about/")
        self.assertIn("script-src 'self'", r["Content-Security-Policy"])
        html = r.content.decode()
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_corrupt_published_document_falls_back_to_markdown(self):
        self.publish(simple_doc("خراب می‌شود"))
        DesignVersion.objects.update(document={"schema_version": 1, "sections": [{"id": "abcd", "rows": [], "boom": True}]})
        r = self.client.get("/fa/about/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "مارک‌داون")

    def test_meta_description_comes_from_builder_text_not_css_or_old_markdown(self):
        self.publish(simple_doc("توضیح صفحه ساخته شده"))
        r = self.client.get("/fa/about/")
        desc = r.content.decode().split('name="description" content="', 1)[1].split('"', 1)[0]
        self.assertIn("توضیح صفحه ساخته شده", desc)
        self.assertNotIn("bx-", desc)
        self.assertNotIn("مارک", desc)

    def test_pages_without_design_unchanged(self):
        other = Page.objects.create(title="تماس", slug="contact", body="تماس با **ما**", status="published")
        r = self.client.get("/fa/contact/")
        self.assertContains(r, "<strong>ما</strong>", html=False)
        self.assertNotContains(r, "bx-page")

    def test_media_images_render_publicly(self):
        m = self.make_image()
        self.publish(document(section(row(col(el("image", media=m.pk))))))
        self.assertContains(self.client.get("/fa/about/"), m.file.url)

    def test_deleted_media_does_not_break_page(self):
        m = self.make_image()
        self.publish(document(section(row(col(el("image", media=m.pk), el("heading", text="سالم"))))))
        m.delete()
        r = self.client.get("/fa/about/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "سالم")

    def test_preview_shows_draft_and_versions_with_noindex_and_no_store(self):
        self.save_draft(simple_doc("پیش‌نمایش پیش‌نویس"), user=self.admin)
        self.act("save", self.admin)
        self.save_draft(simple_doc("پیش‌نمایش جدیدتر"), user=self.admin)
        r = self.client.get(self.url("preview"))
        self.assertContains(r, "پیش‌نمایش جدیدتر")
        self.assertIn("no-store", r["Cache-Control"])
        self.assertEqual(r["X-Frame-Options"], "SAMEORIGIN")
        self.assertContains(r, "noindex")
        r = self.client.get(self.url("preview") + "?version=1")
        self.assertContains(r, "پیش‌نمایش پیش‌نویس")
        self.assertNotContains(r, "پیش‌نمایش جدیدتر")

    def test_editor_page_renders_with_config_and_no_inline_script_data_leak(self):
        self.client.force_login(self.author)
        r = self.client.get(self.url("editor"))
        self.assertContains(r, 'id="bx-config"')
        self.assertContains(r, "builder/builder.js")
        cfg = r.context["config"]
        self.assertFalse(cfg["canPublish"])
        self.assertTrue(cfg["specs"]["types"]["heading"])

    def test_list_view_shows_pending_badge(self):
        self.save_draft(simple_doc())
        self.act("submit", self.author)
        self.client.force_login(self.admin)
        r = self.client.get(reverse("builder:list"))
        self.assertContains(r, "در انتظار تأیید")
        self.assertContains(self.client.get(reverse("builder:list") + "?pending=1"), "درباره ما")

    def test_page_delete_cascades_design(self):
        self.save_draft(simple_doc())
        self.page.delete()
        self.assertEqual(PageDesign.objects.count(), 0)
