import json
import shutil
import tempfile
from pathlib import Path

from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.test import TestCase, override_settings

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.plugins import signing as plugin_signing
from apps.updater import release
from apps.updater.errors import UpdateError

SEED, PUB = plugin_signing.generate_keypair()  # فقط در حافظهٔ تست؛ هیچ کلیدی در مخزن کد نیست
OTHER_SEED, OTHER_PUB = plugin_signing.generate_keypair()
SECRET_TEXT = "s3cr3t-token-value-xyz-123456"


def make_tree(base: Path, version: str, *, extra: dict | None = None, requirements="Django>=5.0\n") -> Path:
    """سورس کوچک و مصنوعی از یک «هسته» (فقط برای آزمایش مسیر ارتقا)."""
    base.mkdir(parents=True, exist_ok=True)
    files = {
        "manage.py": "#!/usr/bin/env python\nprint('manage')\n",
        "apps/__init__.py": "", "apps/core/__init__.py": "",
        "apps/core/version.py": f'RAVIX_VERSION = "{version}"\nPLUGIN_API_VERSION = 1\n',
        "config/__init__.py": "", "config/settings.py": "X = 1\n", "requirements.txt": requirements,
        "static/app.css": f"/* {version} */", "templates/base.html": f"<p>{version}</p>", "docs/readme.md": f"v{version}",
    }
    files.update(extra or {})
    for rel, content in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return base


class FakeDb:
    """جایگزین پشتیبان دیتابیس: وضعیت جدول django_migrations را ذخیره/بازیابی می‌کند (برای نشان‌دادن بازگردانی دیتابیس در تست)."""

    name = "fake"

    def __init__(self, fail_restore=False):
        self.fail_restore, self.restores = fail_restore, 0

    def backup(self, dest):
        dest.write_text(json.dumps(sorted(MigrationRecorder(connection).applied_migrations())))

    def restore(self, src):
        self.restores += 1
        if self.fail_restore:
            raise UpdateError("restore blew up")
        rec = MigrationRecorder(connection)
        rec.migration_qs.all().delete()
        for app, name in json.loads(src.read_text()):
            rec.record_applied(app, name)


class FakeRunner:
    def __init__(self, fail=(), message="boom", migrations=(("fakeapp", "0001_initial"), ("fakeapp", "0002_more"))):
        self.fail, self.message, self.migrations, self.calls = set(fail), message, migrations, []

    def check(self):
        self.calls.append("check")
        if "check" in self.fail or (self.fail & {"check2"} and self.calls.count("check") == 2):
            raise UpdateError(self.message)
        return "ok"

    def migrate(self):
        self.calls.append("migrate")
        rec = MigrationRecorder(connection)
        for app, name in self.migrations:  # مهاجرت‌ها حتی اگر بعداً شکست بخورد، تا نیمه اعمال شده‌اند
            rec.record_applied(app, name)
            if "migrate" in self.fail:
                raise UpdateError(self.message)
        return "Applying fakeapp... OK"


SITE_VERSION = "0.6.1"


class UpdaterCase(TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="ravix-upd-"))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root = make_tree(self.base / "site", SITE_VERSION)  # «سایت» در حال ارتقا
        self.src = self.base / "source"
        self.dist = self.base / "dist"
        ov = override_settings(RAVIX_UPDATE_DIR=str(self.dist), RAVIX_UPDATE_URL="", RAVIX_UPDATE_APP_ROOT=str(self.root),
                               RAVIX_UPDATE_WORK_DIR=self.base / "work", RAVIX_UPDATE_BACKUP_DIR=self.base / "backups",
                               RAVIX_TRUSTED_PUBLIC_KEYS=[f"test:{PUB}"], RAVIX_UPDATE_REQUIRE_SIGNED_INDEX=False, RAVIX_UPDATE_WEB_APPLY=True)
        ov.enable()
        self.addCleanup(ov.disable)
        # «سایت در حال اجرا» در این آزمون‌ها همیشه SITE_VERSION است، مستقل از نسخهٔ واقعی مخزن
        from unittest import mock

        for target in ("apps.updater.versions.RAVIX_VERSION",):
            p = mock.patch(target, SITE_VERSION)
            p.start()
            self.addCleanup(p.stop)
        from django.core.cache import cache

        cache.clear()
        self.db = FakeDb()
        self.runner = FakeRunner()
        self.admin = make_user("upd_admin", Role.ADMIN)
        self.superadmin = make_user("upd_root", Role.SUPER_ADMIN)

    # --- ناشر
    def publish(self, version, *, seed=SEED, requires=None, extra=None, requirements="Django>=5.0\n", sign_index=False):
        tree = make_tree(self.src / version, version, extra=extra, requirements=requirements)
        entry = release.build_core(tree, self.dist, seed=seed, requires=requires or {"plugin_api": 1, "theme_api": 1})
        release.write_index(self.dist, core=[entry], seed=SEED if sign_index else None)
        return entry

    def apply(self, version=None, **kw):
        from apps.updater import pipeline

        kw.setdefault("runner", self.runner)
        kw.setdefault("db_backend", self.db)
        kw.setdefault("root", self.root)
        return pipeline.apply_core(version, self.superadmin, **kw)

    def disk(self):
        from apps.updater import versions

        return versions.disk_version(self.root)

    def migrations(self):
        return sorted(MigrationRecorder(connection).applied_migrations())
