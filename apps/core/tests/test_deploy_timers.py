"""تایمرهای systemd (publish_scheduled، backup، expire-orders): فایل‌های unit، preflight و منطق نگهداری ۷ پشتیبان."""
import os
import re
import shutil
import stat
import subprocess
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock, skipUnless

from django.apps import apps as django_apps
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy"


def run_preflight():
    out = StringIO()
    try:
        call_command("ravix_preflight", stdout=out)
    except SystemExit:
        pass
    return out.getvalue()


class UnitFilesTests(SimpleTestCase):
    def read(self, name):
        return (DEPLOY / name).read_text(encoding="utf-8")

    def test_all_units_exist(self):
        for base in ("ravix-publish-scheduled", "ravix-backup", "ravix-expire-orders"):
            self.assertTrue((DEPLOY / f"{base}.service").is_file(), base)
            self.assertTrue((DEPLOY / f"{base}.timer").is_file(), base)

    def test_publish_scheduled_runs_every_minute(self):
        self.assertIn("OnUnitActiveSec=1min", self.read("ravix-publish-scheduled.timer"))
        svc = self.read("ravix-publish-scheduled.service")
        self.assertIn("manage.py publish_scheduled", svc)
        self.assertIn("Type=oneshot", svc)
        self.assertIn("User=ravix", svc)
        self.assertIn("EnvironmentFile=/etc/ravix/ravix.env", svc)

    def test_backup_daily_persistent_and_keeps_seven(self):
        timer = self.read("ravix-backup.timer")
        self.assertIn("OnCalendar=*-*-* 03:17:00", timer)
        self.assertIn("Persistent=true", timer)
        svc = self.read("ravix-backup.service")
        self.assertIn("ExecStart=/srv/ravix/app/deploy/backup.sh", svc)
        self.assertIn("Environment=KEEP_COUNT=7", svc)
        # KEEP_COUNT پیش از EnvironmentFile بیاید تا ravix.env بتواند آن را عوض کند
        self.assertLess(svc.index("Environment=KEEP_COUNT"), svc.index("EnvironmentFile="))

    def test_timers_are_installable(self):
        for base in ("ravix-publish-scheduled", "ravix-backup", "ravix-expire-orders"):
            self.assertIn("WantedBy=timers.target", self.read(f"{base}.timer"))

    def test_backup_script_is_executable_and_valid_bash(self):
        self.assertTrue(os.access(DEPLOY / "backup.sh", os.X_OK))
        r = subprocess.run(["bash", "-n", str(DEPLOY / "backup.sh")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_systemd_analyze_finds_no_syntax_problem(self):
        exe = shutil.which("systemd-analyze")
        if not exe:
            self.skipTest("systemd-analyze نصب نیست")
        with tempfile.TemporaryDirectory() as d:
            for f in DEPLOY.glob("ravix-*.service"):
                shutil.copy(f, d)
            for f in DEPLOY.glob("ravix-*.timer"):
                shutil.copy(f, d)
            r = subprocess.run([exe, "verify", "--man=no", *sorted(str(p) for p in Path(d).iterdir())], capture_output=True, text=True)
        # نبودن /srv/ravix روی این ماشین خطا نیست؛ هر پیام دیگری (نحو/کلید ناشناخته) خطاست
        bad = [l for l in (r.stdout + r.stderr).splitlines() if l.strip() and "is not executable" not in l]
        self.assertEqual(bad, [])


class PreflightTimerTests(TestCase):
    def test_active_timers_pass(self):
        with mock.patch("apps.core.management.commands.ravix_preflight.systemd_unit_state", return_value=("enabled", "active")):
            out = run_preflight()
        self.assertIn("ravix-publish-scheduled.timer", out)
        self.assertIn("تایمر ravix-backup.timer (پشتیبان‌گیری روزانه) فعال است", out)
        self.assertNotRegex(out, r"✘.*تایمر")

    def test_missing_or_inactive_timer_is_an_error_with_fix_command(self):
        def state(unit):
            if unit.startswith("ravin-"):
                return None  # نصب قدیمی نیست
            return ("disabled", "inactive") if unit == "ravix-backup.timer" else ("enabled", "active")

        with mock.patch("apps.core.management.commands.ravix_preflight.systemd_unit_state", side_effect=state):
            out = run_preflight()
        self.assertRegex(out, r"✘.*ravix-backup\.timer.*فعال نیست")
        self.assertIn("systemctl enable --now ravix-backup.timer", out)
        self.assertNotRegex(out, r"✘.*ravix-publish-scheduled")

    def test_enabled_but_not_running_is_an_error(self):
        with mock.patch("apps.core.management.commands.ravix_preflight.systemd_unit_state", return_value=("enabled", "inactive")):
            out = run_preflight()
        self.assertRegex(out, r"✘.*تایمر.*فعال نیست")

    def test_no_systemd_is_only_a_warning(self):
        with mock.patch("apps.core.management.commands.ravix_preflight.systemd_unit_state", return_value=None):
            out = run_preflight()
        self.assertIn("قابل بررسی نبود", out)
        self.assertNotRegex(out, r"✘.*تایمر")

    @override_settings(SHOP_ENABLED=False)
    def test_expire_orders_timer_only_checked_when_shop_enabled(self):
        calls = []
        with mock.patch("apps.core.management.commands.ravix_preflight.systemd_unit_state", side_effect=lambda u: calls.append(u) or ("enabled", "active")):
            run_preflight()
        self.assertEqual(calls, ["ravix-publish-scheduled.timer", "ravix-backup.timer"])

    # The shop is an optional module (CI also runs with RAVIX_SHOP_ENABLED=false): without its app installed this check does not exist.
    @skipUnless(django_apps.is_installed("apps.shop"), "the shop app is not installed (RAVIX_SHOP_ENABLED=false)")
    @override_settings(SHOP_ENABLED=True)
    def test_expire_orders_timer_checked_with_shop(self):
        calls = []
        with mock.patch("apps.core.management.commands.ravix_preflight.systemd_unit_state", side_effect=lambda u: calls.append(u) or ("enabled", "active")):
            run_preflight()
        self.assertIn("ravix-expire-orders.timer", calls)


class BackupRetentionTests(SimpleTestCase):
    """backup.sh واقعی با pg_dump/pg_restore جعلی: ۷ پشتیبان کامل آخر می‌مانند."""

    def setUp(self):
        if not shutil.which("bash") or not shutil.which("python3"):
            self.skipTest("bash/python3 در دسترس نیست")
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "pg_dump").write_text('#!/bin/sh\nfor a in "$@"; do case "$a" in --file=*) echo dump > "${a#--file=}";; esac; done\n')
        (bin_dir / "pg_restore").write_text("#!/bin/sh\nexit 0\n")
        for f in bin_dir.iterdir():
            f.chmod(f.stat().st_mode | stat.S_IEXEC)
        self.backups = self.tmp / "backups"
        self.backups.mkdir()
        (self.tmp / "media").mkdir()
        (self.tmp / "media" / "a.txt").write_text("x")
        self.env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "DATABASE_URL": "postgres://u:XXXX@localhost/db",
                    "BACKUP_DIR": str(self.backups), "MEDIA_DIR": str(self.tmp / "media"), "RAVIX_ENV_FILE": str(self.tmp / "none.env"),
                    "SHOP_PRIVATE_ROOT": str(self.tmp / "no1"), "RAVIX_PLUGIN_INSTALL_DIR": str(self.tmp / "no2"),
                    "RAVIX_PLUGIN_BACKUP_DIR": str(self.tmp / "no3"), "RAVIX_THEME_INSTALL_DIR": str(self.tmp / "no4"),
                    "RAVIX_REGISTRY_STORAGE": str(self.tmp / "no5")}

    def seed(self, stamp, complete=True, age_days=0):
        names = [f"ravix-{stamp}.dump", f"ravix-{stamp}.media.tar.gz"] + ([f"ravix-{stamp}.sha256"] if complete else [])
        for n in names:
            p = self.backups / n
            p.write_text("x")
            if age_days:
                t = p.stat().st_mtime - age_days * 86400
                os.utime(p, (t, t))

    def run_backup(self, **extra):
        return subprocess.run(["bash", str(DEPLOY / "backup.sh")], env={**self.env, **extra}, capture_output=True, text=True, timeout=60)

    def stamps(self):
        return sorted({re.match(r"ravix-(\d{8}-\d{6})", p.name).group(1) for p in self.backups.iterdir()})

    def test_keeps_newest_seven_complete_backups(self):
        for d in range(1, 11):
            self.seed(f"202601{d:02d}-031700")
        r = self.run_backup()
        self.assertEqual(r.returncode, 0, r.stderr)
        stamps = self.stamps()
        self.assertEqual(len(stamps), 7)  # ۶ قدیمی‌تر + پشتیبان تازه
        self.assertNotIn("20260101-031700", stamps)
        self.assertIn("20260110-031700", stamps)
        self.assertTrue(list(self.backups.glob("ravix-*.media.tar.gz")))

    def test_failed_incomplete_run_does_not_evict_good_backups(self):
        for d in range(1, 8):
            self.seed(f"202601{d:02d}-031700")
        self.seed("20260108-031700", complete=False, age_days=0)  # اجرای ناقصِ جدید
        self.seed("20251201-031700", complete=False, age_days=30)  # بقایای ناقص قدیمی
        r = self.run_backup()
        self.assertEqual(r.returncode, 0, r.stderr)
        stamps = self.stamps()
        self.assertNotIn("20251201-031700", stamps)  # بقایای ناقصِ قدیمی پاک شد
        self.assertIn("20260108-031700", stamps)  # ناقصِ تازه ممکن است هنوز در حال اجرا باشد؛ دست نمی‌خورد
        complete = [s for s in stamps if (self.backups / f"ravix-{s}.sha256").exists()]
        self.assertEqual(len(complete), 7)

    def test_keep_count_is_configurable_and_validated(self):
        for d in range(1, 6):
            self.seed(f"202601{d:02d}-031700")
        self.assertEqual(self.run_backup(KEEP_COUNT="3").returncode, 0)
        self.assertEqual(len([s for s in self.stamps() if (self.backups / f"ravix-{s}.sha256").exists()]), 3)
        for bad in ("0", "abc", "-1", ""):
            r = self.run_backup(KEEP_COUNT=bad) if bad else None
            if r is not None:
                self.assertNotEqual(r.returncode, 0, bad)
                self.assertIn("KEEP_COUNT", r.stderr)

    def test_backup_files_and_hashes_are_valid(self):
        r = self.run_backup()
        self.assertEqual(r.returncode, 0, r.stderr)
        sums = next(self.backups.glob("ravix-*.sha256"))
        chk = subprocess.run(["sha256sum", "-c", sums.name], cwd=self.backups, capture_output=True, text=True)
        self.assertEqual(chk.returncode, 0, chk.stdout + chk.stderr)
