"""Export and import of a page or one section as JSON, with schema validation and no trust in IDs or media from another site."""
import json

from django.test import Client
from django.urls import reverse

from apps.studio import services
from apps.studio.fields import DocumentInvalid
from apps.studio.schema import validate_document

from .helpers import requires_studio, StudioBase, clone, column, document, section, simple_doc, widget


def two_sections():
    return document(section(column(widget("heading", text="First"))), section(column(widget("heading", text="Second"), widget("text", content="body"))))


class ExportTests(StudioBase):
    def test_a_page_export_is_a_self_describing_file(self):
        d = self.put_draft(two_sections())
        payload = services.export_document(d)
        self.assertEqual((payload["format"], payload["kind"], payload["schema_version"]), ("ravix-studio", "page", 2))
        self.assertEqual(len(payload["content"]["sections"]), 2)
        json.dumps(payload)

    def test_a_section_export_holds_only_that_section(self):
        d = self.put_draft(two_sections())
        sid = d.draft["sections"][1]["id"]
        payload = services.export_document(d, section_id=sid)
        self.assertEqual(payload["kind"], "section")
        self.assertEqual(payload["content"]["id"], sid)
        with self.assertRaises(services.WorkflowError):
            services.export_document(d, section_id="n0000000")

    def test_export_can_use_the_live_version(self):
        d = self.put_draft(simple_doc("live one"))
        services.publish_draft(d.pk, self.admin)
        d = self.put_draft(simple_doc("draft two"))
        self.assertIn("live one", json.dumps(services.export_document(d, source="live")))
        self.assertIn("draft two", json.dumps(services.export_document(d, source="draft")))

    @requires_studio
    def test_the_endpoint_sends_an_attachment_with_nosniff(self):
        d = self.put_draft(two_sections())
        c = Client()
        c.force_login(self.admin)
        r = c.get(reverse("studio:api_export", args=[d.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertTrue(r["Content-Disposition"].endswith('.studio.json"'))
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertEqual(json.loads(r.content)["format"], "ravix-studio")


class ImportTests(StudioBase):
    def payload(self, doc=None, kind="page"):
        doc = doc or two_sections()
        if kind == "section":
            return {"format": "ravix-studio", "schema_version": 2, "kind": "section", "content": doc["sections"][0]}
        return {"format": "ravix-studio", "schema_version": 2, "kind": "page", "content": doc}

    def test_replace_swaps_the_page_and_keeps_the_old_draft_as_a_version(self):
        d = self.put_draft(simple_doc("before"))
        services.import_into_draft(d.pk, self.payload(), self.admin)
        d.refresh_from_db()
        self.assertEqual(len(d.draft["sections"]), 2)
        self.assertTrue(any("before" in json.dumps(v.content) for v in d.versions.filter(is_auto=True)))

    def test_append_adds_to_the_end_with_fresh_ids(self):
        d = self.put_draft(simple_doc("existing"))
        payload = self.payload()
        ids_in_file = {s["id"] for s in payload["content"]["sections"]}
        services.import_into_draft(d.pk, payload, self.admin, mode="append")
        services.import_into_draft(d.pk, payload, self.admin, mode="append")  # the same file twice must not collide
        d.refresh_from_db()
        self.assertEqual(len(d.draft["sections"]), 5)
        all_ids = [n["id"] for s in d.draft["sections"] for n in [s]]
        self.assertEqual(len(all_ids), len(set(all_ids)))
        self.assertFalse(ids_in_file & set(all_ids[1:]))
        validate_document(d.draft)

    def test_a_section_file_is_always_appended(self):
        d = self.put_draft(simple_doc("existing"))
        services.import_into_draft(d.pk, self.payload(kind="section"), self.admin)
        d.refresh_from_db()
        self.assertEqual([s["columns"][0]["widgets"][0]["props"]["text"] for s in d.draft["sections"]], ["existing", "First"])

    def test_media_from_another_site_is_cleared_not_trusted(self):
        mine = self.make_image("mine.png")
        doc = document(section(column(widget("image", media=mine.pk), widget("image", media=424242)), style={"desktop": {"bg_media": 424243}}))
        d = self.document_for()
        _, cleared = services.import_into_draft(d.pk, self.payload(doc), self.admin)
        d.refresh_from_db()
        widgets = d.draft["sections"][0]["columns"][0]["widgets"]
        self.assertEqual((widgets[0]["props"]["media"], widgets[1]["props"]["media"]), (mine.pk, None))
        self.assertNotIn("bg_media", d.draft["sections"][0]["style"].get("desktop", {}))
        self.assertEqual(cleared, 2)

    def test_invalid_files_are_refused_and_change_nothing(self):
        d = self.put_draft(simple_doc("safe"))
        rev = d.draft_rev
        bad_docs = [
            None, "text", [], {"format": "other"}, {"format": "ravix-studio", "kind": "page"}, {"format": "ravix-studio", "kind": "page", "schema_version": 2, "content": "x"},
            {"format": "ravix-studio", "kind": "weird", "schema_version": 2, "content": {}},
            {"format": "ravix-studio", "kind": "section", "schema_version": 2, "content": "x"},
        ]
        for bad in bad_docs:
            with self.assertRaises((services.WorkflowError, DocumentInvalid), msg=str(bad)):
                services.import_into_draft(d.pk, bad, self.admin)
        broken = self.payload()
        broken["content"]["sections"][0]["columns"][0]["widgets"][0]["props"]["tag"] = "script"
        with self.assertRaises(DocumentInvalid):
            services.import_into_draft(d.pk, broken, self.admin)
        newer = self.payload()
        newer["content"]["schema_version"] = 99
        with self.assertRaises(DocumentInvalid):
            services.import_into_draft(d.pk, newer, self.admin)
        d.refresh_from_db()
        self.assertEqual((d.draft_rev, "safe" in json.dumps(d.draft)), (rev, True))

    def test_a_version_1_file_from_the_old_builder_is_converted_on_import(self):
        from .test_migration import v1_document

        payload = {"format": "ravix-studio", "schema_version": 1, "kind": "page", "content": v1_document("legacy file")}
        d = self.document_for()
        services.import_into_draft(d.pk, payload, self.admin)
        d.refresh_from_db()
        self.assertEqual(d.draft["schema_version"], 2)
        self.assertIn("legacy file", json.dumps(d.draft))

    def test_the_round_trip_is_lossless(self):
        d = self.put_draft(two_sections())
        exported = services.export_document(d)
        other = self.document_for(self.post)
        services.import_into_draft(other.pk, exported, self.admin)
        other.refresh_from_db()
        a, b = clone(d.draft), clone(other.draft)
        self.assertEqual(json.dumps(a["sections"], sort_keys=True), json.dumps(b["sections"], sort_keys=True))

    @requires_studio
    def test_the_endpoint_validates_the_mode_and_reports_cleared_media(self):
        d = self.document_for()
        c = Client()
        c.force_login(self.admin)
        doc = document(section(column(widget("image", media=555555))))
        r = c.post(reverse("studio:api_import", args=[d.pk]), json.dumps({"payload": self.payload(doc), "mode": "whatever"}), content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["clearedMedia"], 1)
        self.assertEqual(c.post(reverse("studio:api_import", args=[d.pk]), json.dumps({"payload": {"format": "x"}}), content_type="application/json").status_code, 422)
