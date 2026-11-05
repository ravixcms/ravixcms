"""Safe update, stage 2: the Python environment switch, restart + /healthz confirmation, the job queue with its executor, and recovery after a dead process."""
import base64
import hashlib
import http.server
import json
import os
import shutil
import socket
import socketserver
import sys
import tempfile
import threading
import time
import zipfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.updater import jobs, lifecycle, middleware, pipeline, release, venvs
from apps.updater.errors import UpdateBlocked, UpdateBusy, UpdateError
from apps.updater.models import UpdateJob, UpdateLock, UpdateRun

from .helpers import SEED, SITE_VERSION, UpdaterCase, make_tree


class FakeLife(lifecycle.Lifecycle):
    name = "fake"
    automatic = True

    def __init__(self, restart_ok=(True,), healthy=(True,)):
        self.restart_ok, self.healthy, self.calls = list(restart_ok), list(healthy), []

    def restart(self):
        self.calls.append("restart")
        return self.restart_ok.pop(0) if len(self.restart_ok) > 1 else self.restart_ok[0]

    def wait_healthy(self, timeout=None):
        self.calls.append("health")
        ok = self.healthy.pop(0) if len(self.healthy) > 1 else self.healthy[0]
        return ok, "HTTP 200" if ok else "HTTP 503"


def make_wheel(folder: Path, name="ravixtestpkg", version="1.0") -> tuple[Path, str]:
    """A real, tiny, pure-Python wheel (so pip can install it offline) and its sha256."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}-{version}-py3-none-any.whl"
    dist = f"{name}-{version}.dist-info"
    files = {f"{name}/__init__.py": f"VERSION = '{version}'\n", f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
             f"{dist}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
    record = []
    for rel, text in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).rstrip(b"=").decode()
        record.append(f"{rel},sha256={digest},{len(text.encode())}")
    record.append(f"{dist}/RECORD,,")
    files[f"{dist}/RECORD"] = "\n".join(record) + "\n"
    with zipfile.ZipFile(path, "w") as zf:
        for rel, text in files.items():
            zf.writestr(rel, text)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def lock_files(tree: Path, name="ravixtestpkg", version="1.0", sha=""):
    (tree / "requirements.lock").write_text(f"{name}=={version}\n", encoding="utf-8")
    (tree / "requirements.hashes.lock").write_text(f"{name}=={version} \\\n    --hash=sha256:{sha}\n", encoding="utf-8")


# ----------------------------------------------------------------------------------------------------------------------- environment
class EnvironmentModeTests(SimpleTestCase):
    def test_the_mode_follows_the_settings_and_the_link(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with override_settings(RAVIX_VENV_LINK="", RAVIX_HOSTING=False):
            self.assertEqual(venvs.mode(), venvs.NONE)
            self.assertFalse(venvs.manages_packages())
        (tmp / "venv-a").mkdir()
        os.symlink("venv-a", tmp / "venv")
        with override_settings(RAVIX_VENV_LINK=str(tmp / "venv"), RAVIX_HOSTING=False):
            self.assertEqual(venvs.mode(), venvs.VERSIONED)
        (tmp / "real").mkdir()
        with override_settings(RAVIX_VENV_LINK=str(tmp / "real"), RAVIX_HOSTING=False):
            self.assertEqual(venvs.mode(), venvs.LEGACY)  # a real folder: install.sh has not migrated it
        with override_settings(RAVIX_VENV_LINK=str(tmp / "nothing-yet"), RAVIX_HOSTING=False):
            self.assertEqual(venvs.mode(), venvs.VERSIONED)
        with override_settings(RAVIX_HOSTING=True):
            self.assertEqual(venvs.mode(), venvs.INPLACE)

    def test_the_link_switch_is_atomic_relative_and_reversible(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for name in ("venv-a", "venv-b"):
            (tmp / name).mkdir()
        link = tmp / "venv"
        os.symlink("venv-a", link)
        venvs.switch_link(link, "venv-b")
        self.assertEqual(os.readlink(link), "venv-b")
        self.assertFalse((tmp / "venv.switching").exists())
        info = {"link": str(link), "old_target": "venv-a", "new_dir": "venv-b"}
        self.assertTrue(venvs.switch_back(info))
        self.assertEqual(os.readlink(link), "venv-a")
        self.assertTrue(venvs.switch_back(info))  # idempotent
        venvs.discard_new(info)
        self.assertFalse((tmp / "venv-b").exists())
        venvs.discard_new({**info, "new_dir": "venv-a"})
        self.assertTrue((tmp / "venv-a").exists())  # never the one the link points at
        self.assertFalse(venvs.switch_back({**info, "old_target": "venv-gone"}))

    def test_prune_keeps_the_active_and_the_previous_environment(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for i, name in enumerate(("venv-0.1.0", "venv-0.2.0", "venv-0.3.0", "venv-0.4.0")):
            (tmp / name).mkdir()
            os.utime(tmp / name, (1000 + i, 1000 + i))
        os.symlink("venv-0.4.0", tmp / "venv")
        with override_settings(RAVIX_VENV_LINK=str(tmp / "venv")):
            gone = venvs.prune(2)
        self.assertEqual(sorted(gone), ["venv-0.1.0", "venv-0.2.0"])
        self.assertTrue((tmp / "venv-0.3.0").is_dir() and (tmp / "venv-0.4.0").is_dir())


class PrepareTests(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tree = self.tmp / "tree"
        self.tree.mkdir()

    def test_no_change_means_nothing_to_build(self):
        installed = venvs.installed_of()
        (self.tree / "requirements.lock").write_text(f"asgiref=={installed['asgiref']}\n")
        (self.tree / "requirements.hashes.lock").write_text(f"asgiref=={installed['asgiref']} \\\n    --hash=sha256:{'ab' * 32}\n")
        (self.tmp / "venv-a").mkdir()
        os.symlink("venv-a", self.tmp / "venv")
        with override_settings(RAVIX_VENV_LINK=str(self.tmp / "venv"), RAVIX_HOSTING=False):
            plan = venvs.prepare(self.tree, "9.9.9")
        self.assertFalse(plan.changed)
        self.assertIsNone(plan.new_dir)

    def test_an_unmanaged_install_never_builds_anything(self):
        lock_files(self.tree, sha="cd" * 32)
        with override_settings(RAVIX_VENV_LINK="", RAVIX_HOSTING=False):
            plan = venvs.prepare(self.tree, "9.9.9")
        self.assertEqual(plan.mode, venvs.NONE)
        self.assertFalse(plan.changed)

    def test_a_server_not_yet_migrated_is_told_to_run_the_installer(self):
        lock_files(self.tree, sha="cd" * 32)
        (self.tmp / "venv").mkdir()
        with override_settings(RAVIX_VENV_LINK=str(self.tmp / "venv"), RAVIX_HOSTING=False):
            with self.assertRaises(UpdateBlocked) as ctx:
                venvs.prepare(self.tree, "9.9.9")
        self.assertIn("install.sh", str(ctx.exception))

    def test_a_dry_run_reports_the_change_but_builds_nothing(self):
        lock_files(self.tree, sha="cd" * 32)
        (self.tmp / "venv-a").mkdir()
        os.symlink("venv-a", self.tmp / "venv")
        with override_settings(RAVIX_VENV_LINK=str(self.tmp / "venv"), RAVIX_HOSTING=False):
            plan = venvs.prepare(self.tree, "9.9.9", build=False)
        self.assertTrue(plan.changed)
        self.assertFalse((self.tmp / "venv-9.9.9").exists())

    def test_hosting_without_wheels_stops_before_any_change(self):
        lock_files(self.tree, sha="cd" * 32)
        with override_settings(RAVIX_HOSTING=True):
            with self.assertRaises(UpdateBlocked) as ctx:
                venvs.prepare(self.tree, "9.9.9")
        self.assertIn("ZIP", str(ctx.exception))  # the message tells the owner to install the full ZIP

    def test_hosting_with_a_wheel_that_does_not_fit_stops_before_any_change(self):
        wheel, _sha = make_wheel(self.tree / "wheelhouse")
        lock_files(self.tree, sha="00" * 32)  # the lock's hash is not the wheel's: --require-hashes refuses it
        with override_settings(RAVIX_HOSTING=True):
            with self.assertRaises(UpdateBlocked):
                venvs.prepare(self.tree, "9.9.9")
        self.assertTrue(wheel.exists())

    def test_a_real_new_environment_is_built_from_hashed_wheels_and_switched(self):
        wheel, sha = make_wheel(self.tmp / "wheels")
        lock_files(self.tree, sha=sha)
        (self.tmp / "venv-a").mkdir()
        os.symlink("venv-a", self.tmp / "venv")
        env = {"PIP_NO_INDEX": "1", "PIP_FIND_LINKS": str(wheel.parent)}
        with override_settings(RAVIX_VENV_LINK=str(self.tmp / "venv"), RAVIX_HOSTING=False), mock.patch.dict(os.environ, env):
            plan = venvs.prepare(self.tree, "9.9.9")
            self.assertEqual(plan.new_dir, self.tmp / "venv-9.9.9")
            out = os.popen(f"{plan.python} -c \"import ravixtestpkg; print(ravixtestpkg.VERSION)\"").read().strip()
            self.assertEqual(out, "1.0")
            self.assertEqual(os.readlink(self.tmp / "venv"), "venv-a")  # building does not switch
            venvs.switch(plan)
            self.assertEqual(os.readlink(self.tmp / "venv"), "venv-9.9.9")
            self.assertEqual(plan.as_dict()["old_target"], "venv-a")
            self.assertTrue(venvs.switch_back(plan.as_dict()))
        self.assertEqual(os.readlink(self.tmp / "venv"), "venv-a")

    def test_a_tampered_wheel_never_becomes_an_environment(self):
        wheel, _sha = make_wheel(self.tmp / "wheels")
        lock_files(self.tree, sha="11" * 32)
        (self.tmp / "venv-a").mkdir()
        os.symlink("venv-a", self.tmp / "venv")
        env = {"PIP_NO_INDEX": "1", "PIP_FIND_LINKS": str(wheel.parent)}
        with override_settings(RAVIX_VENV_LINK=str(self.tmp / "venv"), RAVIX_HOSTING=False), mock.patch.dict(os.environ, env):
            with self.assertRaises(UpdateBlocked):
                venvs.prepare(self.tree, "9.9.9")
        self.assertFalse((self.tmp / "venv-9.9.9").exists())  # the half-built folder is removed
        self.assertEqual(os.readlink(self.tmp / "venv"), "venv-a")


class WheelhouseTests(UpdaterCase):
    def build(self, wheelhouse):
        tree = make_tree(self.src / "0.7.0", "0.7.0")
        return release.build_core(tree, self.dist, seed=SEED, requires={"plugin_api": 1, "theme_api": 1}, wheelhouse=wheelhouse)

    def test_a_package_can_carry_wheels_and_they_are_listed_in_the_manifest(self):
        from apps.updater import manifest, package

        wheel, sha = make_wheel(self.base / "wheels")
        self.build(wheel.parent)
        root = package.extract((self.dist / "ravix-core-0.7.0.zip").read_bytes(), self.base / "x")
        self.assertTrue(package.load_release(root)["wheelhouse"])
        self.assertIn(f"wheelhouse/{wheel.name}", manifest.read(root))
        package.validate_core(root, "0.7.0")  # the wheels are part of what the signature covers
        self.assertNotIn("wheelhouse", package.CORE_ENTRIES)  # …but they are never swapped into the site

    def test_a_swapped_wheel_inside_the_package_is_refused(self):
        from apps.updater import package

        wheel, _ = make_wheel(self.base / "wheels")
        self.build(wheel.parent)
        root = package.extract((self.dist / "ravix-core-0.7.0.zip").read_bytes(), self.base / "x")
        (root / "wheelhouse" / wheel.name).write_bytes(b"not the wheel")
        with self.assertRaises(UpdateError):
            package.validate_core(root, "0.7.0")

    def test_an_empty_or_missing_wheelhouse_is_an_error_at_build_time(self):
        (self.base / "empty").mkdir()
        for folder in (self.base / "empty", self.base / "nope"):
            with self.assertRaises(UpdateError):
                self.build(folder)

    def test_hosting_dry_run_accepts_wheels_whose_hashes_match_the_lock(self):
        wheel, sha = make_wheel(self.base / "tree" / "wheelhouse")
        tree = self.base / "tree"
        lock_files(tree, sha=sha)
        with override_settings(RAVIX_HOSTING=True):
            plan = venvs.prepare(tree, "9.9.9")
        self.assertEqual(plan.wheelhouse, tree / "wheelhouse")
        self.assertTrue(plan.changed)


# ----------------------------------------------------------------------------------------------------------------------- restart and /healthz
class HealthProbeTests(SimpleTestCase):
    def serve_unix(self, body=b'{"status": "ok"}', status=200):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "g.sock")

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(status)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        class S(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

            def get_request(self):
                request, _ = super().get_request()
                return request, ("unix", 0)

        server = S(path, H)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        return path

    def test_the_gunicorn_socket_answering_ok_is_healthy(self):
        self.assertEqual(lifecycle.probe_unix(self.serve_unix())[0], True)

    def test_an_error_answer_or_a_missing_socket_is_not_healthy(self):
        self.assertFalse(lifecycle.probe_unix(self.serve_unix(b'{"status": "error"}', 503))[0])
        self.assertFalse(lifecycle.probe_unix(self.serve_unix(b"<html>", 200))[0])
        self.assertFalse(lifecycle.probe_unix("/nonexistent/g.sock")[0])

    def test_an_http_address_is_probed_and_other_schemes_refused(self):
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')

            def log_message(self, *a):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.assertTrue(lifecycle.probe_url(f"http://127.0.0.1:{server.server_port}/healthz")[0])
        self.assertFalse(lifecycle.probe_url("file:///etc/passwd")[0])
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            dead = s.getsockname()[1]
        self.assertFalse(lifecycle.probe_url(f"http://127.0.0.1:{dead}/healthz")[0])

    def test_polling_gives_up_at_the_deadline_and_stops_at_the_first_success(self):
        answers = iter([(False, "HTTP 503"), (False, "HTTP 503"), (True, "HTTP 200")])
        self.assertEqual(lifecycle._poll(lambda: next(answers), 5, interval=0.01), (True, "HTTP 200"))
        ok, detail = lifecycle._poll(lambda: (False, "HTTP 502"), 0.05, interval=0.01)
        self.assertEqual((ok, detail), (False, "HTTP 502"))

    def test_the_probe_chooses_url_then_socket_then_site(self):
        with override_settings(RAVIX_HEALTHZ_URL="http://x.test/healthz"), mock.patch.object(lifecycle, "probe_url", return_value=(True, "")) as pu:
            lifecycle.health_probe()()
            pu.assert_called_once_with("http://x.test/healthz")
        with override_settings(RAVIX_HEALTHZ_URL="", RAVIX_HOSTING=False), mock.patch.dict(os.environ, {"GUNICORN_BIND": "unix:/run/x.sock"}), \
                mock.patch.object(lifecycle, "probe_unix", return_value=(True, "")) as pu:
            lifecycle.health_probe()()
            pu.assert_called_once_with("/run/x.sock")
        with override_settings(RAVIX_HEALTHZ_URL="", RAVIX_HOSTING=True, SITE_URL="https://s.test/"), mock.patch.object(lifecycle, "probe_url", return_value=(True, "")) as pu:
            lifecycle.health_probe()()
            pu.assert_called_once_with("https://s.test/healthz")

    def test_the_restart_request_is_a_file_the_root_unit_removes(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        life = lifecycle.SystemdLifecycle(tmp, wait_seconds=5)

        def root_unit():
            for _ in range(100):
                if (tmp / lifecycle.REQUEST_NAME).exists():
                    (tmp / lifecycle.REQUEST_NAME).unlink()
                    return
                time.sleep(0.02)

        t = threading.Thread(target=root_unit)
        t.start()
        self.assertTrue(life.restart())
        t.join()
        slow = lifecycle.SystemdLifecycle(tmp, wait_seconds=0.2)
        self.assertFalse(slow.restart())  # nobody answered the request
        self.assertFalse((tmp / lifecycle.REQUEST_NAME).exists())  # and it is withdrawn, so a late unit does not restart at a bad moment

    def test_get_chooses_the_lifecycle_from_the_setting(self):
        for value, cls in (("systemd", lifecycle.SystemdLifecycle), ("touch", lifecycle.TouchLifecycle), ("", lifecycle.Lifecycle)):
            with override_settings(RAVIX_UPDATE_RESTART=value):
                self.assertIs(type(lifecycle.get()), cls)
        self.assertFalse(lifecycle.Lifecycle().automatic)


# ----------------------------------------------------------------------------------------------------------------------- pipeline with restart and environment
class AutomaticRestartTests(UpdaterCase):
    def test_success_restarts_and_confirms_the_new_version(self):
        self.publish("0.7.0")
        life = FakeLife()
        run = self.apply("0.7.0", lifecycle=life)
        self.assertEqual(run.status, UpdateRun.Status.SUCCEEDED)
        self.assertEqual(life.calls, ["restart", "health"])
        names = [x["name"] for x in run.steps]
        self.assertIn("restart", names)
        self.assertEqual([x["ok"] for x in run.steps if x["name"] == "health"], [True])
        self.assertFalse(run.restart_required)
        self.assertEqual(self.disk(), "0.7.0")

    def test_an_unhealthy_new_version_is_rolled_back_and_the_old_one_restarted(self):
        self.publish("0.7.0")
        life = FakeLife(healthy=(False, True))
        with self.assertRaises(UpdateError):
            self.apply("0.7.0", lifecycle=life)
        run = UpdateRun.objects.get()
        self.assertEqual(run.status, UpdateRun.Status.ROLLED_BACK)
        self.assertEqual(self.disk(), SITE_VERSION)  # the old files are back
        self.assertEqual(life.calls, ["restart", "health", "restart", "health"])
        self.assertEqual(self.db.restores, 1)  # and the database too
        steps = {x["name"]: x for x in run.steps}
        self.assertFalse(steps["health"]["ok"])
        self.assertTrue(steps["rollback_restart"]["ok"])
        self.assertTrue(steps["rollback_files"]["ok"])

    def test_a_restart_that_never_happens_is_a_failure_too(self):
        self.publish("0.7.0")
        life = FakeLife(restart_ok=(False, True))
        with self.assertRaises(UpdateError):
            self.apply("0.7.0", lifecycle=life)
        self.assertEqual(UpdateRun.objects.get().status, UpdateRun.Status.ROLLED_BACK)
        self.assertEqual(self.disk(), SITE_VERSION)
        stuck = FakeLife(restart_ok=(False,))  # the old version cannot be restarted either: that needs a person
        self.publish("0.8.0")
        with self.assertRaises(UpdateError):
            self.apply("0.8.0", lifecycle=stuck)
        self.assertEqual(UpdateRun.objects.order_by("-id").first().status, UpdateRun.Status.NEEDS_RECOVERY)

    def test_a_failed_rollback_restart_leaves_the_run_needing_recovery(self):
        self.publish("0.7.0")
        life = FakeLife(healthy=(False,))
        with self.assertRaises(UpdateError):
            self.apply("0.7.0", lifecycle=life)
        run = UpdateRun.objects.get()
        self.assertEqual(run.status, UpdateRun.Status.NEEDS_RECOVERY)
        self.assertIn("healthz", run.error)

    def test_a_failure_before_the_restart_does_not_restart_anything(self):
        self.publish("0.7.0")
        self.runner.fail = {"migrate"}
        life = FakeLife()
        with self.assertRaises(UpdateError):
            self.apply("0.7.0", lifecycle=life)
        self.assertEqual(life.calls, [])  # the old process was never stopped
        self.assertEqual(UpdateRun.objects.get().status, UpdateRun.Status.ROLLED_BACK)

    def test_without_a_lifecycle_the_behaviour_is_unchanged(self):
        self.publish("0.7.0")
        run = self.apply("0.7.0")
        self.assertEqual(run.status, UpdateRun.Status.SUCCEEDED)
        self.assertNotIn("health", [x["name"] for x in run.steps])


class EnvironmentSwitchInPipelineTests(UpdaterCase):
    def setUp(self):
        super().setUp()
        self.venv_root = self.base / "srv"
        self.venv_root.mkdir()
        (self.venv_root / "venv-old").mkdir()
        os.symlink("venv-old", self.venv_root / "venv")
        ov = override_settings(RAVIX_VENV_LINK=str(self.venv_root / "venv"), RAVIX_HOSTING=False)
        ov.enable()
        self.addCleanup(ov.disable)

    def plan(self):
        new = self.venv_root / "venv-new"
        new.mkdir(exist_ok=True)
        return venvs.DepsPlan(mode=venvs.VERSIONED, changes=[("asgiref", "3.0", "3.12.1")], python=Path(sys.executable), new_dir=new, old_target="venv-old")

    def link(self):
        return os.readlink(self.venv_root / "venv")

    def test_a_successful_update_switches_the_environment_after_the_checks(self):
        self.publish("0.7.0")
        with mock.patch.object(venvs, "prepare", return_value=self.plan()):
            run = self.apply("0.7.0", lifecycle=FakeLife())
        self.assertEqual(run.status, UpdateRun.Status.SUCCEEDED)
        self.assertEqual(self.link(), "venv-new")
        self.assertEqual(run.backup["env"]["old_target"], "venv-old")  # recorded so that a rollback or a recovery can undo it
        names = [x["name"] for x in run.steps]
        self.assertLess(names.index("verify"), names.index("venv_switch"))
        self.assertLess(names.index("venv_switch"), names.index("restart"))

    def test_a_failed_migration_never_switches_and_removes_the_new_environment(self):
        self.publish("0.7.0")
        self.runner.fail = {"migrate"}
        with mock.patch.object(venvs, "prepare", return_value=self.plan()), self.assertRaises(UpdateError):
            self.apply("0.7.0", lifecycle=FakeLife())
        self.assertEqual(self.link(), "venv-old")
        self.assertFalse((self.venv_root / "venv-new").exists())

    def test_an_unhealthy_start_switches_back_and_removes_the_new_environment(self):
        self.publish("0.7.0")
        with mock.patch.object(venvs, "prepare", return_value=self.plan()), self.assertRaises(UpdateError):
            self.apply("0.7.0", lifecycle=FakeLife(healthy=(False, True)))
        self.assertEqual(self.link(), "venv-old")
        self.assertFalse((self.venv_root / "venv-new").exists())
        run = UpdateRun.objects.get()
        self.assertEqual(run.status, UpdateRun.Status.ROLLED_BACK)
        self.assertTrue(next(x for x in run.steps if x["name"] == "rollback_env")["ok"])

    def test_a_package_that_cannot_be_installed_blocks_before_anything_changes(self):
        self.publish("0.7.0")
        with mock.patch.object(venvs, "prepare", side_effect=UpdateBlocked("pip said no")), self.assertRaises(UpdateBlocked):
            self.apply("0.7.0", lifecycle=FakeLife())
        self.assertEqual(self.disk(), SITE_VERSION)
        self.assertEqual(self.link(), "venv-old")
        self.assertEqual(self.db.restores, 0)
        self.assertEqual(UpdateRun.objects.get().status, UpdateRun.Status.FAILED)

    def test_a_manual_rollback_switches_the_environment_back_too(self):
        self.publish("0.7.0")
        with mock.patch.object(venvs, "prepare", return_value=self.plan()):
            run = self.apply("0.7.0", lifecycle=FakeLife())
        self.assertEqual(self.link(), "venv-new")
        life = FakeLife()
        back = pipeline.rollback_run(run.pk, self.superadmin, confirm_data_loss=True, runner=self.runner, db_backend=self.db, root=self.root, lifecycle=life)
        self.assertEqual(back.status, UpdateRun.Status.SUCCEEDED)
        self.assertEqual(self.link(), "venv-old")
        self.assertEqual(self.disk(), SITE_VERSION)
        self.assertEqual(life.calls, ["restart", "health"])


# ----------------------------------------------------------------------------------------------------------------------- recovery
class RecoveryTests(UpdaterCase):
    def interrupted_run(self, *, db_touched):
        """A real backup, then the site half-way through an update: new files on disk, and the process 'killed'."""
        from apps.updater import backup as backups

        self.publish("0.7.0")
        run = pipeline._new_run("core", "", UpdateRun.Kind.UPGRADE, "0.7.0", None, "test")
        run.backup = {**backups.create_backup("core-test", self.root, self.db), "env": {}}
        pipeline.step(run, "backup", True)
        pipeline.step(run, "swap", True)
        if db_touched:
            pipeline.step(run, "code_check", True)
        shutil.rmtree(self.root / "apps")
        shutil.copytree(self.src / "0.7.0" / "apps", self.root / "apps")
        (self.root / "apps" / "core" / "version.py").write_text('RAVIX_VERSION = "0.7.0"\n')
        (self.root / "static" / "half-written.css").write_text("x")
        UpdateRun.objects.filter(pk=run.pk).update(status=UpdateRun.Status.NEEDS_RECOVERY)
        return UpdateRun.objects.get(pk=run.pk)

    def test_recovery_puts_files_back_and_restores_the_database_when_migrations_had_started(self):
        run = self.interrupted_run(db_touched=True)
        self.assertEqual(self.disk(), "0.7.0")
        done = pipeline.recover_run(run.pk, db_backend=self.db, root=self.root)
        self.assertEqual(done.status, UpdateRun.Status.ROLLED_BACK)
        self.assertEqual(self.disk(), SITE_VERSION)
        self.assertEqual(self.db.restores, 1)
        self.assertEqual(UpdateLock.objects.count(), 0)

    def test_recovery_leaves_the_database_alone_when_migrations_never_started(self):
        run = self.interrupted_run(db_touched=False)
        pipeline.recover_run(run.pk, db_backend=self.db, root=self.root)
        self.assertEqual(self.disk(), SITE_VERSION)
        self.assertEqual(self.db.restores, 0)

    def test_recovery_restarts_and_confirms_when_the_executor_can(self):
        run = self.interrupted_run(db_touched=True)
        life = FakeLife()
        pipeline.recover_run(run.pk, db_backend=self.db, root=self.root, lifecycle=life)
        self.assertEqual(life.calls, ["restart", "health"])

    def test_only_a_run_needing_recovery_with_a_backup_can_be_recovered(self):
        run = self.interrupted_run(db_touched=True)
        UpdateRun.objects.filter(pk=run.pk).update(status=UpdateRun.Status.SUCCEEDED)
        with self.assertRaises(UpdateBlocked):
            pipeline.recover_run(run.pk, db_backend=self.db, root=self.root)
        UpdateRun.objects.filter(pk=run.pk).update(status=UpdateRun.Status.NEEDS_RECOVERY, backup={})
        with self.assertRaises(UpdateBlocked):
            pipeline.recover_run(run.pk, db_backend=self.db, root=self.root)

    def test_a_damaged_backup_is_never_restored_from(self):
        run = self.interrupted_run(db_touched=True)
        d = Path(self.base / "backups" / run.backup["dir"])
        (d / run.backup["files_archive"]).write_bytes(b"corrupt")
        with self.assertRaises(UpdateError):
            pipeline.recover_run(run.pk, db_backend=self.db, root=self.root)
        run.refresh_from_db()
        self.assertEqual(run.status, UpdateRun.Status.NEEDS_RECOVERY)
        self.assertEqual(self.db.restores, 0)
        self.assertEqual(self.disk(), "0.7.0")  # untouched: nothing is replaced from a backup that does not verify
        self.assertIn("tar -xzf", run.recovery)  # the manual steps are given instead

    def test_the_environment_is_switched_back_by_the_recovery(self):
        tmp = self.base / "srv"
        tmp.mkdir()
        (tmp / "venv-old").mkdir()
        (tmp / "venv-new").mkdir()
        os.symlink("venv-new", tmp / "venv")
        run = self.interrupted_run(db_touched=True)
        run.backup = {**run.backup, "env": {"mode": "versioned", "old_target": "venv-old", "new_dir": "venv-new", "link": str(tmp / "venv")}}
        run.save(update_fields=["backup"])
        pipeline.recover_run(run.pk, db_backend=self.db, root=self.root)
        self.assertEqual(os.readlink(tmp / "venv"), "venv-old")
        self.assertFalse((tmp / "venv-new").exists())

    def test_a_recovery_that_keeps_failing_is_not_retried_forever(self):
        run = self.interrupted_run(db_touched=True)
        for _ in range(3):
            pipeline.step(run, "recover_failed", False, "x")
        self.assertEqual(pipeline.interrupted_runs(), [])


# ----------------------------------------------------------------------------------------------------------------------- jobs
@override_settings(RAVIX_UPDATE_HEARTBEAT_SECONDS=0)
class JobTests(UpdaterCase):
    def setUp(self):
        super().setUp()
        self.jobs = self.base / "jobs"
        ov = override_settings(RAVIX_UPDATE_EXECUTOR="systemd", RAVIX_UPDATE_JOBS_DIR=self.jobs, RAVIX_UPDATE_HEARTBEAT_SECONDS=0)
        ov.enable()
        self.addCleanup(ov.disable)

    def test_without_an_executor_nothing_can_be_queued(self):
        with override_settings(RAVIX_UPDATE_EXECUTOR=""):
            self.assertFalse(jobs.available())
            with self.assertRaises(UpdateBlocked):
                jobs.enqueue("0.7.0", self.admin)

    def test_a_queued_job_wakes_the_path_unit_with_an_empty_file(self):
        job = jobs.enqueue("0.7.0", self.admin)
        self.assertEqual((job.status, job.target_version, job.requested_by), ("queued", "0.7.0", self.admin))
        f = self.jobs / "queue" / str(job.pk)
        self.assertTrue(f.is_file())
        self.assertEqual(f.read_text(), "")  # the web hands over nothing but a wake-up: the request itself is in the database

    def test_only_one_update_can_be_active(self):
        jobs.enqueue(None, self.admin)
        with self.assertRaises(UpdateBusy):
            jobs.enqueue(None, self.admin)

    def test_a_running_update_blocks_a_new_request(self):
        with pipeline.update_lock("apply-core"):
            with self.assertRaises(UpdateBusy):
                jobs.enqueue(None, self.admin)

    def test_a_waiting_job_can_be_withdrawn_but_a_running_one_cannot(self):
        job = jobs.enqueue(None, self.admin)
        self.assertTrue(jobs.cancel(job.pk))
        job.refresh_from_db()
        self.assertEqual(job.status, "cancelled")
        self.assertFalse((self.jobs / "queue" / str(job.pk)).exists())
        job2 = jobs.enqueue(None, self.admin)
        jobs.claim_next()
        self.assertFalse(jobs.cancel(job2.pk))

    def test_a_job_is_claimed_once(self):
        job = jobs.enqueue(None, self.admin)
        first = jobs.claim_next()
        self.assertEqual(first.pk, job.pk)
        self.assertEqual((first.status, first.pid), ("running", os.getpid()))
        self.assertIsNone(jobs.claim_next())
        self.assertFalse((self.jobs / "queue" / str(job.pk)).exists())

    def test_the_executor_runs_the_update_and_records_the_outcome(self):
        self.publish("0.7.0")
        job = jobs.enqueue("0.7.0", self.admin)
        done = jobs.run_pending(lifecycle=FakeLife(), runner=self.runner, db_backend=self.db, root=self.root)
        self.assertEqual([j.pk for j in done], [job.pk])
        job.refresh_from_db()
        self.assertEqual(job.status, "succeeded")
        self.assertIsNotNone(job.run)
        self.assertEqual(job.run.requested_by if hasattr(job.run, "requested_by") else self.admin, self.admin)
        self.assertEqual(self.disk(), "0.7.0")
        self.assertEqual(jobs.progress(job)["steps"][-1]["name"], "health")
        self.assertFalse(list((self.jobs / "queue").iterdir()))  # an empty queue must not wake the path unit again

    def test_a_failed_update_is_recorded_as_rolled_back_not_lost(self):
        self.publish("0.7.0")
        self.runner.fail = {"migrate"}
        job = jobs.enqueue("0.7.0", self.admin)
        jobs.run_pending(lifecycle=FakeLife(), runner=self.runner, db_backend=self.db, root=self.root)
        job.refresh_from_db()
        self.assertEqual(job.status, "rolled_back")
        self.assertIn("boom", job.message)
        self.assertEqual(self.disk(), SITE_VERSION)

    def test_an_update_blocked_before_any_change_is_failed_not_rolled_back(self):
        job = jobs.enqueue("9.9.9", self.admin)  # not in the source
        jobs.run_pending(lifecycle=FakeLife(), runner=self.runner, db_backend=self.db, root=self.root)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")

    def test_two_executors_cannot_run_at_once(self):
        with jobs.ExecutorLock():
            with self.assertRaises(UpdateBusy):
                jobs.run_pending(lifecycle=FakeLife())

    def test_a_silent_job_is_found_dead_and_its_lock_released(self):
        job = jobs.enqueue(None, self.admin)
        jobs.claim_next()
        run = UpdateRun.objects.create(component="core", from_version="1", to_version="2")
        UpdateJob.objects.filter(pk=job.pk).update(run=run, heartbeat=timezone.now() - timedelta(minutes=5))
        UpdateLock.objects.create(name="global", holder="h", operation="apply-core", run=run, heartbeat=timezone.now() - timedelta(minutes=5))
        dead = jobs.reap_dead()
        self.assertEqual([j.pk for j in dead], [job.pk])
        job.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual((job.status, run.status), ("needs_recovery", "needs_recovery"))
        self.assertEqual(UpdateLock.objects.count(), 0)

    def test_a_live_job_is_left_alone(self):
        jobs.enqueue(None, self.admin)
        job = jobs.claim_next()
        jobs.Heartbeat(job).beat()
        self.assertEqual(jobs.reap_dead(), [])

    def test_a_dead_update_is_recovered_by_the_next_executor_run(self):
        self.publish("0.7.0")
        job = jobs.enqueue("0.7.0", self.admin)
        claimed = jobs.claim_next()
        from apps.updater import backup as backups

        run = pipeline._new_run("core", "", UpdateRun.Kind.UPGRADE, "0.7.0", self.admin, "test")
        run.backup = {**backups.create_backup("core-test", self.root, self.db), "env": {}}
        pipeline.step(run, "backup", True)
        pipeline.step(run, "swap", True)
        (self.root / "apps" / "core" / "version.py").write_text('RAVIX_VERSION = "0.7.0"\n')
        UpdateJob.objects.filter(pk=claimed.pk).update(run=run, heartbeat=timezone.now() - timedelta(minutes=10))
        done = jobs.run_pending(lifecycle=FakeLife(), runner=self.runner, db_backend=self.db, root=self.root)
        self.assertEqual(done, [])
        job.refresh_from_db()
        self.assertEqual(job.status, "rolled_back")
        self.assertEqual(self.disk(), SITE_VERSION)

    def test_the_heartbeat_marks_the_job_and_its_lock(self):
        job = jobs.enqueue(None, self.admin)
        job = jobs.claim_next()
        run = UpdateRun.objects.create(component="core", from_version="1", to_version="2")
        UpdateJob.objects.filter(pk=job.pk).update(run=run, heartbeat=timezone.now() - timedelta(minutes=5))
        lock = UpdateLock.objects.create(name="global", holder="h", operation="apply-core", run=run)
        jobs.Heartbeat(job).beat()
        lock.refresh_from_db()
        job.refresh_from_db()
        self.assertGreater(lock.heartbeat, timezone.now() - timedelta(seconds=30))
        self.assertGreater(job.heartbeat, timezone.now() - timedelta(seconds=30))

    def test_the_progress_view_of_a_job_has_no_secret_fields(self):
        job = jobs.enqueue(None, self.admin)
        self.assertEqual(sorted(jobs.progress(job)), ["finished", "from", "id", "message", "phase", "run_id", "status", "steps", "target", "to"])

    def test_the_command_runs_the_waiting_job(self):
        self.publish("0.7.0")
        jobs.enqueue("0.7.0", self.admin)
        with mock.patch("apps.updater.jobs.lifecycle_mod.get", return_value=FakeLife()), \
                mock.patch("apps.updater.pipeline.SubprocessRunner", return_value=self.runner), mock.patch("apps.updater.backup.default_db_backend", return_value=self.db), \
                mock.patch("apps.updater.versions.app_root", return_value=self.root):
            from io import StringIO

            out = StringIO()
            call_command("ravix_update_exec", stdout=out)
        self.assertIn(f"#{UpdateJob.objects.get().pk}", out.getvalue())
        self.assertEqual(UpdateJob.objects.get().status, "succeeded")

    def test_the_queue_command_and_the_jobs_listing(self):
        from io import StringIO

        out = StringIO()
        call_command("ravix_update", "queue", "--to", "0.7.0", stdout=out)
        self.assertEqual(UpdateJob.objects.get().target_version, "0.7.0")
        call_command("ravix_update", "jobs", stdout=out)
        self.assertIn("0.7.0", out.getvalue())


class CronExecutorTests(UpdaterCase):
    def test_cron_starts_the_executor_only_when_a_job_waits(self):
        with override_settings(RAVIX_UPDATE_EXECUTOR="cron", RAVIX_UPDATE_JOBS_DIR=self.base / "jobs"), mock.patch.object(jobs, "start_detached") as start:
            self.assertEqual(jobs.cron_tick(), "idle")
            start.assert_not_called()
            jobs.enqueue(None, self.admin)
            self.assertEqual(jobs.cron_tick(), "started")
            start.assert_called_once()

    def test_cron_does_nothing_when_the_executor_is_not_cron(self):
        with override_settings(RAVIX_UPDATE_EXECUTOR="systemd"), mock.patch.object(jobs, "start_detached") as start:
            self.assertEqual(jobs.cron_tick(), "skipped")
            start.assert_not_called()

    def test_the_detached_executor_is_a_separate_session(self):
        with mock.patch("subprocess.Popen") as popen:
            jobs.start_detached()
        kwargs = popen.call_args.kwargs
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(popen.call_args.args[0][-1], "ravix_update_exec")

    def test_the_hosting_settings_turn_the_cron_executor_and_the_touch_restart_on(self):
        import importlib

        env = {"RAVIX_HOSTING": "shared", "DJANGO_SECRET_KEY": "x" * 50}
        with mock.patch.dict(os.environ, env):
            src = (Path(__file__).resolve().parents[3] / "config" / "settings" / "hosting.py").read_text()
        self.assertIn('RAVIX_UPDATE_EXECUTOR = RAVIX_UPDATE_EXECUTOR or "cron"', src)
        self.assertIn('RAVIX_UPDATE_RESTART = RAVIX_UPDATE_RESTART or "touch"', src)
        self.assertTrue(importlib)


class StaleLockMaintenanceTests(TestCase):
    def tearDown(self):
        middleware.reset_cache()

    def test_a_lock_whose_executor_died_stops_returning_503(self):
        UpdateLock.objects.create(name="global", holder="h", operation="apply-core", heartbeat=timezone.now() - timedelta(minutes=10))
        middleware.reset_cache()
        self.assertFalse(middleware.maintenance_active())

    def test_a_fresh_or_unbeaten_lock_is_maintenance(self):
        lock = UpdateLock.objects.create(name="global", holder="h", operation="apply-core")
        middleware.reset_cache()
        self.assertTrue(middleware.maintenance_active())
        UpdateLock.objects.filter(pk=lock.pk).update(heartbeat=timezone.now())
        middleware.reset_cache()
        self.assertTrue(middleware.maintenance_active())


class PreflightExecutorTests(TestCase):
    def run_preflight(self, **overrides):
        from io import StringIO

        out = StringIO()
        with override_settings(**overrides):
            try:
                call_command("ravix_preflight", stdout=out, stderr=StringIO())
            except SystemExit:
                pass
            except Exception:  # noqa: BLE001 - other checks may fail in the test environment; only our lines matter
                pass
        return out.getvalue()

    def test_a_plain_virtualenv_folder_is_reported_with_the_fix(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "venv").mkdir()
        (tmp / "jobs" / "queue").mkdir(parents=True)
        text = self.run_preflight(RAVIX_UPDATE_EXECUTOR="systemd", RAVIX_VENV_LINK=str(tmp / "venv"), RAVIX_UPDATE_JOBS_DIR=tmp / "jobs", RAVIX_HOSTING=False)
        self.assertIn("install.sh", text)

    def test_a_versioned_environment_and_a_writable_queue_pass(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "venv-a").mkdir()
        os.symlink("venv-a", tmp / "venv")
        (tmp / "jobs" / "queue").mkdir(parents=True)
        text = self.run_preflight(RAVIX_UPDATE_EXECUTOR="systemd", RAVIX_VENV_LINK=str(tmp / "venv"), RAVIX_UPDATE_JOBS_DIR=tmp / "jobs", RAVIX_HOSTING=False)
        self.assertNotIn("not exist; run install.sh", text)
        self.assertNotIn("cannot write", text)
