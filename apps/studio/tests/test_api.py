"""Every editor endpoint checks the session, the capability, the object permission, CSRF and the content type."""
import json

from django.test import Client, override_settings
from django.urls import reverse

from apps.studio import services

from .helpers import requires_studio, StudioBase, clone, simple_doc


def jpost(client, url, data, **extra):
    return client.post(url, json.dumps(data), content_type="application/json", **extra)


@requires_studio
class AccessTests(StudioBase):
    def urls(self, doc):
        return {
            "state": reverse("studio:api_state", args=[doc.pk]), "ping": reverse("studio:api_ping", args=[doc.pk]),
            "draft": reverse("studio:api_draft", args=[doc.pk]), "render": reverse("studio:api_render", args=[doc.pk]),
            "versions": reverse("studio:api_versions", args=[doc.pk]), "export": reverse("studio:api_export", args=[doc.pk]),
            "import": reverse("studio:api_import", args=[doc.pk]), "media": reverse("studio:api_media"),
        }

    def test_anonymous_gets_401_json_on_every_api_endpoint(self):
        doc = self.document_for()
        for name, url in self.urls(doc).items():
            r = Client().get(url) if name in ("state", "export", "media") else jpost(Client(), url, {})
            self.assertEqual(r.status_code, 401, name)
            self.assertFalse(r.json()["ok"])

    def test_a_regular_user_gets_403_everywhere(self):
        doc = self.document_for()
        c = Client()
        c.force_login(self.visitor)
        for name, url in self.urls(doc).items():
            r = c.get(url) if name in ("state", "export", "media") else jpost(c, url, {})
            self.assertEqual(r.status_code, 403, name)

    def test_panel_pages_need_the_capability(self):
        c = Client()
        for url in (reverse("studio:list"), reverse("studio:editor", args=["page", self.page.pk]), reverse("studio:preview", args=[self.document_for().pk])):
            self.assertEqual(c.get(url).status_code, 302 if "preview" not in url else 302, url)  # redirected to sign in
        c.force_login(self.visitor)
        for url in (reverse("studio:list"), reverse("studio:editor", args=["page", self.page.pk]), reverse("studio:preview", args=[self.document_for().pk])):
            self.assertEqual(c.get(url).status_code, 403, url)

    def test_an_author_cannot_reach_someone_elses_post_through_any_endpoint(self):
        doc = self.document_for(self.post)
        c = Client()
        c.force_login(self.other_author)
        self.assertEqual(c.get(reverse("studio:editor", args=["post", self.post.pk])).status_code, 403)
        for name, url in self.urls(doc).items():
            if name == "media":
                continue
            r = c.get(url) if name in ("state", "export") else jpost(c, url, {"document": simple_doc(), "revision": 1, "action": "save"})
            self.assertEqual(r.status_code, 403, name)
        self.assertEqual(c.get(reverse("studio:preview", args=[doc.pk])).status_code, 403)

    def test_an_author_cannot_publish_or_approve_through_the_api(self):
        doc = self.put_draft(simple_doc("x"), self.post, self.author)
        c = Client()
        c.force_login(self.author)
        r = jpost(c, reverse("studio:api_versions", args=[doc.pk]), {"action": "publish"})
        self.assertEqual(r.status_code, 403)
        sub = jpost(c, reverse("studio:api_versions", args=[doc.pk]), {"action": "submit"})
        self.assertEqual(sub.status_code, 200)
        number = [v for v in sub.json()["versions"] if v["state"] == "pending"][0]["number"]
        r = jpost(c, reverse("studio:api_version", args=[doc.pk, number]), {"action": "approve"})
        self.assertEqual(r.status_code, 403)

    def test_csrf_is_enforced_on_writes(self):
        doc = self.document_for()
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.admin)
        r = jpost(c, reverse("studio:api_draft", args=[doc.pk]), {"document": simple_doc(), "revision": 1})
        self.assertEqual(r.status_code, 403)
        doc.refresh_from_db()
        self.assertEqual(doc.draft_rev, 1)

    def test_only_json_bodies_are_accepted(self):
        doc = self.document_for()
        c = Client()
        c.force_login(self.admin)
        r = c.post(reverse("studio:api_draft", args=[doc.pk]), {"document": "x"})
        self.assertEqual(r.status_code, 400)
        r = c.post(reverse("studio:api_draft", args=[doc.pk]), "not json", content_type="application/json")
        self.assertEqual(r.status_code, 400)
        r = c.post(reverse("studio:api_draft", args=[doc.pk]), "[1]", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_oversized_bodies_are_refused(self):
        doc = self.document_for()
        c = Client()
        c.force_login(self.admin)
        big = {"document": simple_doc(), "revision": 1, "pad": "x" * 700_000}
        self.assertEqual(jpost(c, reverse("studio:api_draft", args=[doc.pk]), big).status_code, 400)

    def test_get_is_refused_on_write_endpoints(self):
        doc = self.document_for()
        c = Client()
        c.force_login(self.admin)
        for name in ("draft", "render", "versions", "import", "ping"):
            self.assertEqual(c.get(self.urls(doc)[name]).status_code, 405, name)


@requires_studio
class EditorFlowTests(StudioBase):
    def setUp(self):
        super().setUp()
        self.c = Client()
        self.c.force_login(self.admin)

    def test_the_editor_page_for_a_page_and_for_a_post(self):
        for kind, obj in (("page", self.page), ("post", self.post)):
            r = self.c.get(reverse("studio:editor", args=[kind, obj.pk]))
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, 'id="studio-config"')
            self.assertContains(r, "studio/js/editor.js")
            cfg = r.context["config"]
            self.assertTrue(cfg["canPublish"] and cfg["canHtml"])
            self.assertIn("heading", [w["type"] for w in cfg["specs"]["widgets"]])
        self.assertEqual(self.c.get(reverse("studio:editor", args=["page", 99999])).status_code, 404)
        self.assertEqual(self.c.get(reverse("studio:editor", args=["thing", 1])).status_code, 404)

    def test_the_author_config_cannot_publish_or_write_html(self):
        c = Client()
        c.force_login(self.author)
        cfg = c.get(reverse("studio:editor", args=["post", self.post.pk])).context["config"]
        self.assertFalse(cfg["canPublish"])
        self.assertFalse(cfg["canHtml"])

    def test_the_editor_page_loads_no_third_party_resources(self):
        html = self.c.get(reverse("studio:editor", args=["page", self.page.pk])).content.decode()
        self.assertNotRegex(html, r'(src|href)="https?://')

    def test_state_draft_render_roundtrip(self):
        doc = self.document_for()
        state = self.c.get(reverse("studio:api_state", args=[doc.pk])).json()
        self.assertTrue(state["ok"])
        r = jpost(self.c, reverse("studio:api_draft", args=[doc.pk]), {"document": simple_doc("hello"), "revision": state["revision"]})
        self.assertEqual(r.status_code, 200)
        rev = r.json()["revision"]
        self.assertEqual(rev, state["revision"] + 1)
        rendered = jpost(self.c, reverse("studio:api_render", args=[doc.pk]), {"document": simple_doc("hello")}).json()
        self.assertTrue(rendered["ok"])
        self.assertIn("hello", rendered["html"])
        self.assertIn('data-st-id="', rendered["html"])  # the preview markup carries the ids the editor selects by
        self.assertIn(".st-root", rendered["css"])

    def test_a_stale_draft_save_returns_409_with_the_current_revision(self):
        doc = self.put_draft(simple_doc("a"))
        r = jpost(self.c, reverse("studio:api_draft", args=[doc.pk]), {"document": simple_doc("b"), "revision": 1})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["revision"], doc.draft_rev)
        ok = jpost(self.c, reverse("studio:api_draft", args=[doc.pk]), {"document": simple_doc("b"), "revision": 1, "overwrite": True})
        self.assertEqual(ok.status_code, 200)

    def test_an_invalid_document_is_422_with_error_paths(self):
        doc = self.document_for()
        bad = simple_doc()
        bad["sections"][0]["columns"][0]["widgets"][0]["props"]["tag"] = "script"
        r = jpost(self.c, reverse("studio:api_draft", args=[doc.pk]), {"document": bad, "revision": doc.draft_rev})
        self.assertEqual(r.status_code, 422)
        self.assertTrue(r.json()["errors"][0]["path"].startswith("$"))

    def test_the_revision_must_be_an_integer(self):
        doc = self.document_for()
        for bad in ("1", True, None, 1.5):
            r = jpost(self.c, reverse("studio:api_draft", args=[doc.pk]), {"document": simple_doc(), "revision": bad})
            self.assertEqual(r.status_code, 400, bad)

    def test_ping_reports_the_other_editor(self):
        doc = self.document_for()
        self.assertIsNone(self.c.post(reverse("studio:api_ping", args=[doc.pk]), content_type="application/json").json()["otherEditor"])
        c2 = Client()
        c2.force_login(self.root)
        other = c2.post(reverse("studio:api_ping", args=[doc.pk]), content_type="application/json").json()["otherEditor"]
        self.assertEqual(other["name"], self.admin.display_name)

    def test_versions_endpoint_runs_the_whole_workflow(self):
        doc = self.put_draft(simple_doc("flow"))
        url = reverse("studio:api_versions", args=[doc.pk])
        self.assertEqual(jpost(self.c, url, {"action": "save", "note": "first"}).status_code, 200)
        state = jpost(self.c, url, {"action": "publish"}).json()
        self.assertIsNotNone(state["live"])
        self.assertEqual(jpost(self.c, url, {"action": "nope"}).status_code, 400)
        self.assertEqual(jpost(self.c, url, {"action": "save", "note": 5}).status_code, 400)
        state = jpost(self.c, url, {"action": "unpublish"}).json()
        self.assertIsNone(state["live"])
        self.assertEqual(jpost(self.c, url, {"action": "unpublish"}).status_code, 409)

    def test_version_actions_restore_and_rollback(self):
        doc = self.put_draft(simple_doc("one"))
        v1 = services.publish_draft(doc.pk, self.admin)
        doc = self.put_draft(simple_doc("two"))
        services.publish_draft(doc.pk, self.admin)
        r = jpost(self.c, reverse("studio:api_version", args=[doc.pk, v1.number]), {"action": "rollback"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["live"], v1.number)
        r = jpost(self.c, reverse("studio:api_version", args=[doc.pk, v1.number]), {"action": "restore"})
        self.assertIn("one", json.dumps(r.json()["document"]))
        self.assertEqual(self.c.post(reverse("studio:api_version", args=[doc.pk, 999]), "{}", content_type="application/json").status_code, 404)

    def test_the_list_filters_and_never_shows_other_authors_posts_to_an_author(self):
        c = Client()
        c.force_login(self.author)
        r = c.get(reverse("studio:list"), {"kind": "post"})
        self.assertContains(r, "Mine")
        c2 = Client()
        c2.force_login(self.other_author)
        self.assertNotContains(c2.get(reverse("studio:list"), {"kind": "post"}), "Mine")
        self.assertContains(self.c.get(reverse("studio:list"), {"q": "abo"}), "About")

    def test_the_preview_is_noindex_and_uses_the_theme(self):
        doc = self.put_draft(simple_doc("Preview me"))
        r = self.c.get(reverse("studio:preview", args=[doc.pk]))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn("Preview me", html)
        self.assertIn('id="st-mount"', html)
        self.assertIn("noindex", html)
        self.assertIn("studio/preview.js", html)
        self.assertEqual(self.c.get(reverse("studio:preview", args=[doc.pk]), {"version": 99}).status_code, 404)

    def test_the_old_builder_addresses_redirect_to_studio(self):
        r = self.c.get("/panel/builder/", follow=False)
        self.assertEqual(r.status_code, 301 if r.status_code == 301 else 302)
        self.assertIn("/panel/studio/", r["Location"])
        r = self.c.get(f"/panel/builder/{self.page.pk}/", follow=False)
        self.assertIn("/panel/studio/", r["Location"])

    def test_media_picker_lists_only_images_and_supports_ids(self):
        self.make_image("pick.png")
        r = self.c.get(reverse("studio:api_media")).json()
        self.assertEqual(len(r["items"]), 1)
        ids = ",".join(str(i["id"]) for i in r["items"])
        self.assertEqual(len(self.c.get(reverse("studio:api_media"), {"ids": ids}).json()["items"]), 1)
        self.assertEqual(self.c.get(reverse("studio:api_media"), {"ids": "abc,-1"}).json()["items"], [])


class ModuleOffTests(StudioBase):
    def setUp(self):
        super().setUp()
        off = override_settings(BUILTIN_MODULES_DEFAULT_ON=False)  # not class-level: that would leak MEDIA_ROOT (see test_no_studio)
        off.enable()
        self.addCleanup(off.disable)

    def test_the_editor_and_the_api_are_hidden_while_the_module_is_off(self):
        c = Client()
        c.force_login(self.admin)
        doc = self.document_for()
        for url in (reverse("studio:list"), reverse("studio:editor", args=["page", self.page.pk]), reverse("studio:api_state", args=[doc.pk])):
            self.assertEqual(c.get(url).status_code, 404, url)

    def test_the_edit_button_disappears_from_the_classic_forms(self):
        c = Client()
        c.force_login(self.admin)
        r = c.get(reverse("content:page_update", args=[self.page.pk]))
        self.assertNotContains(r, "data-studio-edit")
