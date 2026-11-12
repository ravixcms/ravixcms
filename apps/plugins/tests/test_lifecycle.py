import json
import os
import sys

from django.apps import apps
from django.db import connection
from django.test import Client, override_settings
from django.urls import reverse

from apps.plugins import discovery, registry, services, state
from apps.plugins.models import PluginEvent, PluginRecord
from apps.plugins.services import PluginError
from apps.plugins.tests.helpers import PluginTestCase, base_manifest


def table_exists(name):
    return name in connection.introspection.table_names()


class DiscoveryTests(PluginTestCase):
    def test_only_configured_dirs_and_invalid_listed_with_reason(self):
        self.write_plugin("good_one")
        bad = self.write_plugin("bad_one", manifest={"slug": "bad_one", "name": "x"})
        (self.tmp / "nomanifest").mkdir()
        (self.tmp / "nomanifest" / "__init__.py").write_text("")
        (self.tmp / ".hidden").mkdir()
        found = {d.slug: d for d in discovery.discover()}
        self.assertTrue(found["good_one"].valid)
        self.assertFalse(found["bad_one"].valid)
        self.assertTrue(found["bad_one"].errors)
        self.assertFalse(found["nomanifest"].valid)
        self.assertNotIn(".hidden", found)
        self.assertTrue(bad.exists())

    def test_discovery_never_imports_code(self):
        marker = self.tmp / "executed.txt"
        self.write_plugin("sneaky", init=f"open({str(marker)!r}, 'w').write('x')")
        services.overview()
        discovery.discover()
        self.assertFalse(marker.exists())
        self.assertNotIn("ravix_plugins.sneaky", sys.modules)

    def test_symlink_rejected(self):
        root = self.write_plugin("linky")
        (root / "evil.py").symlink_to("/etc/passwd")
        item = discovery.find("linky")
        self.assertFalse(item.valid)
        self.assertTrue(any("symlink" in e or "پیوند" in e for e in item.errors))

    def test_slug_folder_mismatch_invalid(self):
        self.write_plugin("folder_a", manifest=base_manifest("folder_b"))
        self.assertFalse(discovery.find("folder_a").valid)


class InstallSecurityTests(PluginTestCase):
    def test_install_and_activate_are_admin_only(self):
        self.write_plugin("demo")
        for user in (self.author, make_user_anon()):
            with self.assertRaises(PluginError):
                services.install("demo", user, self.fp("demo"))
        self.install("demo")
        with self.assertRaises(PluginError):
            services.activate("demo", self.author)
        with self.assertRaises(PluginError):
            services.deactivate("demo", self.author)

    def test_views_forbidden_for_non_admins_and_anonymous(self):
        self.write_plugin("demo")
        self.login(self.author)
        for name, args in (("plugins:list", []), ("plugins:detail", ["demo"]), ("plugins:install", ["demo"]), ("plugins:events", [])):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403, name)
        for name in ("plugins:activate", "plugins:deactivate", "plugins:uninstall"):
            self.assertEqual(self.client.post(reverse(name, args=["demo"])).status_code, 403, name)
        self.assertEqual(self.client.post(reverse("plugins:install", args=["demo"]), {"fingerprint": self.fp("demo"), "trust": "on"}).status_code, 403)
        self.assertFalse(PluginRecord.objects.exists())
        anon = Client()
        self.assertEqual(anon.get(reverse("plugins:list")).status_code, 302)
        self.assertEqual(anon.post(reverse("plugins:activate", args=["demo"])).status_code, 302)

    def test_csrf_enforced_on_state_changes(self):
        self.write_plugin("demo")
        self.install("demo")
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.admin)
        self.assertEqual(c.post(reverse("plugins:activate", args=["demo"])).status_code, 403)
        self.assertFalse(PluginRecord.objects.get(slug="demo").is_active)

    def test_install_requires_trust_and_matching_fingerprint(self):
        self.write_plugin("demo")
        self.login(self.admin)
        url = reverse("plugins:install", args=["demo"])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertContains(self.client.get(url), self.fp("demo"))
        self.client.post(url, {"fingerprint": self.fp("demo")})  # بدون تأیید اعتماد
        self.assertFalse(PluginRecord.objects.exists())
        self.client.post(url, {"fingerprint": "0" * 64, "trust": "on"})  # اثرانگشت نادرست
        self.assertFalse(PluginRecord.objects.exists())
        self.client.post(url, {"fingerprint": self.fp("demo"), "trust": "on"})
        rec = PluginRecord.objects.get(slug="demo")
        self.assertFalse(rec.is_active)  # نصب ≠ فعال‌سازی
        self.assertEqual(rec.fingerprint, self.fp("demo"))

    def test_code_changed_between_review_and_install_is_refused(self):
        root = self.write_plugin("demo")
        reviewed = self.fp("demo")
        (root / "views.py").write_text("# changed after review\n")
        with self.assertRaises(PluginError):
            services.install("demo", self.admin, reviewed)
        self.assertFalse(PluginRecord.objects.exists())

    def test_code_not_executed_until_activation(self):
        marker = self.tmp / "executed.txt"
        self.write_plugin("lazy", init=f"open({str(marker)!r}, 'a').write('x')")
        self.install("lazy")
        self.assertFalse(marker.exists(), "نصب افزونهٔ بدون مدل نباید کد را اجرا کند")
        services.activate("lazy", self.admin)
        self.assertTrue(marker.exists())

    def test_unapproved_plugin_cannot_be_imported(self):
        self.write_plugin("ghost")
        with self.assertRaises(ImportError):
            __import__("ravix_plugins.ghost")

    def test_incompatible_plugin_refused(self):
        self.write_plugin("future", manifest=base_manifest("future", ravix=">=9.0.0"))
        with self.assertRaises(PluginError) as cm:
            self.install("future")
        self.assertIn("ناسازگار", str(cm.exception))
        row = services.get_row("future")
        self.assertEqual(row.status, "incompatible")

    def test_dependencies_checked(self):
        self.write_plugin("base_pl")
        self.write_plugin("child_pl", manifest=base_manifest("child_pl", requires={"base_pl": ">=1.0.0"}))
        with self.assertRaises(PluginError):
            self.install("child_pl")  # وابستگی نصب/فعال نیست
        self.install_activate("base_pl")
        self.install_activate("child_pl")
        with self.assertRaises(PluginError):
            services.deactivate("base_pl", self.admin)  # وابسته فعال است
        services.deactivate("child_pl", self.admin)
        services.deactivate("base_pl", self.admin)

    def test_safe_mode_and_kill_switch(self):
        self.write_plugin("demo")
        self.install_activate("demo")
        self.assertEqual(Client().get("/ext/demo/ping").status_code, 200)
        with override_settings(RAVIX_PLUGINS_SAFE_MODE=True):
            self.invalidate()
            self.assertEqual(Client().get("/ext/demo/ping").status_code, 404)
            self.login(self.admin)
            self.assertEqual(self.client.get(reverse("plugins:list")).status_code, 200)  # مدیریت در دسترس می‌ماند
        with override_settings(RAVIX_PLUGINS_ENABLED=False):
            self.invalidate()
            self.assertEqual(Client().get("/ext/demo/ping").status_code, 404)
        self.invalidate()
        self.assertEqual(Client().get("/ext/demo/ping").status_code, 200)


def make_user_anon():
    from django.contrib.auth.models import AnonymousUser

    return AnonymousUser()


class ActivationRoutingTests(PluginTestCase):
    def setUp(self):
        super().setUp()
        self.write_plugin("demo")
        self.install_activate("demo")

    def test_public_route_and_methods(self):
        self.assertEqual(Client().get("/ext/demo/ping").content, b"PONG")
        self.assertEqual(Client().get("/ext/demo/ping/").content, b"PONG")
        self.assertEqual(Client().post("/ext/demo/ping").status_code, 405)  # فقط GET اعلام شده
        self.assertEqual(Client().get("/ext/demo/other").status_code, 404)
        self.assertEqual(Client().get("/ext/nothere/ping").status_code, 404)

    def test_admin_page_requires_declared_capability(self):
        url = reverse("plugins:page", args=["demo", "home"])
        self.assertEqual(Client().get(url).status_code, 302)  # ناشناس ← ورود
        self.login(self.author)
        self.assertEqual(self.client.get(url).status_code, 403)  # نقش پایین‌تر مجوز افزونه را ندارد
        self.login(self.admin)
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b"HOME:hi")
        self.assertIn("no-store", r["Cache-Control"])

    def test_plugin_capability_only_for_admin_roles(self):
        self.assertTrue(self.admin.has_cap("demo.use"))
        self.assertTrue(self.superadmin.has_cap("demo.use"))
        self.assertFalse(self.author.has_cap("demo.use"))
        self.assertFalse(self.admin.has_cap("demo.nonexistent"))
        self.assertFalse(self.admin.has_cap("users.manage.fake"))

    def test_nav_item_visible_to_admin_only(self):
        self.login(self.admin)
        html = self.client.get(reverse("dashboard:home")).content.decode()
        self.assertIn(reverse("plugins:page", args=["demo", "home"]), html)
        self.login(self.author)
        html = self.client.get(reverse("dashboard:home")).content.decode()
        self.assertNotIn("/panel/plugins/x/demo/", html)

    def test_deactivate_removes_everything(self):
        services.deactivate("demo", self.admin)
        self.assertEqual(Client().get("/ext/demo/ping").status_code, 404)
        self.login(self.admin)
        self.assertEqual(self.client.get(reverse("plugins:page", args=["demo", "home"])).status_code, 404)
        self.assertFalse(self.admin.has_cap("demo.use"))
        self.assertNotIn("/panel/plugins/x/demo/", self.client.get(reverse("dashboard:home")).content.decode())

    def test_settings_validated_and_applied(self):
        self.login(self.admin)
        url = reverse("plugins:settings", args=["demo"])
        self.assertEqual(self.client.get(url).status_code, 200)
        bad = self.client.post(url, {"greeting": "x" * 50})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(PluginRecord.objects.get(slug="demo").config["greeting"], "hi")
        self.assertEqual(self.client.post(url, {"greeting": "سلام", "evil": "1"}).status_code, 302)
        self.assertEqual(PluginRecord.objects.get(slug="demo").config, {"greeting": "سلام"})
        self.assertEqual(self.client.get(reverse("plugins:page", args=["demo", "home"])).content.decode(), "HOME:سلام")
        self.login(self.author)
        self.assertEqual(self.client.post(url, {"greeting": "h"}).status_code, 403)

    def test_events_logged_and_visible(self):
        kinds = set(PluginEvent.objects.filter(slug="demo").values_list("kind", flat=True))
        self.assertTrue({"install", "activate"} <= kinds)
        self.login(self.admin)
        r = self.client.get(reverse("plugins:events") + "?plugin=demo")
        self.assertContains(r, "install")

    def test_uninstall_requires_inactive(self):
        with self.assertRaises(PluginError):
            services.uninstall("demo", self.admin)
        services.deactivate("demo", self.admin)
        services.uninstall("demo", self.admin)
        self.assertFalse(PluginRecord.objects.filter(slug="demo").exists())
        self.assertTrue(PluginEvent.objects.filter(slug="demo", kind="uninstall").exists())  # گزارش می‌ماند


class TamperTests(PluginTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.write_plugin("demo")

    def test_tamper_before_activation_refused(self):
        self.install("demo")
        (self.root / "views.py").write_text("raise SystemExit('pwned')\n")
        with self.assertRaises(PluginError):
            services.activate("demo", self.admin)
        self.assertFalse(PluginRecord.objects.get(slug="demo").is_active)

    def test_tamper_after_activation_disables_immediately(self):
        services_ok = self.install_activate("demo")
        self.assertTrue(services_ok.is_active)
        self.assertEqual(Client().get("/ext/demo/ping").status_code, 200)
        (self.root / "views.py").write_text("from django.http import HttpResponse\ndef ping(r, api): return HttpResponse('EVIL')\ndef home(r, api): return HttpResponse('EVIL')\n")
        self.invalidate()
        r = Client().get("/ext/demo/ping")
        self.assertEqual(r.status_code, 404)
        self.assertNotIn(b"EVIL", r.content)
        rec = PluginRecord.objects.get(slug="demo")
        self.assertFalse(rec.is_active)
        self.assertTrue(PluginEvent.objects.filter(slug="demo", kind="tamper").exists())
        self.assertEqual(services.get_row("demo").status, "update_pending")

    def test_new_file_added_counts_as_tamper(self):
        self.install_activate("demo")
        (self.root / "backdoor.py").write_text("x = 1\n")
        self.invalidate()
        self.assertEqual(Client().get("/ext/demo/ping").status_code, 404)
        self.assertTrue(PluginEvent.objects.filter(slug="demo", kind="tamper").exists())

    def test_symlink_added_after_install_is_tamper(self):
        self.install_activate("demo")
        (self.root / "link.py").symlink_to("/etc/hostname")
        self.invalidate()
        self.assertEqual(Client().get("/ext/demo/ping").status_code, 404)

    def test_stale_pyc_is_ignored(self):
        # بایت‌کد دست‌کاری‌شده در __pycache__ نباید اجرا شود؛ فقط منبع تأییدشده کامپایل می‌شود
        self.install_activate("demo")
        cache_dir = self.root / "__pycache__"
        cache_dir.mkdir(exist_ok=True)
        (cache_dir / "views.cpython-313.pyc").write_bytes(b"garbage")
        registry.reset_for_tests()
        self.invalidate()
        self.assertEqual(Client().get("/ext/demo/ping").content, b"PONG")

    def test_approve_update_flow(self):
        self.install_activate("demo")
        (self.root / "views.py").write_text(
            "from django.http import HttpResponse\ndef ping(r, api): return HttpResponse('V2')\ndef home(r, api): return HttpResponse('V2')\n")
        manifest = base_manifest("demo", version="1.1.0")
        (self.root / "plugin.json").write_text(__import__("json").dumps(manifest))
        self.invalidate()
        self.assertEqual(Client().get("/ext/demo/ping").status_code, 404)  # تا تأیید متوقف
        with self.assertRaises(PluginError):
            services.approve_update("demo", self.admin, "f" * 64)  # اثرانگشت نادرست
        with self.assertRaises(PluginError):
            services.approve_update("demo", self.author, self.fp("demo"))
        services.approve_update("demo", self.admin, self.fp("demo"))
        rec = PluginRecord.objects.get(slug="demo")
        self.assertEqual(rec.version, "1.1.0")
        self.assertFalse(rec.is_active)  # تأیید ≠ فعال‌سازی
        services.activate("demo", self.admin)
        self.assertEqual(Client().get("/ext/demo/ping").content, b"V2")


class ErrorIsolationTests(PluginTestCase):
    BAD_VIEWS = '''from django.http import HttpResponse


def home(request, api):
    raise RuntimeError("boom-admin")


def ping(request, api):
    raise RuntimeError("boom-public")
'''

    def test_admin_page_error_is_contained(self):
        self.write_plugin("bad", views=self.BAD_VIEWS)
        self.install_activate("bad")
        self.login(self.admin)
        r = self.client.get(reverse("plugins:page", args=["bad", "home"]))
        self.assertEqual(r.status_code, 500)
        self.assertContains(r, "در دسترس نیست", status_code=500)
        self.assertNotContains(r, "boom-admin", status_code=500)  # جزئیات فنی به کاربر نشت نمی‌کند
        # پنل و بقیهٔ بخش‌ها سالم‌اند
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 200)
        self.assertEqual(self.client.get(reverse("plugins:list")).status_code, 200)
        ev = PluginEvent.objects.filter(slug="bad", kind="error").first()
        self.assertIn("boom-admin", ev.message + ev.detail)

    def test_public_error_is_contained(self):
        self.write_plugin("bad", views=self.BAD_VIEWS)
        self.install_activate("bad")
        r = Client().get("/ext/bad/ping")
        self.assertEqual(r.status_code, 500)
        self.assertNotIn(b"boom-public", r.content)
        self.assertEqual(Client().get("/fa/").status_code, 200)

    def test_circuit_breaker_auto_disables(self):
        self.write_plugin("bad", views=self.BAD_VIEWS)
        self.install_activate("bad")
        with override_settings(RAVIX_PLUGIN_ERROR_LIMIT=3, RAVIX_PLUGIN_ERROR_WINDOW=600):
            for _ in range(3):
                Client().get("/ext/bad/ping")
        rec = PluginRecord.objects.get(slug="bad")
        self.assertFalse(rec.is_active)
        self.assertTrue(rec.auto_disabled)
        self.assertTrue(PluginEvent.objects.filter(slug="bad", kind="auto_disable").exists())
        self.assertEqual(Client().get("/ext/bad/ping").status_code, 404)
        self.assertEqual(services.get_row("bad").status, "auto_disabled")
        services.activate("bad", self.admin)  # مدیر می‌تواند دوباره فعال کند
        self.assertTrue(PluginRecord.objects.get(slug="bad").is_active)

    def test_non_response_return_is_error(self):
        self.write_plugin("bad", views="def home(r, api): return 'text'\ndef ping(r, api): return None\n")
        self.install_activate("bad")
        self.assertEqual(Client().get("/ext/bad/ping").status_code, 500)

    def test_import_error_blocks_activation_gracefully(self):
        self.write_plugin("broken", init="raise RuntimeError('load-boom')")
        self.install("broken")
        with self.assertRaises(PluginError):
            services.activate("broken", self.admin)
        self.assertFalse(PluginRecord.objects.get(slug="broken").is_active)
        self.assertTrue(PluginEvent.objects.filter(slug="broken", level="error").exists())
        self.login(self.admin)
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 200)

    def test_missing_view_function_blocks_activation(self):
        self.write_plugin("nofunc", views="x = 1\n")
        self.install("nofunc")
        with self.assertRaises(PluginError):
            services.activate("nofunc", self.admin)

    def test_relative_module_refs_cannot_reach_stdlib(self):
        # «os:system» به ravix_ups.<slug>.os اشاره می‌کند (نه ماژول os) و وجود ندارد ← بارگذاری رد می‌شود
        m = base_manifest("evil", public_routes=[{"path": "x", "view": "os:system"}])
        self.write_plugin("evil", manifest=m)
        self.install("evil")
        with self.assertRaises(PluginError):
            services.activate("evil", self.admin)

    def test_nav_failure_does_not_break_panel(self):
        from unittest import mock

        self.write_plugin("demo")
        self.install_activate("demo")
        self.login(self.admin)
        with mock.patch("apps.plugins.state.nav_items", side_effect=RuntimeError("nav boom")):
            self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 200)

    def test_missing_folder_disables(self):
        import shutil

        self.write_plugin("demo")
        self.install_activate("demo")
        shutil.rmtree(self.tmp / "demo")
        self.invalidate()
        self.assertEqual(Client().get("/ext/demo/ping").status_code, 404)
        self.assertFalse(PluginRecord.objects.get(slug="demo").is_active)
        self.assertEqual(services.get_row("demo").status, "missing")


class MigrationControlTests(PluginTestCase):
    MODEL = '''from django.db import models


class Note(models.Model):
    text = models.CharField(max_length=50)
'''
    MIG = '''from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(name="Note", fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                                                    ("text", models.CharField(max_length=50))]),
    ]
'''

    def model_plugin(self, slug, mig=None, model=None, **over):
        m = base_manifest(slug, models=True, **over)
        return self.write_plugin(slug, manifest=m, files={"models.py": model or self.MODEL, "migrations/__init__.py": "",
                                                          "migrations/0001_initial.py": mig or self.MIG})

    def test_migration_runs_on_install_and_purge_on_uninstall(self):
        self.model_plugin("notes")
        self.assertFalse(table_exists("plugin_notes_note"))
        self.install("notes")
        self.assertTrue(table_exists("plugin_notes_note"))
        services.uninstall("notes", self.admin)  # داده‌ها نگه داشته می‌شود
        self.assertTrue(table_exists("plugin_notes_note"))
        self.install("notes")  # نصب دوباره روی جدول موجود مشکلی ندارد
        services.uninstall("notes", self.admin, purge_data=True)
        self.assertFalse(table_exists("plugin_notes_note"))

    def test_failed_migration_rolls_back_install(self):
        bad = self.MIG.replace("    ]\n", "        migrations.RunSQL('SELECT 1'),\n    ]\n")
        self.model_plugin("notes", mig=bad)
        with self.assertRaises(PluginError) as cm:
            self.install("notes")
        self.assertIn("RunSQL", str(cm.exception))
        self.assertFalse(PluginRecord.objects.filter(slug="notes").exists())
        self.assertFalse(table_exists("plugin_notes_note"))

    def test_runpython_forbidden(self):
        bad = self.MIG.replace("    ]\n", "        migrations.RunPython(lambda a, s: None),\n    ]\n")
        self.model_plugin("notes", mig=bad)
        with self.assertRaises(PluginError):
            self.install("notes")

    def test_foreign_table_name_forbidden(self):
        model = self.MODEL + "\n    class Meta:\n        db_table = 'accounts_user'\n"
        self.model_plugin("notes", model=model)
        with self.assertRaises(PluginError):
            self.install("notes")
        self.assertFalse(PluginRecord.objects.filter(slug="notes").exists())

    def test_relation_to_core_model_forbidden(self):
        model = "from django.db import models\n\n\nclass Note(models.Model):\n    page = models.ForeignKey('content.Page', on_delete=models.CASCADE)\n"
        self.model_plugin("notes", model=model)
        with self.assertRaises(PluginError):
            self.install("notes")

    def test_migration_depending_on_core_forbidden(self):
        bad = self.MIG.replace("dependencies = []", "dependencies = [('content', '0001_initial')]")
        self.model_plugin("notes", mig=bad)
        with self.assertRaises(PluginError):
            self.install("notes")

    def test_label_apps_registered(self):
        self.model_plugin("notes")
        self.install("notes")
        self.assertIn("plugin_notes", apps.app_configs)


class TemplateLoaderTests(PluginTestCase):
    def test_namespaced_and_gated(self):
        from django.template import TemplateDoesNotExist
        from django.template.loader import get_template

        self.write_plugin("demo", files={"templates/a.html": "PLUGIN-A"})
        (self.tmp / "secret.txt").write_text("TOP-SECRET")
        self.install("demo")
        with self.assertRaises(TemplateDoesNotExist):
            get_template("plugin:demo/a.html")  # نصب‌شده ولی غیرفعال
        services.activate("demo", self.admin)
        self.assertEqual(get_template("plugin:demo/a.html").render({}), "PLUGIN-A")
        for evil in ("plugin:demo/../../secret.txt", "plugin:demo/../plugin.json", "plugin:demo//etc/passwd", "plugin:../x/a.html"):
            with self.assertRaises(TemplateDoesNotExist, msg=evil):
                get_template(evil)
        with self.assertRaises(TemplateDoesNotExist):
            get_template("plugin:other/a.html")

    def test_plugins_cannot_shadow_core_templates(self):
        from django.template.loader import get_template

        self.write_plugin("demo", files={"templates/base.html": "HIJACKED", "templates/layouts/panel.html": "HIJACKED"})
        self.install_activate("demo")
        self.assertNotEqual(get_template("base.html").render({}), "HIJACKED")
        self.assertNotIn("HIJACKED", get_template("layouts/panel.html").template.source)


class UpdatedCodeInOldProcessTests(PluginTestCase):
    """An updated plugin whose new manifest names a view the old, still imported module does not have used to fail with “… is not callable” and
    the plugin was disabled. It must say clearly that a restart is needed and must not disable anything."""

    MODEL = MigrationControlTests.MODEL
    MIG = MigrationControlTests.MIG
    model_plugin = MigrationControlTests.model_plugin

    NEW_VIEWS = '''from django.http import HttpResponse


def home(request, api):
    return HttpResponse("HOME")


def ping(request, api):
    return HttpResponse("PONG")


def publish(request, api):
    return HttpResponse("PUBLISH")
'''

    def upgrade_on_disk(self, slug, with_models):
        manifest = base_manifest(slug, version="1.1.0", models=with_models)
        manifest["admin_pages"].append({"key": "publish", "title": "Publish", "view": "views:publish", "capability": f"{slug}.use"})
        root = self.tmp / slug
        (root / "plugin.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        (root / "views.py").write_text(self.NEW_VIEWS, encoding="utf-8")
        return self.fp(slug)

    def test_plugin_with_models_asks_for_a_restart_and_stays_enabled(self):
        self.model_plugin("notes")
        self.install_activate("notes")
        self.assertIsNotNone(registry.get("notes"))  # the old code is now imported in this process
        new_fp = self.upgrade_on_disk("notes", with_models=True)
        from django.utils import translation

        with translation.override("en"), self.assertRaises(PluginError) as cm:
            services.run_migrations("notes", self.tmp / "notes", new_fp)
        self.assertIn("Restart the server", str(cm.exception))
        self.assertNotIn("not callable", str(cm.exception))
        self.assertTrue(PluginRecord.objects.get(slug="notes").is_active)
        self.assertFalse(PluginEvent.objects.filter(slug="notes", kind="load_failed").exists())

    def test_plugin_without_models_simply_loads_the_new_code(self):
        self.write_plugin("demo")
        self.install_activate("demo")
        self.assertIsNotNone(registry.get("demo"))
        new_fp = self.upgrade_on_disk("demo", with_models=False)
        loaded = registry.load_for_install("demo", self.tmp / "demo", new_fp)
        self.assertIn("publish", loaded.pages)


class MenuTranslationTests(PluginTestCase):
    """The panel menu names a plugin's pages from its manifest. A freshly started worker has not loaded any plugin yet, and still has to show them
    in the language of the panel (they were shown in English until the plugin's own page was opened)."""

    def test_menu_titles_are_translated_before_any_plugin_code_is_loaded(self):
        from django.utils import translation

        from apps.core import catalogs
        from apps.plugins import state

        manifest = base_manifest("notes", admin_pages=[{"key": "home", "title": "Release notes", "view": "views:home", "capability": "notes.use", "menu": True}])
        root = self.write_plugin("notes", manifest=manifest)
        (root / "locale" / "fa" / "LC_MESSAGES").mkdir(parents=True)
        catalogs.write_mo(root / "locale" / "fa" / "LC_MESSAGES" / "django.mo", {"Release notes": "یادداشت‌های انتشار"}, "fa")
        self.install_activate("notes")
        registry.reset_for_tests()  # a new worker: nothing loaded, no catalog registered
        state.invalidate()
        with translation.override("fa"):
            labels = [str(i.label) for i in state.nav_items() if i.key == "plugin.notes.home"]
        self.assertEqual(labels, ["یادداشت‌های انتشار"])
        self.assertNotIn("notes", registry.loaded_slugs())  # the code was not loaded to get there

    def test_a_plugin_that_changed_after_approval_contributes_no_catalog(self):
        from apps.core import catalogs
        from apps.plugins import state

        root = self.write_plugin("notes")
        (root / "locale" / "fa" / "LC_MESSAGES").mkdir(parents=True)
        catalogs.write_mo(root / "locale" / "fa" / "LC_MESSAGES" / "django.mo", {"x": "y"}, "fa")
        self.install_activate("notes")
        registry.reset_for_tests()
        (root / "views.py").write_text(DEFAULT_VIEWS_TAMPERED, encoding="utf-8")
        state.invalidate()
        state.active_plugins()
        self.assertNotIn("plugin:notes", catalogs.registered())


DEFAULT_VIEWS_TAMPERED = "from django.http import HttpResponse\n\n\ndef home(request, api):\n    return HttpResponse('tampered')\n"
