"""آزمون نصب‌کنندهٔ افزونه: بستهٔ خراب، نسخهٔ ناسازگار، امضا، نصب ناموفق، اتمی‌بودن، ارتقا/بازگشت، قفل هم‌زمانی و پنل."""
import hashlib
import io
import json
import os
import zipfile
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings

from apps.plugins import installer, services, signing, sources
from apps.plugins.installer import InstallError
from apps.plugins.integrity import fingerprint
from apps.plugins.models import PluginBackup, PluginEvent, PluginOperationLock, PluginPackage, PluginRecord, TrustedKey
from apps.plugins.services import PluginError

from .helpers import PluginTestCase, base_manifest

SEED, PUB = signing.generate_keypair()  # فقط در حافظهٔ تست؛ هیچ کلیدی در مخزن کد نیست
OTHER_SEED, OTHER_PUB = signing.generate_keypair()


def pkg(slug="demo_pkg", version="1.0.0", *, man=None, extra=None, root=None, raw=None):
    m = man or {"slug": slug, "name": f"افزونه {slug}", "version": version, "description": "d", "api_version": 1, "ravin": ">=0.5.0,<1.0.0"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{root or slug}/plugin.json", json.dumps(m))
        zf.writestr(f"{root or slug}/__init__.py", "")
        for k, v in (extra or {}).items():
            zf.writestr(f"{root or slug}/{k}", v)
        for name, content in (raw or []):
            zf.writestr(name, content)
    return buf.getvalue()


def sig_for(data, seed=SEED):
    """(امضا، شناسهٔ کلید) برای بسته، با همان منطق سمت ناشر."""
    from apps.plugins.package import inspect
    i = inspect(data)
    return signing.sign(seed, i.manifest.slug, i.manifest.version, i.sha256, i.fingerprint)


class InstallerCase(PluginTestCase):
    def setUp(self):
        super().setUp()
        self.backups = self.tmp.parent / (self.tmp.name + "-backups")
        self.backups.mkdir()
        self.addCleanup(__import__("shutil").rmtree, self.backups, True)
        ov = override_settings(RAVIN_PLUGIN_INSTALL_DIR=self.tmp, RAVIN_PLUGIN_BACKUP_DIR=self.backups, RAVIN_PLUGIN_WEB_INSTALL=True,
                               RAVIN_TRUSTED_PUBLIC_KEYS=[f"test:{PUB}"], RAVIN_PLUGINS_ALLOW_UNSIGNED=False)
        ov.enable()
        self.addCleanup(ov.disable)

    def local(self, data, user=None, **kw):
        sig, kid = sig_for(data)
        return installer.install_from_file(data, user or self.admin, signature=kw.pop("signature", sig), key_id=kw.pop("key_id", kid), **kw)

    def errors(self, slug=None):
        qs = PluginEvent.objects.filter(level="error")
        return qs.filter(slug=slug) if slug else qs


class PackageValidationTests(InstallerCase):
    def assertRejected(self, data, text, slug="demo_pkg"):
        with self.assertRaises(InstallError) as cm:
            self.local(data)
        self.assertIn(text, str(cm.exception))
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name.startswith(slug)], [])  # هیچ ردپایی در پوشهٔ نصب
        self.assertFalse([p for p in self.tmp.iterdir() if p.name.startswith(".staging")])
        self.assertFalse(PluginRecord.objects.exists())

    def test_corrupt_zip(self):
        with self.assertRaises(InstallError) as cm:
            installer.install_from_file(b"this is not a zip", self.admin)
        self.assertIn("بسته معتبر نیست", str(cm.exception))
        self.assertTrue(self.errors().exists())  # خطا برای مدیر قابل‌فهم و در گزارش ثبت شده

    def test_empty_upload(self):
        with self.assertRaises(InstallError):
            installer.install_from_file(b"", self.admin)

    def test_path_traversal(self):
        for bad in ("demo_pkg/../../evil.py", "/etc/cron.d/x", "demo_pkg\\..\\evil.py"):
            data = pkg(raw=[(bad, "x")])
            with self.assertRaises(InstallError):
                installer.install_from_file(data, self.admin, signature="x")
        self.assertFalse(list(self.tmp.parent.glob("evil.py")))
        self.assertFalse(PluginRecord.objects.exists())

    def test_symlink_member_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("demo_pkg/plugin.json", "{}")
            info = zipfile.ZipInfo("demo_pkg/link")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            zf.writestr(info, "/etc/passwd")
        with self.assertRaises(InstallError) as cm:
            installer.install_from_file(buf.getvalue(), self.admin)
        self.assertIn("symlink", str(cm.exception))

    def test_zip_bomb_rejected(self):
        data = pkg(extra={"big.bin": b"\0" * (9 * 1024 * 1024)})
        with self.assertRaises(InstallError) as cm:
            installer.install_from_file(data, self.admin, signature="x")
        self.assertIn("حجم", str(cm.exception))
        self.assertFalse(any(self.tmp.iterdir()))

    def test_manifest_missing_or_invalid(self):
        for data in (pkg(man={"slug": "demo_pkg"}), pkg(man={**base_manifest("demo_pkg"), "version": "1.0"}), pkg(root="other_name")):
            with self.assertRaises(InstallError):
                installer.install_from_file(data, self.admin, signature="x")
        self.assertFalse(any(self.tmp.iterdir()))

    def test_incompatible_core_version(self):
        data = pkg(man={"slug": "demo_pkg", "name": "x", "version": "1.0.0", "api_version": 1, "ravin": ">=9.0.0"})
        with self.assertRaises(InstallError) as cm:
            self.local(data)
        self.assertIn("سازگار نیست", str(cm.exception))
        self.assertIn("9.0.0", str(cm.exception))
        self.assertFalse(any(self.tmp.iterdir()))

    def test_incompatible_plugin_api(self):
        data = pkg(man={"slug": "demo_pkg", "name": "x", "version": "1.0.0", "api_version": 2, "ravin": ">=0.5.0"})
        with self.assertRaises(InstallError):
            installer.install_from_file(data, self.admin, signature="x")

    def test_missing_dependency(self):
        data = pkg(man={**base_manifest("demo_pkg"), "requires": {"other_one": ">=1.0.0"}})
        with self.assertRaises(InstallError) as cm:
            self.local(data)
        self.assertIn("other_one", str(cm.exception))

    def test_expected_hash_and_identity(self):
        data = pkg()
        with self.assertRaisesMessage(InstallError, "SHA-256"):
            installer.stage(data, source="local", expected_sha256="0" * 64)
        with self.assertRaisesMessage(InstallError, "شناسه"):
            installer.stage(data, source="local", expected_slug="someone_else", signature="x")
        with self.assertRaisesMessage(InstallError, "نسخه"):
            installer.stage(data, source="local", expected_version="2.0.0", signature="x")


class SignatureTests(InstallerCase):
    def test_valid_signature_accepted(self):
        data = pkg()
        kind, slug = self.local(data)
        self.assertEqual((kind, slug), ("installed", "demo_pkg"))
        p = PluginPackage.objects.get()
        self.assertEqual(p.signature_state, "verified")
        self.assertEqual(p.signer_label, "test")

    def test_tampered_package_fails_signature(self):
        data = pkg()
        sig, kid = sig_for(data)
        evil = pkg(extra={"backdoor.py": "import os"})  # همان slug/version ولی محتوای دیگر
        with self.assertRaises(InstallError) as cm:
            installer.install_from_file(evil, self.admin, signature=sig, key_id=kid)
        self.assertIn("امضا", str(cm.exception))
        self.assertFalse((self.tmp / "demo_pkg").exists())

    def test_signature_cannot_be_moved_between_versions(self):
        sig, kid = sig_for(pkg(version="1.0.0"))
        with self.assertRaises(InstallError):
            installer.install_from_file(pkg(version="1.0.1"), self.admin, signature=sig, key_id=kid)

    def test_untrusted_signer_rejected(self):
        data = pkg()
        sig, kid = sig_for(data, OTHER_SEED)
        with self.assertRaises(InstallError) as cm:
            installer.install_from_file(data, self.admin, signature=sig, key_id=kid)
        self.assertIn("مورد اعتماد", str(cm.exception))
        sig2, _ = sig_for(data, OTHER_SEED)
        with self.assertRaises(InstallError):  # بدون ادعای کلید هم با کلیدهای مورد اعتماد نمی‌خواند
            installer.install_from_file(data, self.admin, signature=sig2)

    def test_garbage_signature(self):
        with self.assertRaises(InstallError):
            installer.install_from_file(pkg(), self.admin, signature="not-base64!!")

    def test_no_trusted_keys_means_nothing_verifies(self):
        data = pkg()
        sig, kid = sig_for(data)
        with override_settings(RAVIN_TRUSTED_PUBLIC_KEYS=[]), self.assertRaises(InstallError) as cm:
            installer.install_from_file(data, self.admin, signature=sig, key_id=kid)
        self.assertIn("کلید", str(cm.exception))

    def test_db_key_and_deactivation(self):
        data = pkg()
        sig, kid = sig_for(data, OTHER_SEED)
        k = TrustedKey.objects.create(key_id=signing.key_id(OTHER_PUB), public_key=OTHER_PUB, label="db-key")
        self.assertEqual(self.local(data, signature=sig, key_id=kid)[0], "installed")
        k.is_active = False
        k.save()
        with self.assertRaises(InstallError):
            installer.stage(data, source="local", signature=sig, key_id=kid)

    def test_unsigned_policy(self):
        data = pkg()
        with self.assertRaises(InstallError) as cm:
            installer.install_from_file(data, self.admin)
        self.assertIn("امضا", str(cm.exception))
        with override_settings(RAVIN_PLUGINS_ALLOW_UNSIGNED=True):
            self.assertEqual(installer.install_from_file(data, self.admin)[0], "installed")
        self.assertEqual(PluginPackage.objects.get().signature_state, "unsigned")

    def test_registry_source_always_requires_signature(self):
        with override_settings(RAVIN_PLUGINS_ALLOW_UNSIGNED=True), self.assertRaises(InstallError):
            installer.stage(pkg(), source="registry")

    def test_signing_primitives(self):
        s, p = signing.generate_keypair()
        sig, kid = signing.sign(s, "a_b", "1.0.0", "h" * 64, "f" * 64)
        self.assertEqual(kid, signing.key_id(p))
        with override_settings(RAVIN_TRUSTED_PUBLIC_KEYS=[p]):
            self.assertTrue(signing.verify("a_b", "1.0.0", "h" * 64, "f" * 64, sig, kid).ok)
            for args in (("a_c", "1.0.0", "h" * 64, "f" * 64), ("a_b", "1.0.1", "h" * 64, "f" * 64), ("a_b", "1.0.0", "x" * 64, "f" * 64), ("a_b", "1.0.0", "h" * 64, "y" * 64)):
                self.assertEqual(signing.verify(*args, sig, kid).state, "invalid")
        with self.assertRaises(signing.SigningError):
            signing.normalize_public_key("AAAA")

    def test_private_key_file_permissions(self):
        s, _ = signing.generate_keypair()
        f = self.tmp / "k.key"
        f.write_text(s)
        os.chmod(f, 0o644)
        with self.assertRaises(signing.SigningError):
            signing.read_key_file(f)
        os.chmod(f, 0o600)
        self.assertEqual(signing.read_key_file(f), s)


class PlaceAndLifecycleTests(InstallerCase):
    def test_atomic_placement_and_review_flow(self):
        data = pkg(extra={"views.py": "x = 1\n"})
        self.assertEqual(self.local(data), ("installed", "demo_pkg"))
        d = self.tmp / "demo_pkg"
        self.assertTrue((d / "plugin.json").is_file())
        self.assertFalse([p for p in self.tmp.iterdir() if p.name.startswith(".")])  # پوشهٔ موقت پاک شده
        self.assertFalse(PluginRecord.objects.exists())  # نصب خودکار نیست
        self.assertEqual(services.get_row("demo_pkg").status, "available")
        rec = services.install("demo_pkg", self.admin, fingerprint(d))  # مرحلهٔ تأیید صریح مدیر
        self.assertFalse(rec.is_active)
        self.assertEqual(PluginPackage.objects.get().fingerprint, rec.fingerprint)

    def test_refuses_overwrite_existing_dir(self):
        self.write_plugin("demo_pkg")
        with self.assertRaisesMessage(InstallError, "بازنویسی نمی‌کنیم"):
            self.local(pkg())

    def test_rename_failure_leaves_nothing(self):
        with mock.patch("apps.plugins.installer.os.rename", side_effect=OSError(13, "Permission denied")):
            with self.assertRaises(InstallError):
                self.local(pkg())
        self.assertEqual(list(self.tmp.iterdir()), [])
        self.assertFalse(PluginPackage.objects.exists())
        self.assertFalse(PluginOperationLock.objects.exists())  # قفل آزاد شده

    def test_unwritable_install_dir_message(self):
        with override_settings(RAVIN_PLUGIN_INSTALL_DIR=Path("/proc/nope/plugins")), self.assertRaises(InstallError) as cm:
            self.local(pkg())
        self.assertIn("قابل‌نوشتن", str(cm.exception))

    def test_db_failure_after_placement_cleans_up(self):
        with mock.patch("apps.plugins.installer._record_package", side_effect=RuntimeError("db down")):
            with self.assertRaises(InstallError) as cm:
                self.local(pkg())
        self.assertIn("خطای غیرمنتظره", str(cm.exception))
        self.assertFalse((self.tmp / "demo_pkg").exists())
        self.assertTrue(self.errors().filter(kind="install_failed").exists())

    def test_permissions(self):
        for u in (self.author, None):
            with self.assertRaises(InstallError):
                installer.install_from_file(pkg(), u)
        with override_settings(RAVIN_PLUGIN_WEB_INSTALL=False), self.assertRaisesMessage(InstallError, "خاموش"):
            self.local(pkg())

    def test_already_installed_requires_upgrade(self):
        self.local(pkg())
        services.install("demo_pkg", self.admin, fingerprint(self.tmp / "demo_pkg"))
        with self.assertRaisesMessage(InstallError, "قبلاً نصب"):
            self.local(pkg(version="1.1.0"))

    def test_deactivate_and_controlled_uninstall_with_file_removal(self):
        self.local(pkg())
        services.install("demo_pkg", self.admin, fingerprint(self.tmp / "demo_pkg"))
        services.activate("demo_pkg", self.admin)
        with self.assertRaises(PluginError):
            services.uninstall("demo_pkg", self.admin, remove_files=True)  # فعال است
        services.deactivate("demo_pkg", self.admin)
        services.uninstall("demo_pkg", self.admin, remove_files=True)
        self.assertFalse((self.tmp / "demo_pkg").exists())
        b = PluginBackup.objects.get()
        self.assertEqual(b.reason, "uninstall")
        installer.rollback(b.pk, self.admin)  # بازیابی فایل‌ها
        self.assertTrue((self.tmp / "demo_pkg" / "plugin.json").is_file())
        self.assertFalse(PluginRecord.objects.exists())


class UpgradeRollbackTests(InstallerCase):
    def setUp(self):
        super().setUp()
        self.local(pkg(version="1.0.0", extra={"views.py": "V = 1\n"}))
        self.rec = services.install("demo_pkg", self.admin, fingerprint(self.tmp / "demo_pkg"))
        self.fp1 = self.rec.fingerprint

    def upgrade(self, version="1.1.0", **kw):
        return self.local(pkg(version=version, extra={"views.py": f"V = '{version}'\n"}), upgrade_existing=True, **kw)

    def test_upgrade_backs_up_replaces_and_requires_reapproval(self):
        PluginRecord.objects.filter(pk=self.rec.pk).update(is_active=True)
        self.assertEqual(self.upgrade(), ("upgraded", "demo_pkg"))
        b = PluginBackup.objects.get()
        self.assertEqual((b.version, b.fingerprint, b.was_active), ("1.0.0", self.fp1, True))
        self.assertTrue((self.backups / b.archive).is_file())
        self.assertEqual(hashlib.sha256((self.backups / b.archive).read_bytes()).hexdigest(), b.archive_sha256)
        self.assertIn("1.1.0", (self.tmp / "demo_pkg" / "plugin.json").read_text())
        rec = PluginRecord.objects.get()
        self.assertEqual(rec.version, "1.0.0")  # هنوز تأیید نشده
        self.assertFalse(rec.is_active)  # تا تأیید غیرفعال
        self.assertEqual(services.get_row("demo_pkg").status, "update_pending")
        services.approve_update("demo_pkg", self.admin, fingerprint(self.tmp / "demo_pkg"))
        self.assertEqual(PluginRecord.objects.get().version, "1.1.0")

    def test_upgrade_must_be_newer(self):
        for v in ("1.0.0", "0.9.0"):
            with self.assertRaisesMessage(InstallError, "جدیدتر نیست"):
                self.upgrade(v)
        self.assertFalse(PluginBackup.objects.exists())

    def test_upgrade_refused_when_current_code_unapproved(self):
        (self.tmp / "demo_pkg" / "views.py").write_text("V = 'tampered'\n")
        with self.assertRaisesMessage(InstallError, "یکی نیست"):
            self.upgrade()
        self.assertFalse(PluginBackup.objects.exists())

    def test_upgrade_refused_for_manually_placed_plugin(self):
        other = self.tmp.parent / (self.tmp.name + "-manual")
        other.mkdir()
        self.addCleanup(__import__("shutil").rmtree, other, True)
        with override_settings(RAVIN_PLUGIN_INSTALL_DIR=other), self.assertRaisesMessage(InstallError, "دستی"):
            self.upgrade()

    def test_failed_swap_restores_everything(self):
        PluginRecord.objects.filter(pk=self.rec.pk).update(is_active=True)
        real = os.rename
        calls = {"n": 0}

        def flaky(a, b):
            calls["n"] += 1
            if ".staging" in str(a) and str(b).endswith("demo_pkg"):  # فقط جایگذاری نسخهٔ جدید شکست می‌خورد
                raise OSError(5, "I/O error")
            return real(a, b)
        with mock.patch("apps.plugins.installer.os.rename", side_effect=flaky), self.assertRaises(InstallError):
            self.upgrade()
        self.assertEqual(fingerprint(self.tmp / "demo_pkg"), self.fp1)  # نسخهٔ قبلی سر جایش
        self.assertTrue(PluginRecord.objects.get().is_active)  # وضعیت فعال برگشت
        self.assertFalse(PluginBackup.objects.exists())
        self.assertFalse(PluginOperationLock.objects.exists())

    def test_rollback_restores_previous_version(self):
        self.upgrade()
        services.approve_update("demo_pkg", self.admin, fingerprint(self.tmp / "demo_pkg"))
        b = PluginBackup.objects.get()
        installer.rollback(b.pk, self.admin)
        rec = PluginRecord.objects.get()
        self.assertEqual((rec.version, rec.fingerprint, rec.is_active), ("1.0.0", self.fp1, False))
        self.assertEqual(fingerprint(self.tmp / "demo_pkg"), self.fp1)
        self.assertEqual(services.get_row("demo_pkg").status, "inactive")
        b.refresh_from_db()
        self.assertIsNotNone(b.restored_at)
        with self.assertRaisesMessage(InstallError, "قبلاً استفاده"):
            installer.rollback(b.pk, self.admin)

    def test_rollback_after_unapproved_upgrade(self):
        self.upgrade()
        installer.rollback(PluginBackup.objects.get().pk, self.admin)
        self.assertEqual(services.get_row("demo_pkg").status, "inactive")

    def test_tampered_backup_refused(self):
        self.upgrade()
        b = PluginBackup.objects.get()
        with open(self.backups / b.archive, "ab") as fh:
            fh.write(b"junk")
        with self.assertRaisesMessage(InstallError, "تمامیت"):
            installer.rollback(b.pk, self.admin)
        self.assertIn("1.1.0", (self.tmp / "demo_pkg" / "plugin.json").read_text())

    def test_rollback_permission_and_unknown(self):
        self.upgrade()
        b = PluginBackup.objects.get()
        with self.assertRaises(InstallError):
            installer.rollback(b.pk, self.author)
        with self.assertRaises(InstallError):
            installer.rollback(99999, self.admin)

    def test_backup_retention(self):
        with override_settings(RAVIN_PLUGIN_BACKUPS_KEEP=2):
            for v in ("1.1.0", "1.2.0", "1.3.0"):
                self.upgrade(v)
                services.approve_update("demo_pkg", self.admin, fingerprint(self.tmp / "demo_pkg"))
        self.assertEqual(PluginBackup.objects.count(), 2)
        self.assertEqual(len(list((self.backups / "demo_pkg").iterdir())), 2)


class MigrationRollbackTests(InstallerCase):
    MODEL1 = "from django.db import models\n\n\nclass Note(models.Model):\n    text = models.CharField(max_length=50)\n"
    MODEL2 = MODEL1 + "    extra = models.CharField(max_length=10, default='')\n"
    MIG1 = ("from django.db import migrations, models\n\n\nclass Migration(migrations.Migration):\n    initial = True\n    dependencies = []\n"
            "    operations = [migrations.CreateModel(name='Note', fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),"
            " ('text', models.CharField(max_length=50))])]\n")
    MIG2 = ("from django.db import migrations, models\n\n\nclass Migration(migrations.Migration):\n    dependencies = [('plugin_rbnotes', '0001_initial')]\n"
            "    operations = [migrations.AddField(model_name='note', name='extra', field=models.CharField(default='', max_length=10))]\n")

    def pk(self, version, model, migs):
        m = base_manifest("rbnotes", version=version, models=True, ravin=">=0.5.0,<1.0.0")
        m.pop("admin_pages"); m.pop("public_routes"); m.pop("capabilities")
        extra = {"models.py": model, "migrations/__init__.py": "", **migs}
        return pkg("rbnotes", version, man=m, extra=extra)

    def cols(self):
        from django.db import connection
        with connection.cursor() as c:
            return [x.name for x in connection.introspection.get_table_description(c, "plugin_rbnotes_note")]

    def test_rollback_reverses_migrations_only_with_confirmation(self):
        self.local(self.pk("1.0.0", self.MODEL1, {"migrations/0001_initial.py": self.MIG1}))
        services.install("rbnotes", self.admin, fingerprint(self.tmp / "rbnotes"))
        self.assertNotIn("extra", self.cols())
        self.local(self.pk("1.1.0", self.MODEL2, {"migrations/0001_initial.py": self.MIG1, "migrations/0002_extra.py": self.MIG2}), upgrade_existing=True)
        services.approve_update("rbnotes", self.admin, fingerprint(self.tmp / "rbnotes"))
        self.assertIn("extra", self.cols())
        b = PluginBackup.objects.get()
        self.assertEqual(b.last_migration, "0001_initial")
        with self.assertRaisesMessage(InstallError, "مهاجرت"):
            installer.rollback(b.pk, self.admin)  # بدون تأیید حذف داده
        self.assertIn("extra", self.cols())
        installer.rollback(b.pk, self.admin, confirm_data_loss=True)
        self.assertNotIn("extra", self.cols())
        self.assertEqual(PluginRecord.objects.get().version, "1.0.0")
        services.uninstall("rbnotes", self.admin, purge_data=True)  # جدول آزمون به آزمون‌های بعدی نشت نکند


class ConcurrencyTests(InstallerCase):
    def test_second_operation_is_refused_while_locked(self):
        self.local(pkg())
        services.install("demo_pkg", self.admin, fingerprint(self.tmp / "demo_pkg"))
        with installer.plugin_lock("demo_pkg", "upgrade"):
            with self.assertRaisesMessage(InstallError, "در جریان"):
                self.local(pkg(version="1.1.0"), upgrade_existing=True)
            with self.assertRaisesMessage(InstallError, "در جریان"):
                installer.remove_files("demo_pkg", self.admin)
        self.assertFalse(PluginOperationLock.objects.exists())
        self.assertEqual(self.local(pkg(version="1.1.0"), upgrade_existing=True)[0], "upgraded")  # بعد از آزادشدن ممکن است

    def test_lock_released_after_failure(self):
        with self.assertRaises(InstallError):
            installer.install_from_file(b"junk", self.admin)
        self.assertFalse(PluginOperationLock.objects.exists())

    def test_stale_lock_is_cleared(self):
        from datetime import timedelta

        from django.utils import timezone
        PluginOperationLock.objects.create(slug="demo_pkg", operation="upgrade", holder="dead")
        PluginOperationLock.objects.update(acquired_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(self.local(pkg())[0], "installed")

    def test_different_plugins_do_not_block_each_other(self):
        with installer.plugin_lock("aaa_one", "upgrade"):
            self.assertEqual(self.local(pkg("bbb_two"))[0], "installed")


class RegistrySourceTests(InstallerCase):
    def setUp(self):
        super().setUp()
        from apps.accounts.permissions import Role
        from apps.accounts.tests.test_auth import make_user
        from apps.pluginhub.models import Publisher
        import tempfile
        self.hub_tmp = tempfile.mkdtemp(prefix="hub-")
        self.addCleanup(__import__("shutil").rmtree, self.hub_tmp, True)
        ov = override_settings(RAVIN_REGISTRY_STORAGE=self.hub_tmp, RAVIN_REGISTRY_SIGNING_KEY=SEED, RAVIN_OFFICIAL_REGISTRY_URL="")
        ov.enable()
        self.addCleanup(ov.disable)
        self.pubuser = make_user("pubx", Role.USER)
        self.pub = Publisher.objects.create(user=self.pubuser, display_name="ناشر")

    def hub_publish(self, version="1.0.0", slug="hub_plug", **kw):
        from apps.pluginhub import services as hub
        return hub.upload_version(self.pub, self.pubuser, pkg(slug, version, **kw), publish=True)

    def test_install_from_local_registry_verifies_signature(self):
        v = self.hub_publish()
        self.assertTrue(v.signature and v.signature_key_id)
        self.assertEqual(installer.install_from_registry("hub_plug", None, self.admin), ("installed", "hub_plug"))
        p = PluginPackage.objects.get()
        self.assertEqual((p.source, p.signature_state), ("registry", "verified"))
        self.assertTrue((self.tmp / "hub_plug" / "plugin.json").is_file())

    def test_unsigned_registry_version_rejected(self):
        with override_settings(RAVIN_REGISTRY_SIGNING_KEY=""):
            self.hub_publish()
        with self.assertRaises(InstallError) as cm:
            installer.install_from_registry("hub_plug", None, self.admin)
        self.assertIn("امضا", str(cm.exception))
        self.assertFalse((self.tmp / "hub_plug").exists())

    def test_registry_key_not_trusted_by_site(self):
        self.hub_publish()
        with override_settings(RAVIN_TRUSTED_PUBLIC_KEYS=[f"x:{OTHER_PUB}"]), self.assertRaises(InstallError):
            installer.install_from_registry("hub_plug", None, self.admin)

    def test_tampered_package_in_registry_storage(self):
        v = self.hub_publish()
        with open(v.package.path, "ab") as fh:
            fh.write(b"evil")
        with self.assertRaises(InstallError) as cm:
            installer.install_from_registry("hub_plug", None, self.admin)
        self.assertTrue("SHA-256" in str(cm.exception) or "بسته" in str(cm.exception))
        self.assertFalse((self.tmp / "hub_plug").exists())

    def test_incompatible_versions_are_skipped_or_refused(self):
        self.hub_publish("1.0.0")
        self.hub_publish("2.0.0", man={"slug": "hub_plug", "name": "x", "version": "2.0.0", "api_version": 1, "ravin": ">=9.0.0"})
        installer.install_from_registry("hub_plug", None, self.admin)  # آخرین نسخهٔ سازگار = 1.0.0
        self.assertIn('"1.0.0"', (self.tmp / "hub_plug" / "plugin.json").read_text())
        with self.assertRaises(InstallError) as cm:
            installer.install_from_registry("hub_plug", "2.0.0", self.admin, upgrade_existing=True)
        self.assertIn("سازگار", str(cm.exception))

    def test_unknown_plugin_and_missing_registry(self):
        with self.assertRaisesMessage(InstallError, "پیدا نشد"):
            installer.install_from_registry("nope_x", None, self.admin)
        with override_settings(RAVIN_REGISTRY_ENABLED=False), self.assertRaisesMessage(InstallError, "تنظیم نشده"):
            installer.install_from_registry("hub_plug", None, self.admin)

    def test_upgrade_from_registry_and_update_visibility(self):
        self.hub_publish("1.0.0")
        installer.install_from_registry("hub_plug", None, self.admin)
        services.install("hub_plug", self.admin, fingerprint(self.tmp / "hub_plug"))
        cache.clear()
        self.assertIsNone(sources.update_available("hub_plug", "1.0.0"))
        self.hub_publish("1.1.0")
        cache.clear()
        self.assertEqual(sources.update_available("hub_plug", "1.0.0"), "1.1.0")
        self.assertEqual(installer.install_from_registry("hub_plug", "1.1.0", self.admin, upgrade_existing=True)[0], "upgraded")

    def test_remote_registry_requires_https_and_checks_host(self):
        with self.assertRaises(PluginError):
            sources.http_get("http://registry.example.com/x", 100)
        src = sources.RemoteSource("https://registry.example.com")
        with self.assertRaisesMessage(PluginError, "دامنهٔ دیگری"):
            src.download("a", {"download_url": "https://evil.example.net/p.zip"})
        with self.assertRaises(PluginError):
            src.download("a", {"download_url": "http://registry.example.com/p.zip"})

    def test_remote_registry_end_to_end_with_mocked_network(self):
        v = self.hub_publish()
        from django.test import Client
        c = Client()
        info = c.get("/registry/v1/plugins/hub_plug/", secure=True).json()
        for e in info["versions"]:
            e["download_url"] = e["download_url"].replace("https://testserver", "https://registry.example.com")
        body = b"".join(c.get("/registry/v1/plugins/hub_plug/versions/1.0.0/download/", secure=True).streaming_content)

        def fake(url, max_bytes, allow_insecure=False):
            return (json.dumps(info).encode() if url.endswith("/hub_plug/") else body), {}
        with override_settings(RAVIN_OFFICIAL_REGISTRY_URL="https://registry.example.com"), mock.patch("apps.plugins.sources.http_get", fake):
            self.assertEqual(installer.install_from_registry("hub_plug", None, self.admin), ("installed", "hub_plug"))
            self.assertEqual(PluginPackage.objects.get().source_ref, "https://registry.example.com#hub_plug@1.0.0")
        self.assertTrue(v.signature)

    def test_unavailable_registry_does_not_break_panel_versions(self):
        with override_settings(RAVIN_OFFICIAL_REGISTRY_URL="https://down.example.com"), mock.patch("apps.plugins.sources.http_get", side_effect=sources.RegistryUnavailable("down")):
            cache.clear()
            self.assertEqual(sources.latest_versions(), {})


class PanelTests(InstallerCase):
    def upload(self, client, data, **post):
        f = io.BytesIO(data)
        f.name = "p.zip"
        return client.post("/panel/plugins/install-package/", {"op": "local", "package": f, **post})

    def test_access_control(self):
        from django.test import Client
        self.assertEqual(Client().get("/panel/plugins/install-package/").status_code, 302)
        self.assertEqual(self.login(self.author).get("/panel/plugins/install-package/").status_code, 403)
        self.assertEqual(self.login(self.admin).get("/panel/plugins/install-package/").status_code, 200)
        self.assertEqual(self.login(self.author).post("/panel/plugins/install-package/", {"op": "registry", "slug": "x_y_z"}).status_code, 403)

    def test_upload_flow_redirects_to_review_not_install(self):
        data = pkg()
        sig, kid = sig_for(data)
        r = self.upload(self.login(self.admin), data, signature=sig, key_id=kid)
        self.assertRedirects(r, "/panel/plugins/demo_pkg/install/", fetch_redirect_response=False)
        self.assertFalse(PluginRecord.objects.exists())
        self.assertContains(self.client.get("/panel/plugins/demo_pkg/install/"), "فایل‌های افزونه")

    def test_bad_upload_shows_readable_error(self):
        c = self.login(self.admin)
        r = self.upload(c, b"junk", signature="x")
        self.assertRedirects(r, "/panel/plugins/install-package/", fetch_redirect_response=False)
        self.assertContains(c.get("/panel/plugins/install-package/"), "بسته معتبر نیست")
        self.assertNotContains(c.get("/panel/plugins/install-package/"), "Traceback")

    def test_web_install_disabled_message(self):
        c = self.login(self.admin)
        with override_settings(RAVIN_PLUGIN_WEB_INSTALL=False):
            self.upload(c, pkg(), signature="x")
            self.assertContains(c.get("/panel/plugins/install-package/"), "خاموش")

    def test_csrf(self):
        from django.test import Client
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.admin)
        self.assertEqual(c.post("/panel/plugins/install-package/", {"op": "local"}).status_code, 403)

    def test_detail_shows_versions_upgrade_and_rollback(self):
        c = self.login(self.admin)
        self.local(pkg(version="1.0.0"))
        services.install("demo_pkg", self.admin, fingerprint(self.tmp / "demo_pkg"))
        r = c.post("/panel/plugins/demo_pkg/upgrade/", {"version": "1.1.0"})  # مخزنی نیست ⇒ پیام خطا، بدون تغییر
        self.assertRedirects(r, "/panel/plugins/demo_pkg/", fetch_redirect_response=False)
        data = pkg(version="1.1.0")
        sig, kid = sig_for(data)
        self.upload(c, data, signature=sig, key_id=kid, upgrade="on")
        html = c.get("/panel/plugins/demo_pkg/").content.decode()
        self.assertIn("بازگشت به 1.0.0", html)
        self.assertIn("سابقهٔ بسته‌ها", html)
        b = PluginBackup.objects.get()
        self.assertEqual(c.post(f"/panel/plugins/other_plug/rollback/{b.pk}/").status_code, 404)  # backup متعلق به افزونهٔ دیگر
        c.post(f"/panel/plugins/demo_pkg/rollback/{b.pk}/")
        self.assertEqual(PluginRecord.objects.get().version, "1.0.0")

    def test_list_shows_new_version(self):
        self.local(pkg())
        services.install("demo_pkg", self.admin, fingerprint(self.tmp / "demo_pkg"))
        with mock.patch("apps.plugins.sources.latest_versions", return_value={"demo_pkg": "2.0.0"}):
            self.assertContains(self.login(self.admin).get("/panel/plugins/"), "نسخهٔ جدید 2.0.0")
            self.assertContains(self.client.get("/panel/plugins/demo_pkg/"), "2.0.0")

    def test_trusted_keys_superadmin_only(self):
        for u in (self.admin, self.author):
            self.assertEqual(self.login(u).get("/panel/plugins/trusted-keys/").status_code, 403)
            self.assertEqual(self.client.post("/panel/plugins/trusted-keys/", {"op": "add", "label": "x", "public_key": PUB}).status_code, 403)
        self.assertFalse(TrustedKey.objects.exists())
        c = self.login(self.superadmin)
        self.assertEqual(c.get("/panel/plugins/trusted-keys/").status_code, 200)
        self.assertEqual(c.post("/panel/plugins/trusted-keys/", {"op": "add", "label": "x", "public_key": "bad"}).status_code, 400)
        self.assertEqual(c.post("/panel/plugins/trusted-keys/", {"op": "add", "label": "ok", "public_key": OTHER_PUB}).status_code, 302)
        k = TrustedKey.objects.get()
        self.assertEqual(k.key_id, signing.key_id(OTHER_PUB))
        c.post("/panel/plugins/trusted-keys/", {"op": "toggle", "pk": k.pk})
        k.refresh_from_db()
        self.assertFalse(k.is_active)


class CommandTests(InstallerCase):
    def test_keygen_and_sign_roundtrip(self):
        out = io.StringIO()
        keyfile = self.backups / "dev.key"
        call_command("plugin_keygen", out=str(keyfile), stdout=out)
        self.assertEqual(oct(keyfile.stat().st_mode & 0o777), "0o600")
        seed = keyfile.read_text().strip()
        self.assertNotIn(seed, out.getvalue())  # کلید خصوصی چاپ نمی‌شود
        pub = [l for l in out.getvalue().splitlines() if "کلید عمومی" in l][0].split(": ")[1].strip()
        z = self.backups / "p.zip"
        z.write_bytes(pkg())
        out2 = io.StringIO()
        call_command("plugin_sign", str(z), key_file=str(keyfile), stdout=out2)
        sig = [l for l in out2.getvalue().splitlines() if l.startswith("signature:")][0].split(": ")[1]
        kid = [l for l in out2.getvalue().splitlines() if l.startswith("key_id:")][0].split(": ")[1]
        with override_settings(RAVIN_TRUSTED_PUBLIC_KEYS=[f"dev:{pub}"]):
            self.assertEqual(installer.install_from_file(z.read_bytes(), self.admin, signature=sig, key_id=kid)[0], "installed")
        with self.assertRaises(Exception):
            call_command("plugin_keygen", out=str(keyfile))  # بازنویسی نمی‌کند
