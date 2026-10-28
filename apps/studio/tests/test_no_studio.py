"""With Ravix Studio switched off (server lock ``RAVIX_STUDIO_ENABLED=false``) everything else stays healthy and finished pages keep showing."""
from django.test import Client, override_settings
from django.urls import NoReverseMatch, reverse

from apps.core import modules
from apps.studio import services

from .helpers import StudioBase, simple_doc


class LockedTests(StudioBase):
    # Not a class-level @override_settings: Django disables that one in a class cleanup, after MediaTestCase restored MEDIA_ROOT, and the stale
    # (deleted) media folder would leak into later tests.
    def setUp(self):
        super().setUp()
        locked = override_settings(STUDIO_ENABLED=False)
        locked.enable()
        self.addCleanup(locked.disable)
        d = self.put_draft(simple_doc("Kept"))
        services.publish_draft(d.pk, self.admin)

    def test_the_module_reports_locked(self):
        self.assertFalse(modules.is_installed("studio"))
        self.assertTrue(modules.server_locked("studio"))
        self.assertFalse(modules.is_enabled("studio"))

    def test_every_panel_and_api_address_is_a_404(self):
        c = Client()
        c.force_login(self.root)
        for url in ("/panel/studio/", f"/panel/studio/page/{self.page.pk}/", "/panel/studio/media/", "/panel/studio/d/1/api/state/", "/panel/studio/d/1/preview/"):
            self.assertEqual(c.get(url).status_code, 404, url)

    def test_the_old_builder_addresses_do_not_leak_the_editor_either(self):
        c = Client()
        c.force_login(self.root)
        self.assertIn(c.get("/panel/builder/", follow=True).status_code, (404,))

    def test_the_classic_editor_and_the_rest_of_the_panel_work_as_before(self):
        c = Client()
        c.force_login(self.admin)
        for name, args in (("content:page_list", []), ("content:page_update", [self.page.pk]), ("content:post_list", []), ("dashboard:home", [])):
            try:
                url = reverse(name, args=args)
            except NoReverseMatch:
                continue
            r = c.get(url)
            self.assertEqual(r.status_code, 200, name)
            self.assertNotContains(r, "data-studio-edit")

    def test_finished_pages_still_show_and_the_site_is_healthy(self):
        c = Client()
        r = c.get("/en/about/")
        self.assertContains(r, "Kept")
        self.assertEqual(c.get("/en/").status_code, 200)
        self.assertEqual(c.get("/healthz").status_code, 200)
        self.assertEqual(c.get("/en/sitemap.xml").status_code, 200)

    def test_the_sidebar_has_no_studio_entry(self):
        c = Client()
        c.force_login(self.admin)
        self.assertNotContains(c.get(reverse("content:page_list")), 'href="/panel/studio/"')

    def test_management_command_still_runs(self):
        from django.core.management import call_command
        from io import StringIO

        call_command("studio_rebuild", stdout=StringIO())
