"""The file lock must work on Linux/macOS (fcntl) and Windows (msvcrt), and no module may need a POSIX-only module just to be imported."""
import ast
import errno
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase

from apps.core import filelock

POSIX_ONLY = {"fcntl", "pwd", "grp", "resource", "termios", "syslog", "crypt"}


class LockTests(SimpleTestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ravix-lock-")
        self.path = os.path.join(self.dir, "x.lock")
        self.addCleanup(__import__("shutil").rmtree, self.dir, True)

    def open(self):
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(lambda: self._close(fd))
        return fd

    @staticmethod
    def _close(fd):
        try:
            os.close(fd)
        except OSError:
            pass

    def test_a_second_holder_is_refused_until_the_first_lets_go(self):
        a, b = self.open(), self.open()
        filelock.try_lock(a)
        with self.assertRaises(BlockingIOError):
            filelock.try_lock(b)
        os.close(a)  # closing releases it (a crash does the same)
        filelock.try_lock(b)

    def test_unlock_releases(self):
        a, b = self.open(), self.open()
        filelock.try_lock(a)
        filelock.unlock(a)
        filelock.try_lock(b)

    def test_another_process_is_refused(self):
        fd = self.open()
        filelock.try_lock(fd)
        code = "import os,sys;sys.path.insert(0,os.getcwd());from apps.core import filelock;fd=os.open(sys.argv[1],os.O_RDWR);\ntry:\n filelock.try_lock(fd);print('got')\nexcept BlockingIOError:\n print('busy')"
        out = subprocess.run([sys.executable, "-I", "-c", f"import sys;sys.path.insert(0,{str(settings.BASE_DIR)!r});exec({code!r})", self.path], capture_output=True, text=True, cwd=str(settings.BASE_DIR), env={**os.environ, "DJANGO_SETTINGS_MODULE": ""}, timeout=60)
        self.assertEqual(out.stdout.strip(), "busy", out.stderr)

    def test_a_file_system_without_locks_raises_a_plain_oserror(self):
        fd = self.open()
        with mock.patch.object(filelock, "fcntl") as fake:
            fake.LOCK_EX, fake.LOCK_NB = 2, 4
            fake.flock.side_effect = OSError(errno.ENOLCK, "no locks")
            with self.assertRaises(OSError) as ctx:
                filelock.try_lock(fd)
        self.assertNotIsInstance(ctx.exception, BlockingIOError)


class WindowsPathTests(SimpleTestCase):
    """Windows is simulated: no fcntl, and an msvcrt that behaves as documented (the real one only exists there; CI runs the real thing on windows-latest)."""

    class FakeMsvcrt:
        LK_NBLCK, LK_UNLCK = 2, 0

        def __init__(self, error=None):
            self.calls, self.error = [], error

        def locking(self, fd, mode, nbytes):
            self.calls.append((mode, nbytes))
            if self.error and mode == self.LK_NBLCK:
                raise OSError(self.error, "x")

    def test_msvcrt_locks_one_byte_at_the_start_of_the_file(self):
        fake = self.FakeMsvcrt()
        fd = os.open(os.devnull, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        with mock.patch.object(filelock, "fcntl", None), mock.patch.object(filelock, "msvcrt", fake):
            filelock.try_lock(fd)
            filelock.unlock(fd)
        self.assertEqual(fake.calls, [(2, 1), (0, 1)])

    def test_a_lock_held_by_someone_else_is_reported_as_busy(self):
        for code in (errno.EACCES, errno.EDEADLK):
            fake = self.FakeMsvcrt(error=code)
            with mock.patch.object(filelock, "fcntl", None), mock.patch.object(filelock, "msvcrt", fake):
                with self.assertRaises(BlockingIOError):
                    filelock.try_lock(os.open(os.devnull, os.O_RDONLY))

    def test_another_error_is_not_mistaken_for_busy(self):
        fake = self.FakeMsvcrt(error=errno.EINVAL)
        with mock.patch.object(filelock, "fcntl", None), mock.patch.object(filelock, "msvcrt", fake):
            with self.assertRaises(OSError) as ctx:
                filelock.try_lock(os.open(os.devnull, os.O_RDONLY))
        self.assertNotIsInstance(ctx.exception, BlockingIOError)

    def test_no_locking_at_all_is_an_error_not_a_silent_success(self):
        with mock.patch.object(filelock, "fcntl", None), mock.patch.object(filelock, "msvcrt", None):
            self.assertFalse(filelock.supported())
            with self.assertRaises(OSError):
                filelock.try_lock(os.open(os.devnull, os.O_RDONLY))


class NoPosixOnlyImportTests(SimpleTestCase):
    def test_no_module_imports_a_posix_only_module_at_load_time(self):
        """A top-level ``import fcntl`` (outside a try block) is what stopped Ravix from starting on Windows."""
        offenders = []
        for path in Path(settings.BASE_DIR).glob("**/*.py"):
            rel = path.relative_to(settings.BASE_DIR).as_posix()
            if not rel.startswith(("apps/", "config/")) or "/tests/" in rel or "/migrations/" in rel:
                continue
            for node in ast.parse(path.read_text(encoding="utf-8")).body:  # top-level statements only; a Try block is the allowed guard
                names = [a.name.split(".")[0] for a in node.names] if isinstance(node, ast.Import) else [node.module.split(".")[0]] if isinstance(node, ast.ImportFrom) and node.module else []
                offenders += [f"{rel}: import {n}" for n in names if n in POSIX_ONLY]
        self.assertEqual(offenders, [])

    def test_the_application_starts_and_the_lock_users_load_without_posix_modules(self):
        code = textwrap.dedent("""
            import os, sys
            for name in ("fcntl", "pwd", "grp", "resource", "termios"):
                sys.modules[name] = None            # `import fcntl` now raises ImportError, as on Windows
            for attr in ("geteuid", "getegid", "getuid", "getgroups"):
                if hasattr(os, attr):
                    delattr(os, attr)
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
            import django
            django.setup()
            from apps.core import filelock, fsaccess
            from apps.core.management.commands import ravix_cron, ravix_preflight
            from apps.themes import installer
            from apps.updater import jobs
            assert fsaccess.process_identity() == ""
            print("started")
        """)
        env = {**os.environ, "PYTHONPATH": str(settings.BASE_DIR)}
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(settings.BASE_DIR), env=env, timeout=120)
        self.assertTrue(out.stdout.strip().splitlines()[-1:] and out.stdout.strip().splitlines()[-1].startswith("started"), out.stderr[-1500:])
