"""RENDERER_VERSION, the studio_rebuild command, the post-migrate hook, and what happens when a media file is replaced or deleted."""
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import override_settings

from apps.media_library.models import Media
from apps.studio import render as renderer, services
from apps.studio.apps import _rebuild_after_migrate
from apps.studio.models import StudioMediaUse, StudioVersion

from .helpers import requires_studio, StudioBase, column, document, section, simple_doc, widget


class RendererVersionTests(StudioBase):
    def setUp(self):
        super().setUp()
        self.d = self.put_draft(simple_doc("versioned"))
        self.v = services.publish_draft(self.d.pk, self.admin)

    def test_a_new_version_stores_the_current_renderer_version(self):
        self.v.refresh_from_db()
        self.assertEqual(self.v.renderer_version, renderer.RENDERER_VERSION)
        self.assertIn("versioned", self.v.html)
        self.assertEqual(services.stale_versions().count(), 0)

    def test_bumping_the_renderer_version_marks_live_pages_stale(self):
        with mock.patch.object(renderer, "RENDERER_VERSION", renderer.RENDERER_VERSION + 1):
            self.assertEqual(services.stale_versions().count(), 1)
            self.assertEqual(services.rebuild_stale(), 1)
            self.v.refresh_from_db()
            self.assertEqual(self.v.renderer_version, renderer.RENDERER_VERSION)
            self.assertEqual(services.stale_versions().count(), 0)

    @requires_studio
    def test_a_stale_page_is_rebuilt_on_first_display(self):
        StudioVersion.objects.filter(pk=self.v.pk).update(renderer_version=0, html="<p>old output</p>")
        r = self.client.get("/en/about/")
        self.assertContains(r, "versioned")
        self.assertNotContains(r, "old output")
        self.v.refresh_from_db()
        self.assertEqual(self.v.renderer_version, renderer.RENDERER_VERSION)

    def test_a_rebuild_never_touches_content_or_versions(self):
        before = StudioVersion.objects.get(pk=self.v.pk).content
        services.rebuild_stale(force=True)
        self.assertEqual(StudioVersion.objects.get(pk=self.v.pk).content, before)
        self.assertEqual(StudioVersion.objects.filter(document=self.d).count(), 1)

    def test_an_invalid_stored_document_is_left_as_it_was(self):
        StudioVersion.objects.filter(pk=self.v.pk).update(content={"schema_version": 99}, renderer_version=0)
        self.assertEqual(services.rebuild_stale(), 0)
        self.v.refresh_from_db()
        self.assertIn("versioned", self.v.html)  # the visible page is not blanked by a failed rebuild

    def test_the_command_reports_and_supports_all(self):
        out = StringIO()
        call_command("studio_rebuild", stdout=out)
        self.assertTrue(out.getvalue().strip())
        StudioVersion.objects.filter(pk=self.v.pk).update(renderer_version=0)
        out = StringIO()
        call_command("studio_rebuild", stdout=out)
        self.v.refresh_from_db()
        self.assertEqual(self.v.renderer_version, renderer.RENDERER_VERSION)
        call_command("studio_rebuild", "--all", stdout=StringIO())

    def test_the_post_migrate_hook_rebuilds_stale_pages_and_never_raises(self):
        StudioVersion.objects.filter(pk=self.v.pk).update(renderer_version=0)
        _rebuild_after_migrate(sender=None)
        self.v.refresh_from_db()
        self.assertEqual(self.v.renderer_version, renderer.RENDERER_VERSION)
        with mock.patch("apps.studio.services.rebuild_stale", side_effect=RuntimeError("boom")):
            _rebuild_after_migrate(sender=None)  # logged, not raised


class MediaChangeTests(StudioBase):
    def setUp(self):
        super().setUp()
        self.media = self.make_image("hero.png", size=(40, 30))
        doc = document(section(column(widget("image", media=self.media.pk), widget("heading", text="With image")),
                               style={"desktop": {"bg_media": self.media.pk}}))
        self.d = self.put_draft(doc)
        self.v = services.publish_draft(self.d.pk, self.admin)
        self.v.refresh_from_db()

    def test_media_use_is_recorded_for_the_live_version(self):
        self.assertEqual(list(StudioMediaUse.objects.filter(version=self.v).values_list("media_id", flat=True)), [self.media.pk])
        self.assertIn(self.media.file.url, self.v.html)

    def test_replacing_the_file_redraws_the_page(self):
        from django.core.files.base import ContentFile

        from apps.core.testing import png_bytes

        old_url = self.media.file.url
        self.media.file.save("hero2.png", ContentFile(png_bytes((80, 60))), save=False)
        self.media.width, self.media.height = 80, 60
        self.media.save()
        self.v.refresh_from_db()
        self.assertIn(self.media.file.url, self.v.html)
        self.assertIn('width="80"', self.v.html)
        self.assertNotIn(old_url, self.v.html)

    def test_changing_the_alt_text_redraws_the_page(self):
        self.media.alt_text = "A brand new description"
        self.media.save()
        self.v.refresh_from_db()
        self.assertIn("A brand new description", self.v.html)

    def test_deleting_the_file_blanks_the_image_and_keeps_the_page_working(self):
        url, pk = self.media.file.url, self.media.pk
        self.media.delete()
        self.v.refresh_from_db()
        self.assertNotIn(url, self.v.html)
        self.assertNotIn("<img", self.v.html.replace('<img src=""', ""))
        self.assertIn("With image", self.v.html)  # the rest of the page is intact
        r = self.client.get("/en/about/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "With image")
        self.assertFalse(StudioMediaUse.objects.filter(version=self.v, media_id=pk).exists())
        self.assertEqual(self.v.content["sections"][0]["columns"][0]["widgets"][0]["props"]["media"], pk)  # the document keeps its reference

    def test_a_draft_that_still_points_at_a_deleted_file_cannot_be_published(self):
        from apps.studio.fields import DocumentInvalid

        self.media.delete()
        with self.assertRaises(DocumentInvalid):
            services.publish_draft(self.d.pk, self.admin)
        self.assertIn("With image", services.get_document(self.page).published_version.html)  # the live page stays up

    def test_unrelated_media_changes_do_nothing(self):
        other = self.make_image("other.png")
        with mock.patch("apps.studio.services.rebuild_version") as rebuild:
            other.alt_text = "x"
            other.save()
            other.delete()
            rebuild.assert_not_called()
