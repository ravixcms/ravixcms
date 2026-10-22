from django.test import TestCase
from django.urls import reverse

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.audit.models import AuditLog
from apps.content.models import Category, Page, Post, PublishStatus
from apps.core.models import SiteSettings
from apps.themes import services
from apps.themes.models import ThemeConfig

from .helpers import ThemeDirMixin

APPEARANCE = {
    "primary": "#b91c1c", "background": "#ffffff", "text": "#1d2433", "font": "vazirmatn", "font_size": "md",
    "logo_media": "", "favicon_media": "",
}


class AccessTests(TestCase):
    def test_only_theme_managers_can_open_pages(self):
        urls = [reverse(n) for n in ("themes:list", "themes:customize", "themes:layout", "themes:menus", "themes:home")]
        for url in urls:  # ناشناس ← ورود
            self.assertEqual(self.client.get(url).status_code, 302, url)
        for role in (Role.USER, Role.AUTHOR):
            self.client.force_login(make_user(f"u_{role}", role=role))
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, 403, (role, url))
        self.client.force_login(make_user("adm", role=Role.ADMIN))
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_actions_are_post_only_and_protected(self):
        self.client.force_login(make_user("adm", role=Role.ADMIN))
        for name in ("themes:publish", "themes:discard", "themes:preview_start", "themes:preview_stop"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 405, name)
        self.client.force_login(make_user("auth", role=Role.AUTHOR))
        self.assertEqual(self.client.post(reverse("themes:publish")).status_code, 403)
        self.assertEqual(self.client.post(reverse("themes:activate", args=["default"])).status_code, 403)


class ThemeManagementTests(ThemeDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.admin = make_user("adm", role=Role.ADMIN)
        self.client.force_login(self.admin)
        self.install_theme("custom")

    def test_list_shows_installed_themes(self):
        resp = self.client.get(reverse("themes:list"))
        self.assertContains(resp, "پیش‌فرض راویکس")
        self.assertContains(resp, "قالب custom")

    def test_invalid_theme_is_listed_but_cannot_be_activated(self):
        self.install_theme("broken", edit=lambda p: (p / "templates" / "home.html").unlink())
        self.assertContains(self.client.get(reverse("themes:list")), "نامعتبر")
        resp = self.client.post(reverse("themes:activate", args=["broken"]), follow=True)
        self.assertContains(resp, "نامعتبر")
        self.assertEqual(ThemeConfig.load().active_theme, "default")

    def test_unknown_theme_cannot_be_activated(self):
        self.client.post(reverse("themes:activate", args=["nope"]))
        self.assertEqual(ThemeConfig.load().active_theme, "default")

    def test_activate_and_deactivate_persist_in_database(self):
        self.client.post(reverse("themes:activate", args=["custom"]))
        self.assertEqual(ThemeConfig.load().active_theme, "custom")
        self.assertEqual(ThemeConfig.load().published["theme"], "custom")
        self.assertTrue(AuditLog.objects.filter(object_type="ظاهر سایت").exists())
        self.client.post(reverse("themes:deactivate", args=["custom"]))
        self.assertEqual(ThemeConfig.load().active_theme, "default")

    def test_default_theme_cannot_be_deactivated(self):
        self.client.post(reverse("themes:deactivate", args=["default"]))
        self.assertEqual(ThemeConfig.load().active_theme, "default")

    def test_theme_change_does_not_touch_content(self):
        Post.objects.create(title="n", slug="n", body="x", status=PublishStatus.PUBLISHED)
        Page.objects.create(title="p", slug="p", body="x", status="published")
        Category.objects.create(name="c")
        site_name = SiteSettings.load().site_name
        before = (Post.objects.count(), Page.objects.count(), Category.objects.count(),
                  Post.objects.get().updated_at, Page.objects.get().updated_at)
        self.client.post(reverse("themes:activate", args=["custom"]))
        self.client.post(reverse("themes:customize"), APPEARANCE)
        self.client.post(reverse("themes:publish"))
        self.client.post(reverse("themes:activate", args=["default"]))
        after = (Post.objects.count(), Page.objects.count(), Category.objects.count(),
                 Post.objects.get().updated_at, Page.objects.get().updated_at)
        self.assertEqual(before, after)
        self.assertEqual(SiteSettings.load().site_name, site_name)

    def test_removing_theme_folder_falls_back_without_data_loss(self):
        self.client.post(reverse("themes:activate", args=["custom"]))
        import shutil

        shutil.rmtree(self.extra_dir / "custom")
        from apps.themes import registry

        registry.clear_cache()
        self.assertEqual(ThemeConfig.load().active_theme, "custom")  # انتخاب مدیر حفظ می‌شود
        self.assertEqual(self.client.get("/").status_code, 200)  # ولی سایت با قالب پیش‌فرض بالا می‌ماند
        self.assertContains(self.client.get(reverse("themes:list")), "پیدا نشد")


class DraftWorkflowTests(ThemeDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(make_user("adm", role=Role.ADMIN))

    def save_appearance(self, **over):
        return self.client.post(reverse("themes:customize"), {**APPEARANCE, **over})

    def test_save_goes_to_draft_not_published(self):
        resp = self.save_appearance()
        self.assertEqual(resp.status_code, 302)
        cfg = ThemeConfig.load()
        self.assertTrue(cfg.has_draft)
        self.assertEqual(cfg.draft["appearance"]["primary"], "#b91c1c")
        self.assertEqual(cfg.published["appearance"]["primary"], "#4f46e5")
        self.assertNotContains(self.client.get("/"), "--rv-primary:#b91c1c")  # بدون پیش‌نمایش

    def test_unreadable_colors_are_rejected(self):
        resp = self.save_appearance(text="#cccccc")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "کنتراست")
        self.assertFalse(ThemeConfig.load().has_draft)

    def test_malformed_color_and_font_rejected(self):
        for bad in ({"primary": "red;}"}, {"font": "comic"}, {"background": "#12"}, {"font_size": "xl"}):
            self.assertEqual(self.save_appearance(**bad).status_code, 200, bad)
        self.assertFalse(ThemeConfig.load().has_draft)

    def test_publish_applies_and_clears_draft(self):
        self.save_appearance()
        self.client.post(reverse("themes:publish"))
        cfg = ThemeConfig.load()
        self.assertFalse(cfg.has_draft)
        self.assertEqual(cfg.published["appearance"]["primary"], "#b91c1c")
        self.assertIsNotNone(cfg.published_at)
        anon = self.client_class()
        self.assertContains(anon.get("/"), "--rv-primary:#b91c1c")

    def test_discard_restores_published(self):
        self.save_appearance()
        self.client.post(reverse("themes:discard"))
        cfg = ThemeConfig.load()
        self.assertFalse(cfg.has_draft)
        self.assertEqual(cfg.published["appearance"]["primary"], "#4f46e5")

    def test_publish_and_discard_without_draft_report_error(self):
        resp = self.client.post(reverse("themes:publish"), follow=True)
        self.assertContains(resp, "وجود ندارد")
        resp = self.client.post(reverse("themes:discard"), follow=True)
        self.assertContains(resp, "وجود ندارد")

    def test_corrupt_draft_cannot_be_published(self):
        cfg = ThemeConfig.load()
        cfg.draft = {"appearance": {"primary": "#ffff00", "background": "#ffffff", "text": "#000000"}}
        cfg.save()
        self.client.post(reverse("themes:publish"))
        cfg = ThemeConfig.load()
        self.assertEqual(cfg.published["appearance"]["primary"], "#4f46e5")

    def test_layout_menus_and_home_save_to_draft(self):
        cat = Category.objects.create(name="فناوری")
        page = Page.objects.create(title="درباره", slug="about", body="x", status="published")
        self.client.post(reverse("themes:layout"), {
            "header_show_tagline": "on", "header_cta_label": "تماس", "header_cta_url": "/contact/",
            "footer_text": "متن پانویس", "footer_show_menu": "on", "footer_social": "Instagram | https://instagram.com/x",
        })
        draft = ThemeConfig.load().draft
        self.assertEqual(draft["header"]["cta_url"], "/contact/")
        self.assertEqual(draft["footer"]["social"][0]["url"], "https://instagram.com/x")
        self.assertFalse(draft["header"]["sticky"])

        data = {"header-TOTAL_FORMS": 2, "header-INITIAL_FORMS": 0, "header-MIN_NUM_FORMS": 0, "header-MAX_NUM_FORMS": 20,
                "footer-TOTAL_FORMS": 0, "footer-INITIAL_FORMS": 0, "footer-MIN_NUM_FORMS": 0, "footer-MAX_NUM_FORMS": 20,
                "header-0-label": "خانه", "header-0-kind": "home", "header-0-ORDER": 2,
                "header-1-label": "درباره", "header-1-kind": "page", "header-1-page": page.pk, "header-1-ORDER": 1}
        resp = self.client.post(reverse("themes:menus"), data)
        self.assertEqual(resp.status_code, 302, getattr(resp, "context", None) and resp.context["menu_formsets"][0][2].errors)
        labels = [i["label"] for i in ThemeConfig.load().draft["menus"]["header"]]
        self.assertEqual(labels, ["درباره", "خانه"])

        sections = {"sections-TOTAL_FORMS": 2, "sections-INITIAL_FORMS": 0, "sections-MIN_NUM_FORMS": 0, "sections-MAX_NUM_FORMS": 20,
                    "sections-0-type": "hero", "sections-0-title": "خوش آمدید", "sections-0-ORDER": 1,
                    "sections-1-type": "category_posts", "sections-1-category": cat.pk, "sections-1-count": 3, "sections-1-ORDER": 2}
        self.assertEqual(self.client.post(reverse("themes:home"), sections).status_code, 302)
        types = [s["type"] for s in ThemeConfig.load().draft["home"]["sections"]]
        self.assertEqual(types, ["hero", "category_posts"])

    def test_dangerous_links_rejected(self):
        resp = self.client.post(reverse("themes:layout"), {"header_cta_label": "x", "header_cta_url": "javascript:alert(1)"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(ThemeConfig.load().has_draft)

    def test_theme_choice_preview_only_changes_draft(self):
        self.install_theme("custom")
        self.client.post(reverse("themes:preview_theme", args=["custom"]))
        cfg = ThemeConfig.load()
        self.assertEqual(cfg.active_theme, "default")
        self.assertEqual(cfg.draft["theme"], "custom")
        self.assertIn('data-theme="custom"', self.client.get("/").content.decode())
        self.assertIn('data-theme="default"', self.client_class().get("/").content.decode())
        services_doc = services.published_doc(cfg)
        self.assertEqual(services_doc["theme"], "default")
