"""The reversible import of the first page builder's layouts, versions and approval states into Ravix Studio."""
import copy

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from apps.studio.legacy_import import v1_to_v2
from apps.studio.schema import validate_document

def _targets(studio_node):
    """Every other app at its latest migration, Studio at ``studio_node`` (so the historical models match the real tables)."""
    others = [n for n in MigrationExecutor(connection).loader.graph.leaf_nodes() if n[0] != "studio"]
    return others + [("studio", studio_node)]


BEFORE_NODE, AFTER_NODE = "0001_initial", "0002_import_from_builder"


def v1_document(text="Title"):
    return {"schema_version": 1, "meta": {"show_title": True}, "sections": [
        {"id": "n0000001", "props": {"width": "boxed", "padding_y": 40, "bg_color": "#eeeeee", "bg_media": None, "text_color": "", "border_width": 0, "border_style": "solid", "border_color": "", "radius": 0},
         "rows": [{"id": "n0000002", "props": {"gap": 24, "valign": "start", "stack_at": "mobile"}, "columns": [
             {"id": "n0000003", "props": {"span": 12, "bg_color": "", "padding": 0, "border_width": 0, "border_style": "solid", "border_color": "", "radius": 0}, "elements": [
                 {"id": "n0000004", "type": "heading", "props": {"text": text, "level": "2", "font_size": 32, "color": "", "align": "start", "bg_color": "", "padding": 0, "border_width": 0, "border_style": "solid", "border_color": "", "radius": 0, "margin_bottom": 16}},
             ]}]}]}]}


class ImportFromBuilderTests(TransactionTestCase):
    def migrate(self, node):
        targets = _targets(node)
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        executor = MigrationExecutor(connection)
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        self.migrate(AFTER_NODE)  # leave the schema as the rest of the suite expects it
        super().tearDown()

    def seed_old(self, apps):
        User = apps.get_model("accounts", "User")
        Page = apps.get_model("content", "Page")
        PageDesign = apps.get_model("builder", "PageDesign")
        DesignVersion = apps.get_model("builder", "DesignVersion")
        user = User.objects.create(username="legacy", email="l@example.com", language="fa")
        reviewer = User.objects.create(username="rev", email="r@example.com", language="fa")
        p1 = Page.objects.create(title="One", slug="one", body="b", status="published")
        p2 = Page.objects.create(title="Two", slug="two", body="b", status="published")
        Page.objects.create(title="No design", slug="none", body="b", status="published")
        d1 = PageDesign.objects.create(page=p1, draft=v1_document("draft one"), revision=7, updated_by=user)
        v1 = DesignVersion.objects.create(design=d1, number=1, document=v1_document("v1"), state="approved", note="first", created_by=user, reviewed_by=reviewer, review_note="fine")
        DesignVersion.objects.create(design=d1, number=2, document=v1_document("v2"), state="pending", created_by=user)
        DesignVersion.objects.create(design=d1, number=3, document=v1_document("v3"), state="rejected", created_by=user, reviewed_by=reviewer, review_note="no")
        DesignVersion.objects.create(design=d1, number=4, document=v1_document("auto"), state="saved", is_auto=True)
        PageDesign.objects.filter(pk=d1.pk).update(published_version=v1)
        PageDesign.objects.create(page=p2, draft=v1_document("only a draft"))
        return p1, p2

    def test_forward_copies_everything_and_keeps_the_old_rows(self):
        old = self.migrate(BEFORE_NODE)
        p1, p2 = self.seed_old(old)
        new = self.migrate(AFTER_NODE)
        Doc, Ver = new.get_model("studio", "StudioDocument"), new.get_model("studio", "StudioVersion")
        self.assertEqual(Doc.objects.count(), 2)
        d1 = Doc.objects.get(page_id=p1.pk)
        self.assertEqual(d1.draft_rev, 7)
        self.assertEqual(d1.draft["schema_version"], 2)
        self.assertIn("draft one", str(d1.draft))
        self.assertEqual(sorted(Ver.objects.filter(document=d1).values_list("number", "state", "is_auto")),
                         [(1, "approved", False), (2, "pending", False), (3, "rejected", False), (4, "saved", True)])
        live = d1.published_version
        self.assertEqual(live.number, 1)
        self.assertEqual((live.note, live.review_note, live.reviewed_by_id is not None), ("first", "fine", True))
        self.assertIsNone(Doc.objects.get(page_id=p2.pk).published_version_id)
        self.assertEqual(Ver.objects.filter(document__page_id=p2.pk).count(), 0)
        self.assertEqual(new.get_model("builder", "PageDesign").objects.count(), 2)  # the old rows stay (the rollback source)
        self.assertEqual(new.get_model("builder", "DesignVersion").objects.count(), 4)

    def test_converted_documents_are_valid_for_the_current_schema(self):
        old = self.migrate(BEFORE_NODE)
        self.seed_old(old)
        new = self.migrate(AFTER_NODE)
        for v in new.get_model("studio", "StudioVersion").objects.all():
            validate_document(v.content)
        for d in new.get_model("studio", "StudioDocument").objects.all():
            validate_document(d.draft)

    def test_the_published_page_is_drawn_after_the_migration(self):
        from apps.studio import services
        from apps.studio.models import StudioDocument

        old = self.migrate(BEFORE_NODE)
        p1, _ = self.seed_old(old)
        self.migrate(AFTER_NODE)
        d = StudioDocument.objects.get(page_id=p1.pk)
        self.assertEqual(d.published_version.html, "")  # empty until the first display / post_migrate hook / studio_rebuild
        self.assertEqual(services.rebuild_stale(), 1)
        d.published_version.refresh_from_db()
        self.assertIn("v1", d.published_version.html)

    def test_a_site_that_used_the_builder_keeps_the_editor_switched_on(self):
        from apps.core.models import ModuleState

        old = self.migrate(BEFORE_NODE)
        self.seed_old(old)
        self.migrate(AFTER_NODE)
        self.assertTrue(ModuleState.objects.get(key="studio").enabled)

    def test_a_site_without_designs_gets_no_module_row(self):
        from apps.core.models import ModuleState

        self.migrate(BEFORE_NODE)
        self.migrate(AFTER_NODE)
        self.assertFalse(ModuleState.objects.filter(key="studio").exists())

    def test_reverse_removes_only_what_was_imported(self):
        old = self.migrate(BEFORE_NODE)
        p1, p2 = self.seed_old(old)
        new = self.migrate(AFTER_NODE)
        Doc = new.get_model("studio", "StudioDocument")
        made_in_studio = Doc.objects.create(page_id=new.get_model("content", "Page").objects.get(slug="none").pk, draft={"schema_version": 2, "meta": {}, "sections": []})
        back = self.migrate(BEFORE_NODE)
        self.assertEqual(list(back.get_model("studio", "StudioDocument").objects.values_list("pk", flat=True)), [made_in_studio.pk])
        self.assertEqual(back.get_model("builder", "PageDesign").objects.count(), 2)
        self.assertEqual(back.get_model("builder", "DesignVersion").objects.count(), 4)

    def test_forward_is_idempotent(self):
        old = self.migrate(BEFORE_NODE)
        self.seed_old(old)
        self.migrate(AFTER_NODE)
        self.migrate(BEFORE_NODE)
        self.migrate(AFTER_NODE)
        new = self.migrate(AFTER_NODE)
        self.assertEqual(new.get_model("studio", "StudioDocument").objects.count(), 2)
        self.assertEqual(new.get_model("studio", "StudioVersion").objects.count(), 4)

    def test_a_page_that_already_has_a_studio_layout_is_skipped(self):
        old = self.migrate(BEFORE_NODE)
        p1, _ = self.seed_old(old)
        old.get_model("studio", "StudioDocument").objects.create(page_id=p1.pk, draft={"schema_version": 2, "meta": {}, "sections": []})
        new = self.migrate(AFTER_NODE)
        d = new.get_model("studio", "StudioDocument").objects.get(page_id=p1.pk)
        self.assertEqual(d.draft["sections"], [])  # the Studio layout was not overwritten
        self.assertIsNone(d.legacy_design_id)

    def test_an_unconvertible_document_is_kept_as_it_was(self):
        old = self.migrate(BEFORE_NODE)
        p1, _ = self.seed_old(old)
        bad = {"schema_version": 1, "sections": "not a list"}
        old.get_model("builder", "PageDesign").objects.filter(page_id=p1.pk).update(draft=bad)
        new = self.migrate(AFTER_NODE)  # must not crash
        self.assertEqual(new.get_model("studio", "StudioDocument").objects.get(page_id=p1.pk).draft, bad)

    def test_the_converter_does_not_mutate_its_input(self):
        doc = v1_document()
        snapshot = copy.deepcopy(doc)
        v1_to_v2(doc)
        self.assertEqual(doc, snapshot)
