"""Updating on shared hosting: an uploaded update bundle, the automatic restart, the MySQL/MariaDB backup tool and the files an update carries."""
import io
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from django.test import Client, SimpleTestCase, override_settings
from django.urls import reverse

from apps.updater import backup, package, pipeline, work
from apps.updater.errors import UpdateError
from apps.updater.models import UpdateRun

from .helpers import UpdaterCase


def bundle(files: dict) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    buf.seek(0)
    return buf


class ExtractBundleTests(UpdaterCase):
    def extract(self, files, max_total=10_000_000):
        folder = work.new_folder()
        self.addCleanup(lambda: __import__("shutil").rmtree(folder, ignore_errors=True))
        work.extract_bundle(bundle(files), folder, max_total=max_total)
        return folder

    def test_a_normal_bundle_is_unpacked_flat(self):
        folder = self.extract({"index.json": "{}", "index.json.sig": "{}", "ravix-core-1.0.0.zip": b"PK"})
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["index.json", "index.json.sig", "ravix-core-1.0.0.zip"])

    def test_dangerous_or_unexpected_names_are_refused(self):
        for bad in ("../index.json", "/etc/passwd", "sub/index.json", "evil.py", "index.json/..", "ravix-core-1.zip.exe", ".htaccess", "x\\y.zip", "ravix-core-../x.zip"):
            with self.assertRaises(UpdateError, msg=bad):
                self.extract({"index.json": "{}", bad: "x"})

    def test_index_is_required(self):
        with self.assertRaises(UpdateError):
            self.extract({"ravix-core-1.0.0.zip": b"PK"})

    def test_too_many_or_too_large(self):
        with self.assertRaises(UpdateError):
            self.extract({"index.json": "{}", **{f"ravix-core-1.0.{i}.zip": b"x" for i in range(20)}})
        with self.assertRaises(UpdateError):
            self.extract({"index.json": "{}", "ravix-core-1.0.0.zip": b"x" * 5000}, max_total=1000)

    def test_not_a_zip(self):
        with self.assertRaises(zipfile.BadZipFile):
            work.extract_bundle(io.BytesIO(b"not a zip"), work.new_folder(), max_total=1000)


class UploadViewTests(UpdaterCase):
    def post(self, user, files=None, **kw):
        c = Client()
        c.force_login(user)
        data = {}
        if files is not None:
            data["bundle"] = io.BytesIO(files.getvalue())
            data["bundle"].name = "update.zip"
        return c.post(reverse("updater:upload"), data, **kw)

    def test_only_a_superadmin_with_web_apply_on(self):
        files = bundle({"index.json": "{}"})
        self.assertEqual(self.post(self.admin, files).status_code, 403)
        c = Client()
        self.assertEqual(c.post(reverse("updater:upload")).status_code, 302)
        with override_settings(RAVIX_UPDATE_WEB_APPLY=False):
            r = self.post(self.superadmin, files, follow=True)
            self.assertEqual(r.redirect_chain[-1][0], reverse("updater:home"))

    def test_missing_or_invalid_files_are_explained(self):
        r = self.post(self.superadmin, None, follow=True)
        self.assertEqual(r.redirect_chain[-1][0], reverse("updater:home"))
        r = self.post(self.superadmin, bundle({"readme.txt": "x"}), follow=True)
        self.assertEqual(r.redirect_chain[-1][0], reverse("updater:home"))
        bad = io.BytesIO(b"nope")
        c = Client()
        c.force_login(self.superadmin)
        bad.name = "x.zip"
        r = c.post(reverse("updater:upload"), {"bundle": bad}, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(UpdateRun.objects.count(), 0)

    def test_a_valid_bundle_goes_through_the_normal_pipeline_and_leaves_nothing_behind(self):
        entry = self.publish("0.7.0")
        files = {p.name: p.read_bytes() for p in self.dist.iterdir() if p.is_file()}
        seen = {}

        def fake_apply(version, user, source=None, **kw):
            seen["files"] = sorted(p.name for p in Path(source.dir).iterdir())
            seen["dir"] = Path(source.dir)
            seen["index"] = source.index()["core"][0]["version"]
            return UpdateRun.objects.create(component="core", from_version="0.6.1", to_version="0.7.0", status="succeeded", restart_required=False)

        with mock.patch.object(pipeline, "apply_core", side_effect=fake_apply):
            r = self.post(self.superadmin, bundle(files))
        self.assertEqual(r.status_code, 302)
        self.assertIn("index.json", seen["files"])
        self.assertEqual(seen["index"], "0.7.0")
        self.assertFalse(seen["dir"].exists())  # the unpacked bundle is removed

    def test_a_bundle_signed_with_an_unknown_key_is_refused_by_the_pipeline(self):
        from .helpers import OTHER_SEED

        self.publish("0.7.0", seed=OTHER_SEED)
        files = {p.name: p.read_bytes() for p in self.dist.iterdir() if p.is_file()}
        r = self.post(self.superadmin, bundle(files), follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.disk(), "0.6.1")  # nothing changed on disk


class RestartTests(SimpleTestCase):
    def test_hosting_restarts_the_application_itself(self):
        with override_settings(RAVIX_HOSTING=True), mock.patch("apps.core.hosting.restart_app", return_value=True) as restart:
            self.assertTrue(pipeline._restart_hosting_app())
        restart.assert_called_once()

    def test_a_vps_does_nothing(self):
        with override_settings(RAVIX_HOSTING=False), mock.patch("apps.core.hosting.restart_app") as restart:
            self.assertFalse(pipeline._restart_hosting_app())
        restart.assert_not_called()


class PackageContentTests(SimpleTestCase):
    def test_an_update_carries_translations_and_the_hosting_entry_files(self):
        for entry in ("locale", "passenger_wsgi.py", "requirements-cpanel.txt"):
            self.assertIn(entry, package.CORE_ENTRIES)
            self.assertTrue((Path(__file__).resolve().parents[3] / entry).exists(), entry)


class MysqlBackendTests(SimpleTestCase):
    def make(self, password="p@ss word'\"$"):
        settings_dict = {"NAME": "db1", "HOST": "localhost", "PORT": "3306", "USER": "u1", "PASSWORD": password}
        with mock.patch.object(backup, "connection", mock.Mock(settings_dict=settings_dict, vendor="mysql")):
            return backup.MysqlBackend()

    def test_the_password_goes_through_the_environment_never_the_command_line(self):
        b = self.make()
        with mock.patch.object(backup.MysqlBackend, "_tool", return_value="/usr/bin/mysqldump"), mock.patch("subprocess.run", return_value=mock.Mock(returncode=0)) as run:
            b.backup(Path("/tmp/x.sql"))
        argv = run.call_args[0][0]
        self.assertNotIn("p@ss", " ".join(argv))
        self.assertEqual(run.call_args[1]["env"]["MYSQL_PWD"], "p@ss word'\"$")
        for flag in ("--single-transaction", "--default-character-set=utf8mb4", "--result-file=/tmp/x.sql"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[-1], "db1")

    def test_without_the_dump_tool_a_json_dump_is_written(self):
        b = self.make()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(backup.MysqlBackend, "_tool", return_value=None), \
                mock.patch("django.core.management.call_command", side_effect=lambda *a, stdout=None, **k: stdout.write('[{"model": "x"}]')):
            dest = Path(tmp) / "db.dump"
            b.backup(dest)
            import gzip

            self.assertEqual(gzip.open(dest, "rt").read(), '[{"model": "x"}]')

    def test_a_failing_dump_tool_falls_back_to_json(self):
        b = self.make()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(backup.MysqlBackend, "_tool", return_value="/x"), mock.patch("subprocess.run", return_value=mock.Mock(returncode=2)), \
                mock.patch.object(backup.MysqlBackend, "_json_dump") as fallback:
            b.backup(Path(tmp) / "d")
        fallback.assert_called_once()

    def test_restore_of_a_json_dump_flushes_then_loads(self):
        import gzip

        b = self.make()
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "db.dump"
            with gzip.open(src, "wt") as fh:
                fh.write("[]")
            calls = []
            with mock.patch.object(backup, "connection", mock.Mock()), mock.patch("django.core.management.call_command", side_effect=lambda *a, **k: calls.append(a[0])):
                b.restore(src)
        self.assertEqual(calls, ["flush", "loaddata"])

    def test_restore_of_an_sql_dump_pipes_it_to_the_client(self):
        b = self.make()
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "db.dump"
            src.write_text("-- MariaDB dump\nSELECT 1;\n")
            with mock.patch.object(backup, "connection", mock.Mock()), mock.patch.object(backup.MysqlBackend, "_tool", return_value="/usr/bin/mysql"), \
                    mock.patch("subprocess.run", return_value=mock.Mock(returncode=0)) as run:
                b.restore(src)
        self.assertEqual(run.call_args[1]["env"]["MYSQL_PWD"], "p@ss word'\"$")
        self.assertNotIn("p@ss", " ".join(run.call_args[0][0]))

    def test_restore_failure_is_reported_without_the_password(self):
        b = self.make()
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "db.dump"
            src.write_text("SELECT 1;")
            with mock.patch.object(backup, "connection", mock.Mock()), mock.patch.object(backup.MysqlBackend, "_tool", return_value="/usr/bin/mysql"), \
                    mock.patch("subprocess.run", return_value=mock.Mock(returncode=1, stderr=b"ERROR 1045 (28000): Access denied")):
                with self.assertRaises(UpdateError) as ctx:
                    b.restore(src)
        self.assertNotIn("p@ss", str(ctx.exception))

    def test_default_backend_follows_the_database(self):
        for vendor, name in (("mysql", "MysqlBackend"), ("sqlite", "SqliteBackend"), ("postgresql", "PostgresBackend")):
            with mock.patch.object(backup, "connection", mock.Mock(vendor=vendor, settings_dict={"NAME": "x", "HOST": "", "PORT": "", "USER": "", "PASSWORD": ""})):
                self.assertEqual(type(backup.default_db_backend()).__name__, name)

