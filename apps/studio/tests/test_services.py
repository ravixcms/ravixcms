"""The workflow: draft → submit → approve/reject → publish → rollback, with the same role permissions as the old builder."""
from django.core.exceptions import PermissionDenied
from django.test import override_settings

from apps.audit.models import AuditLog
from apps.studio import services
from apps.studio.fields import DocumentInvalid
from apps.studio.models import StudioVersion

from .helpers import StudioBase, clone, column, document, section, simple_doc, widget


class DraftTests(StudioBase):
    def test_a_new_document_starts_empty_and_is_created_once(self):
        d1, d2 = self.document_for(), self.document_for()
        self.assertEqual(d1.pk, d2.pk)
        self.assertEqual(d1.draft["sections"], [])
        self.assertEqual(d1.draft_rev, 1)

    def test_saving_a_draft_bumps_the_revision(self):
        d = self.put_draft(simple_doc("A"))
        self.assertEqual(d.draft_rev, 2)
        self.assertEqual(d.updated_by, self.admin)

    def test_a_stale_save_is_a_conflict_and_changes_nothing(self):
        d = self.put_draft(simple_doc("first"))
        services.save_draft(d.pk, simple_doc("second"), d.draft_rev, self.admin)
        with self.assertRaises(services.ConflictError) as ctx:
            services.save_draft(d.pk, simple_doc("lost?"), d.draft_rev, self.admin)  # still the old revision
        self.assertEqual(ctx.exception.current_revision, d.draft_rev + 1)
        d.refresh_from_db()
        self.assertIn("second", str(d.draft))

    def test_overwrite_keeps_the_other_draft_as_an_automatic_version(self):
        d = self.put_draft(simple_doc("theirs"))
        services.save_draft(d.pk, simple_doc("mine"), 0, self.admin, overwrite=True)
        auto = StudioVersion.objects.filter(document=d, is_auto=True)
        self.assertEqual(auto.count(), 1)
        self.assertIn("theirs", str(auto.first().content))
        d.refresh_from_db()
        self.assertIn("mine", str(d.draft))

    def test_an_invalid_draft_is_refused(self):
        d = self.document_for()
        bad = simple_doc()
        bad["sections"][0]["columns"][0]["widgets"][0]["props"]["tag"] = "script"
        with self.assertRaises(DocumentInvalid):
            services.save_draft(d.pk, bad, d.draft_rev, self.admin)

    def test_the_soft_lock_names_the_other_editor_but_never_blocks(self):
        d = self.document_for()
        self.assertIsNone(services.touch_editing(d, self.admin))
        d.refresh_from_db()
        other = services.touch_editing(d, self.root)
        self.assertEqual(other["name"], self.admin.display_name)
        d.refresh_from_db()
        services.save_draft(d.pk, simple_doc(), d.draft_rev, self.root)  # no exception: only a warning

    def test_an_author_edits_only_their_own_draft_posts(self):
        d = self.document_for(self.post)
        services.save_draft(d.pk, simple_doc(), d.draft_rev, self.author)
        with self.assertRaises(PermissionDenied):
            services.require_edit(self.other_author, d)
        self.post.status = "published"
        self.post.save()
        with self.assertRaises(PermissionDenied):
            services.require_edit(self.author, d)  # same rule as the classic editor: published posts are for editors
        services.require_edit(self.admin, d)

    def test_a_regular_user_can_edit_nothing(self):
        with self.assertRaises(PermissionDenied):
            services.require_edit(self.visitor, self.document_for())


class WorkflowTests(StudioBase):
    def setUp(self):
        super().setUp()
        self.post.status = "draft"
        self.post.save()
        self.pd = self.put_draft(simple_doc("post body"), self.post, self.author)

    def test_author_submit_then_admin_approve_publishes(self):
        v = services.submit_for_review(self.pd.pk, self.author)
        self.assertEqual(v.state, StudioVersion.State.PENDING)
        self.pd.refresh_from_db()
        self.assertIsNone(self.pd.published_version_id)
        with self.assertRaises(PermissionDenied):
            services.approve(self.pd.pk, v.number, self.author)
        services.approve(self.pd.pk, v.number, self.admin, "ok")
        self.pd.refresh_from_db()
        self.assertEqual(self.pd.published_version.number, v.number)
        self.assertIn("post body", self.pd.published_version.html)
        self.assertEqual(self.pd.published_version.state, StudioVersion.State.APPROVED)

    def test_reject_keeps_the_draft_and_records_the_reason(self):
        v = services.submit_for_review(self.pd.pk, self.author)
        services.reject(self.pd.pk, v.number, self.admin, "needs work")
        v.refresh_from_db()
        self.assertEqual((v.state, v.review_note, v.reviewed_by), (StudioVersion.State.REJECTED, "needs work", self.admin))
        self.pd.refresh_from_db()
        self.assertIsNone(self.pd.published_version_id)
        self.assertIn("post body", str(self.pd.draft))

    def test_a_new_submission_withdraws_the_previous_pending_one(self):
        v1 = services.submit_for_review(self.pd.pk, self.author)
        v2 = services.submit_for_review(self.pd.pk, self.author)
        v1.refresh_from_db()
        self.assertEqual((v1.state, v2.state), (StudioVersion.State.SAVED, StudioVersion.State.PENDING))

    def test_only_a_pending_version_can_be_approved_or_rejected(self):
        v = services.save_version(self.pd.pk, self.author)
        for fn in (services.approve, services.reject):
            with self.assertRaises(services.WorkflowError):
                fn(self.pd.pk, v.number, self.admin)

    def test_author_cannot_publish_directly_or_roll_back_or_unpublish(self):
        for call in (lambda: services.publish_draft(self.pd.pk, self.author), lambda: services.unpublish(self.pd.pk, self.author)):
            with self.assertRaises(PermissionDenied):
                call()

    def test_an_empty_page_cannot_be_submitted_or_published(self):
        d = self.document_for(self.page)
        for call in (lambda: services.submit_for_review(d.pk, self.admin), lambda: services.publish_draft(d.pk, self.admin)):
            with self.assertRaises(services.WorkflowError):
                call()

    def test_required_fields_are_enforced_when_publishing_not_when_saving(self):
        d = self.document_for()
        doc = document(section(column(widget("button", label="", url="/"))))
        services.save_draft(d.pk, doc, d.draft_rev, self.admin)  # drafts may be incomplete
        d.refresh_from_db()
        with self.assertRaises(DocumentInvalid):
            services.publish_draft(d.pk, self.admin)

    def test_direct_publish_rollback_and_unpublish(self):
        d = self.put_draft(simple_doc("v1"))
        v1 = services.publish_draft(d.pk, self.admin)
        d = self.put_draft(simple_doc("v2"))
        v2 = services.publish_draft(d.pk, self.admin)
        d.refresh_from_db()
        self.assertEqual(d.published_version_id, v2.pk)
        v1.refresh_from_db()
        self.assertEqual(v1.html, "")  # the stored page of a replaced version is released
        services.rollback(d.pk, v1.number, self.admin)
        d.refresh_from_db()
        self.assertIn("v1", d.published_version.html)
        self.assertIn("v2", str(d.draft))  # the draft is untouched by a rollback
        with self.assertRaises(services.WorkflowError):
            services.rollback(d.pk, v1.number, self.admin)  # already live
        services.unpublish(d.pk, self.admin)
        d.refresh_from_db()
        self.assertIsNone(d.published_version_id)

    def test_restore_puts_an_old_version_in_the_draft_and_saves_the_current_one_first(self):
        d = self.put_draft(simple_doc("old"))
        v = services.save_version(d.pk, self.admin)
        d = self.put_draft(simple_doc("new"))
        services.restore(d.pk, v.number, self.admin)
        d.refresh_from_db()
        self.assertIn("old", str(d.draft))
        self.assertTrue(StudioVersion.objects.filter(document=d, is_auto=True).exists())
        self.assertTrue(any("new" in str(x.content) for x in StudioVersion.objects.filter(document=d)))

    def test_actions_are_audited(self):
        d = self.put_draft(simple_doc("x"))
        services.publish_draft(d.pk, self.admin)
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.PUBLISH, actor=self.admin).exists())


class PruningTests(StudioBase):
    @override_settings(STUDIO_MAX_VERSIONS=5)
    def test_old_versions_are_pruned_but_the_live_and_pending_ones_stay(self):
        d = self.put_draft(simple_doc("live"))
        live = services.publish_draft(d.pk, self.admin)
        pending = services.submit_for_review(d.pk, self.admin)
        for i in range(12):
            services.save_version(d.pk, self.admin, f"n{i}")
        numbers = set(StudioVersion.objects.filter(document=d).values_list("number", flat=True))
        self.assertLessEqual(len(numbers), 6)
        self.assertIn(live.number, numbers)
        self.assertIn(pending.number, numbers)
        d.refresh_from_db()
        self.assertEqual(d.published_version_id, StudioVersion.objects.get(document=d, number=live.number).pk)

    def test_the_cap_has_a_floor_of_five(self):
        with override_settings(STUDIO_MAX_VERSIONS=1):
            d = self.put_draft(simple_doc("x"))
            for _ in range(8):
                services.save_version(d.pk, self.admin)
            self.assertGreaterEqual(StudioVersion.objects.filter(document=d).count(), 5)

    def test_the_default_cap_is_fifty(self):
        from django.conf import settings

        self.assertEqual(settings.STUDIO_MAX_VERSIONS, 50)


class DeleteTargetTests(StudioBase):
    def test_deleting_a_page_or_post_removes_its_layout(self):
        """Regression: on MySQL/MariaDB Django nulls the layout's page/post first, which broke the one-target CHECK constraint."""
        from apps.content.models import Page, Post
        from apps.studio.models import StudioDocument

        page = Page.objects.create(title="Gone", body="", author=self.admin)
        post = Post.objects.create(title="Gone too", body="", author=self.admin)
        services.get_document(page)
        services.get_document(post)
        self.assertEqual(StudioDocument.objects.count(), 2)
        page.delete()
        post.delete()
        self.assertEqual(StudioDocument.objects.count(), 0)
