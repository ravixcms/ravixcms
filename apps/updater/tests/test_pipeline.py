"""ارتقای هسته: موفق، رد شدن پیش از تغییر، شکست در میانهٔ کار با بازگشت خودکار، بازگشت دستی، قفل، نشت اسرار."""
import os
from unittest import mock

from django.db import connection
from django.test import override_settings

from apps.updater import backup, pipeline, release, signing
from apps.updater.errors import UpdateBlocked, UpdateBusy, UpdateError
from apps.updater.models import UpdateLock, UpdateRun

from .helpers import OTHER_SEED, SECRET_TEXT, SEED, UpdaterCase, FakeDb, FakeRunner


class SuccessfulUpgrade(UpdaterCase):
    def test_upgrade_replaces_files_runs_migrations_and_records_history(self):
        self.publish("0.7.0")
        run = self.apply()
        self.assertEqual(run.status, "succeeded")
        self.assertEqual((run.from_version, run.to_version), ("0.6.1", "0.7.0"))
        self.assertEqual(self.disk(), "0.7.0")
        self.assertEqual((self.root / "static/app.css").read_text(), "/* 0.7.0 */")
        self.assertEqual(self.runner.calls, ["check", "migrate", "check"])
        self.assertEqual(run.migrations_applied, ["fakeapp.0001_initial", "fakeapp.0002_more"])
        self.assertIn(("fakeapp", "0001_initial"), self.migrations())
        self.assertTrue(run.restart_required)
        names = [s["name"] for s in run.steps]
        for n in ("download", "checksum", "signature", "validate", "backup", "swap", "migrate", "verify"):
            self.assertIn(n, names)
        self.assertEqual(run.signer_label, "test")
        self.assertEqual(len(run.sha256), 64)
        self.assertFalse(UpdateLock.objects.exists())
        self.assertFalse((self.root / ".ravin-old").exists())

    def test_backup_contains_files_and_db_and_is_private(self):
        self.publish("0.7.0")
        run = self.apply()
        d = backup.backup_dir() / run.backup["dir"]
        self.assertTrue((d / "files.tar.gz").is_file() and (d / "db.dump").is_file())
        if os.name == "posix":
            self.assertEqual(oct(backup.backup_dir().stat().st_mode & 0o777), "0o700")
            self.assertEqual(oct((d / "files.tar.gz").stat().st_mode & 0o777), "0o600")
        backup.verify_backup(run.backup)

    def test_untouched_paths_survive(self):
        (self.root / ".env").write_text("SECRET=1")
        (self.root / "media").mkdir()
        (self.root / "media/a.txt").write_text("user upload")
        (self.root / "plugins").mkdir()
        (self.root / "plugins/p.py").write_text("x=1")
        self.publish("0.7.0")
        self.apply()
        self.assertEqual((self.root / ".env").read_text(), "SECRET=1")
        self.assertEqual((self.root / "media/a.txt").read_text(), "user upload")
        self.assertEqual((self.root / "plugins/p.py").read_text(), "x=1")

    def test_dry_run_validates_but_changes_nothing(self):
        self.publish("0.7.0")
        run = self.apply(dry_run=True)
        self.assertEqual(run.status, "succeeded")
        self.assertEqual(self.disk(), "0.6.1")
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(run.backup, {})
        self.assertNotIn("swap", [s["name"] for s in run.steps])

    def test_latest_compatible_picked_by_default(self):
        self.publish("0.7.0")
        self.publish("0.8.0")
        self.assertEqual(self.apply().to_version, "0.8.0")

    def test_up_to_date_is_blocked(self):
        self.publish("0.6.1")
        with self.assertRaisesRegex(UpdateBlocked, "جدیدتر"):
            self.apply()

    def test_downgrade_refused(self):
        self.publish("0.5.0")
        with self.assertRaises(UpdateBlocked):
            self.apply("0.5.0")
        self.assertEqual(self.disk(), "0.6.1")


class RejectedBeforeAnyChange(UpdaterCase):
    """هر رد شدن پیش از جایگزینی: FAILED، فایل‌ها دست‌نخورده، پشتیبان/مهاجرتی اجرا نشده."""

    def assertUntouched(self, run_status="failed"):
        self.assertEqual(self.disk(), "0.6.1")
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(UpdateRun.objects.first().status, run_status)
        self.assertFalse(UpdateLock.objects.exists())

    def test_unsigned_rejected(self):
        self.publish("0.7.0", seed=None)
        with self.assertRaisesRegex(UpdateBlocked, "امضا"):
            self.apply()
        self.assertUntouched()
        self.assertEqual(UpdateRun.objects.first().backup, {})

    def test_untrusted_key_rejected(self):
        self.publish("0.7.0", seed=OTHER_SEED)
        with self.assertRaisesRegex(UpdateBlocked, "امضا"):
            self.apply()
        self.assertUntouched()

    def test_no_trusted_keys_configured_rejects(self):
        self.publish("0.7.0")
        with override_settings(RAVIN_TRUSTED_PUBLIC_KEYS=[]):
            with self.assertRaisesRegex(UpdateBlocked, "کلید مورد اعتماد"):
                self.apply()
        self.assertUntouched()

    def test_tampered_package_sha_mismatch(self):
        entry = self.publish("0.7.0")
        path = self.dist / entry["download_url"]
        path.write_bytes(path.read_bytes() + b"x")
        with self.assertRaisesRegex(UpdateBlocked, "SHA-256"):
            self.apply()
        self.assertUntouched()

    def test_signature_not_transferable_between_versions_or_kinds(self):
        e7 = self.publish("0.7.0")
        e8 = self.publish("0.8.0")
        # امضای 0.7.0 روی بستهٔ 0.8.0 (فهرست دست‌کاری‌شده)
        import json
        idx = json.loads((self.dist / "index.json").read_text())
        for e in idx["core"]:
            if e["version"] == "0.8.0":
                e["signature"] = e7["signature"]
        (self.dist / "index.json").write_text(json.dumps(idx))
        with self.assertRaisesRegex(UpdateBlocked, "امضا"):
            self.apply("0.8.0")
        self.assertUntouched()
        # امضای نوع «theme» برای هسته معتبر نیست
        s, kid = signing.sign(SEED, "theme", "", "0.7.0", e7["sha256"])
        self.assertFalse(signing.verify("core", "", "0.7.0", e7["sha256"], s, kid).ok)

    def test_corrupt_zip_rejected(self):
        entry = self.publish("0.7.0")
        import hashlib, json
        (self.dist / entry["download_url"]).write_bytes(b"garbage")
        idx = json.loads((self.dist / "index.json").read_text())
        idx["core"][0]["sha256"] = hashlib.sha256(b"garbage").hexdigest()
        idx["core"][0]["signature"], idx["core"][0]["signature_key_id"] = signing.sign(SEED, "core", "", "0.7.0", idx["core"][0]["sha256"])
        (self.dist / "index.json").write_text(json.dumps(idx))
        with self.assertRaisesRegex(UpdateError, "zip"):
            self.apply()
        self.assertUntouched()

    def test_python_requirement_blocks(self):
        self.publish("0.7.0", requires={"python": ">=9.0"})
        with self.assertRaisesRegex(UpdateBlocked, "Python"):
            self.apply()
        self.assertUntouched()

    def test_missing_dependency_blocks_and_nothing_is_installed(self):
        self.publish("0.7.0", requirements="Django>=5.0\nnot-a-real-package-xyz>=1.0\n")
        with self.assertRaisesRegex(UpdateBlocked, "not-a-real-package-xyz"):
            self.apply()
        self.assertUntouched()

    def test_dependency_version_conflict_blocks(self):
        self.publish("0.7.0", requirements="Django>=99.0\n")
        with self.assertRaisesRegex(UpdateBlocked, "Django"):
            self.apply()
        self.assertUntouched()

    def test_min_from_blocks(self):
        self.publish("0.9.0", requires={"min_from": "0.8.0"})
        with self.assertRaisesRegex(UpdateBlocked, "0.8.0"):
            self.apply("0.9.0")
        self.assertUntouched()

    def test_incompatible_active_plugin_blocks_but_inactive_only_warns(self):
        from apps.plugins.models import PluginRecord

        PluginRecord.objects.create(slug="oldp", name="Old", version="1.0.0", is_active=True, manifest={"ravin": ">=0.5,<0.7", "api_version": 1}, fingerprint="x")
        self.publish("0.7.0")
        with self.assertRaisesRegex(UpdateBlocked, "oldp"):
            self.apply()
        self.assertUntouched()
        PluginRecord.objects.filter(slug="oldp").update(is_active=False)
        UpdateRun.objects.all().delete()
        self.assertEqual(self.apply().status, "succeeded")

    def test_plugin_api_change_blocks_active_plugins(self):
        from apps.plugins.models import PluginRecord

        PluginRecord.objects.create(slug="p1", name="P", version="1.0.0", is_active=True, manifest={"ravin": ">=0.5,<1.0", "api_version": 1}, fingerprint="x")
        self.publish("0.7.0", requires={"plugin_api": 2, "theme_api": 1})
        with self.assertRaisesRegex(UpdateBlocked, "p1"):
            self.apply()
        self.assertUntouched()

    def test_signed_index_required_policy(self):
        self.publish("0.7.0")
        with override_settings(RAVIN_UPDATE_REQUIRE_SIGNED_INDEX=True):
            with self.assertRaisesRegex(UpdateError, "فهرست"):
                self.apply()
        self.publish("0.7.0", sign_index=True)
        with override_settings(RAVIN_UPDATE_REQUIRE_SIGNED_INDEX=True):
            self.assertEqual(self.apply().status, "succeeded")

    def test_tampered_signed_index_rejected(self):
        self.publish("0.7.0", sign_index=True)
        p = self.dist / "index.json"
        p.write_text(p.read_text().replace("0.7.0", "0.7.1"))
        with self.assertRaisesRegex(UpdateError, "امضای فهرست"):
            self.apply()

    def test_plugin_operation_in_progress_blocks(self):
        from apps.plugins.models import PluginOperationLock

        PluginOperationLock.objects.create(slug="x", operation="upgrade", holder="h")
        self.publish("0.7.0")
        with self.assertRaisesRegex(UpdateBlocked, "افزونه"):
            self.apply()
        self.assertUntouched()


class FailureMidway(UpdaterCase):
    """پس از شروع جایگزینی هر شکستی ⇐ بازگشت خودکار فایل‌ها و دیتابیس به نسخهٔ قبلی."""

    def setUp(self):
        super().setUp()
        self.publish("0.7.0")
        (self.root / "static/user_marker.txt").write_text("old-static-only")
        self.before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.migrations_before = self.migrations()

    def assertRolledBack(self, run):
        run.refresh_from_db()
        self.assertEqual(run.status, "rolled_back", run.error)
        self.assertEqual(self.disk(), "0.6.1")
        after = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(after, self.before)  # بایت‌به‌بایت همان نسخهٔ قبل
        self.assertEqual(self.migrations(), self.migrations_before)
        self.assertFalse(UpdateLock.objects.exists())
        self.assertIn("برگردانده", run.recovery)

    def fail(self, runner=None, **patch_kw):
        if runner:
            self.runner = runner
        with self.assertRaises(UpdateError) as cm:
            self.apply()
        return UpdateRun.objects.first(), cm.exception

    def test_failure_in_swap_midway(self):
        real, calls = __import__("shutil").move, {"n": 0}

        def flaky(src, dst, *a, **k):
            calls["n"] += 1
            if calls["n"] == 3:
                raise OSError("disk full")
            return real(src, dst, *a, **k)

        with mock.patch("apps.updater.pipeline.shutil.move", side_effect=flaky):
            run, _ = self.fail()
        self.assertRolledBack(run)
        self.assertEqual(self.runner.calls, [])  # به مهاجرت نرسید

    def test_failure_in_code_check(self):
        run, _ = self.fail(FakeRunner(fail={"check"}))
        self.assertRolledBack(run)
        self.assertEqual(self.db.restores, 0)  # دیتابیس هنوز دست نخورده بود

    def test_failure_in_migrate_restores_db_and_files(self):
        run, exc = self.fail(FakeRunner(fail={"migrate"}))
        self.assertRolledBack(run)
        self.assertEqual(self.db.restores, 1)
        self.assertIn("rolled", [s["name"] for s in run.steps] + ["rolled"])
        self.assertIn("rollback_db", [s["name"] for s in run.steps])

    def test_failure_after_migrate_in_verify(self):
        run, _ = self.fail(FakeRunner(fail={"check2"}))
        self.assertRolledBack(run)
        self.assertEqual(self.db.restores, 1)

    def test_history_survives_db_restore(self):
        old = UpdateRun.objects.create(component="core", from_version="0.5.0", to_version="0.6.1", status="succeeded")
        run, _ = self.fail(FakeRunner(fail={"migrate"}))
        self.assertTrue(UpdateRun.objects.filter(pk=old.pk, status="succeeded").exists())
        self.assertTrue(UpdateRun.objects.filter(pk=run.pk, status="rolled_back").exists())

    def test_rollback_failure_needs_manual_recovery_with_instructions(self):
        self.db = FakeDb(fail_restore=True)
        run, _ = self.fail(FakeRunner(fail={"migrate"}))
        run.refresh_from_db()
        self.assertEqual(run.status, "needs_recovery")
        self.assertIn("پشتیبان", run.recovery)
        self.assertIn(run.backup["dir"], run.recovery)
        self.assertIn("docs/upgrade.md", run.recovery)
        self.assertFalse(UpdateLock.objects.exists())

    def test_secrets_never_stored_in_history(self):
        with override_settings(SECRET_KEY=SECRET_TEXT + "-django-secret"):
            msg = f"failed with SECRET_KEY={SECRET_TEXT}-django-secret and postgres://u:{SECRET_TEXT}@db/x and -----BEGIN " + "PRIVATE KEY-----\nMIIabc\n-----END PRIVATE KEY-----"
            run, _ = self.fail(FakeRunner(fail={"migrate"}, message=msg))
        blob = " ".join([run.error, run.recovery, run.migration_output, str(run.steps), str(run.backup)])
        self.assertNotIn(SECRET_TEXT, blob)
        self.assertNotIn("MIIabc", blob)
        self.assertNotIn("PRIVATE KEY-----\nMII", blob)


class ManualRollback(UpdaterCase):
    def upgrade(self):
        self.publish("0.7.0")
        return self.apply()

    def rollback(self, run, **kw):
        kw.setdefault("confirm_data_loss", True)
        return pipeline.rollback_run(run.pk, self.superadmin, runner=self.runner, db_backend=self.db, root=self.root, **kw)

    def test_rollback_restores_previous_version(self):
        up = self.upgrade()
        before_files = {p.name for p in (self.root / "static").iterdir()}
        rb = self.rollback(up)
        self.assertEqual(rb.status, "succeeded")
        self.assertEqual(rb.kind, "rollback")
        self.assertEqual(self.disk(), "0.6.1")
        self.assertEqual((self.root / "static/app.css").read_text(), "/* 0.6.1 */")
        self.assertEqual(self.migrations(), [m for m in self.migrations() if m[0] != "fakeapp"])
        up.refresh_from_db()
        self.assertEqual(up.status, "reverted")
        self.assertEqual(rb.parent_id, up.pk)
        self.assertTrue(UpdateRun.objects.filter(pk=up.pk).exists())  # تاریخچه حفظ شد
        self.assertTrue(rb.backup.get("dir"))  # پشتیبان از وضعیت پیش از بازگشت
        self.assertEqual(before_files, {p.name for p in (self.root / "static").iterdir()})

    def test_rollback_requires_explicit_confirmation(self):
        up = self.upgrade()
        with self.assertRaisesRegex(UpdateBlocked, "تأیید"):
            self.rollback(up, confirm_data_loss=False)
        self.assertEqual(self.disk(), "0.7.0")

    def test_rollback_refused_if_code_changed_since(self):
        up = self.upgrade()
        (self.root / "apps/core/version.py").write_text('RAVIN_VERSION = "0.7.5"\n')
        with self.assertRaisesRegex(UpdateBlocked, "نیست"):
            self.rollback(up)

    def test_rollback_of_failed_or_unknown_run_refused(self):
        self.publish("0.7.0")
        with self.assertRaises(UpdateBlocked):
            self.rollback(UpdateRun(pk=999))
        self.runner = FakeRunner(fail={"migrate"})
        with self.assertRaises(UpdateError):
            self.apply()
        failed = UpdateRun.objects.first()
        with self.assertRaisesRegex(UpdateBlocked, "قابل بازگشت"):
            self.rollback(failed)

    def test_corrupt_backup_refuses_rollback(self):
        up = self.upgrade()
        (backup.backup_dir() / up.backup["dir"] / "files.tar.gz").write_bytes(b"corrupt")
        with self.assertRaisesRegex(UpdateError, "خراب"):
            self.rollback(up)
        self.assertEqual(self.disk(), "0.7.0")


class LockingAndCrash(UpdaterCase):
    def test_concurrent_upgrade_refused_and_first_not_disturbed(self):
        self.publish("0.7.0")
        UpdateLock.objects.create(name="global", holder="other", operation="apply-core")
        with self.assertRaises(UpdateBusy):
            self.apply()
        self.assertEqual(self.disk(), "0.6.1")
        self.assertTrue(UpdateLock.objects.filter(holder="other").exists())  # قفل دیگری آزاد نشد

    def test_lock_released_after_success_and_failure(self):
        self.publish("0.7.0")
        self.runner = FakeRunner(fail={"migrate"})
        with self.assertRaises(UpdateError):
            self.apply()
        self.assertFalse(UpdateLock.objects.exists())
        self.runner = FakeRunner()
        self.assertEqual(self.apply().status, "succeeded")
        self.assertFalse(UpdateLock.objects.exists())

    def test_stale_lock_is_released_and_interrupted_run_flagged(self):
        from django.utils import timezone
        from datetime import timedelta

        crashed = UpdateRun.objects.create(component="core", from_version="0.6.1", to_version="0.7.0", status="running", phase="swap")
        UpdateRun.objects.filter(pk=crashed.pk).update(started_at=timezone.now() - timedelta(hours=3))
        lock = UpdateLock.objects.create(name="global", holder="dead", operation="apply-core", run=crashed)
        UpdateLock.objects.filter(pk=lock.pk).update(acquired_at=timezone.now() - timedelta(hours=3))
        self.publish("0.7.0")
        self.assertEqual(self.apply().status, "succeeded")
        crashed.refresh_from_db()
        self.assertEqual(crashed.status, "needs_recovery")
        self.assertIn("نیمه‌کاره", crashed.error)
        self.assertTrue(crashed.recovery)

    def test_running_run_without_lock_marked_interrupted(self):
        from datetime import timedelta
        from django.utils import timezone

        r = UpdateRun.objects.create(component="core", status="running")
        pipeline.mark_interrupted()
        r.refresh_from_db()
        self.assertEqual(r.status, "running")  # تازه ساخته شده؛ مهلت ۲ دقیقه‌ای
        UpdateRun.objects.filter(pk=r.pk).update(started_at=timezone.now() - timedelta(minutes=5))
        pipeline.mark_interrupted()
        r.refresh_from_db()
        self.assertEqual(r.status, "needs_recovery")

    def test_maintenance_only_during_apply(self):
        from django.test import Client
        from apps.updater.middleware import reset_cache

        c = Client()
        reset_cache()
        self.assertEqual(c.get("/healthz").status_code, 200)
        lock = UpdateLock.objects.create(name="global", holder="h", operation="prepare-core")
        reset_cache()
        self.assertNotEqual(c.get("/").status_code, 503)  # دریافت/اعتبارسنجی سایت را متوقف نمی‌کند
        UpdateLock.objects.filter(pk=lock.pk).update(operation="apply-core")
        reset_cache()
        r = c.get("/")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r["Retry-After"], "30")
        self.assertEqual(c.get("/healthz").status_code, 200)
        UpdateLock.objects.all().delete()
        reset_cache()
        self.assertNotEqual(c.get("/").status_code, 503)

    def test_plugin_install_blocked_during_core_update(self):
        from apps.plugins import installer
        from apps.plugins.installer import InstallError

        UpdateLock.objects.create(name="global", holder="h", operation="apply-core")
        with self.assertRaisesRegex(InstallError, "ارتقای هسته"):
            with installer.plugin_lock("any", "install"):
                pass
