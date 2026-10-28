"""Shared hosting (cPanel): helpers, the WSGI wrapper, the cron command, the mail queue, the guest page cache, the protective .htaccess files,
the health checks and the two new settings pages."""
import io
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.content.models import Page, PublishStatus
from apps.core import hosthtaccess, hosting, mailbackend, pagecache, secretbox
from apps.core.models import CronRun, MailSettings, QueuedEmail, SiteSettings

APP_ROOT = Path(__file__).resolve().parents[3]


class TmpMixin:
    def tmp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)


# ------------------------------------------------------------------ helpers
class HostingHelperTests(TmpMixin, SimpleTestCase):
    def test_env_file_roundtrip_and_mode(self):
        path = self.tmp() / "sub" / "ravix.env"
        values = {"DJANGO_SECRET_KEY": "a b#c\"d'e\\f", "DATABASE_URL": "mysql://u:p%40ss@localhost:3306/db", "EMPTY": ""}
        hosting.write_env_file(values, path)
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        self.assertEqual(hosting.read_env_file(path), values)
        self.assertEqual([p.name for p in path.parent.iterdir()], ["ravix.env"])  # no temp file left behind

    def test_env_file_rejects_newlines_and_bad_names(self):
        for bad in ({"A": "x\ny"}, {"lower": "x"}, {"A B": "x"}):
            with self.assertRaises(ValueError):
                hosting.write_env_file(bad, self.tmp() / "e")

    def test_real_environment_wins_over_the_file(self):
        path = self.tmp() / "e"
        hosting.write_env_file({"RAVIX_TEST_ONLY_A": "file", "RAVIX_TEST_ONLY_B": "file"}, path)
        with mock.patch.dict(os.environ, {"RAVIX_ENV_FILE": str(path), "RAVIX_TEST_ONLY_A": "real"}):
            hosting.load_env_into_environ()
            self.assertEqual(os.environ["RAVIX_TEST_ONLY_A"], "real")
            self.assertEqual(os.environ["RAVIX_TEST_ONLY_B"], "file")
            os.environ.pop("RAVIX_TEST_ONLY_B")

    def test_marker_or_variable_turns_the_mode_on(self):
        root = self.tmp()
        with mock.patch.object(hosting, "APP_ROOT", root), mock.patch.dict(os.environ, {"RAVIX_HOSTING": ""}):
            self.assertFalse(hosting.wanted())
            (root / hosting.MARKER).write_text("x")
            self.assertTrue(hosting.wanted())
        with mock.patch.object(hosting, "APP_ROOT", self.tmp()), mock.patch.dict(os.environ, {"RAVIX_HOSTING": "shared"}):
            self.assertTrue(hosting.wanted())
            self.assertEqual(hosting.choose_settings("x"), "config.settings.hosting")

    def test_a_vps_checkout_never_selects_the_hosting_settings(self):
        self.assertFalse((APP_ROOT / hosting.MARKER).exists())

    def test_data_dir_is_a_sibling_of_the_code_folder(self):
        with mock.patch.dict(os.environ, {"RAVIX_DATA_DIR": ""}):
            self.assertEqual(hosting.data_dir(), APP_ROOT.parent / f"{APP_ROOT.name}-data")
            self.assertFalse(str(hosting.data_dir()).startswith(str(APP_ROOT) + os.sep))

    def test_restart_touches_restart_txt_and_the_startup_file(self):
        root = self.tmp()
        (root / "passenger_wsgi.py").write_text("")
        os.utime(root / "passenger_wsgi.py", (1, 1))
        with mock.patch.object(hosting, "APP_ROOT", root):
            self.assertTrue(hosting.restart_app())
        self.assertTrue((root / "tmp" / "restart.txt").exists())
        self.assertGreater((root / "passenger_wsgi.py").stat().st_mtime, 1000)

    def test_cron_command_shape(self):
        line = hosting.cron_command(Path("/home/u/ravix"))
        self.assertTrue(line.startswith("*/5 * * * * cd /home/u/ravix && "))
        self.assertIn("config.settings.hosting", line)
        self.assertTrue(line.endswith("manage.py ravix_cron >/dev/null 2>&1"))

    def test_minimum_python_is_3_11(self):
        self.assertEqual(hosting.MIN_PYTHON, (3, 11))


# ------------------------------------------------------------------ passenger_wsgi.py
class PassengerWsgiTests(TmpMixin, SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sys.path.insert(0, str(APP_ROOT))
        import passenger_wsgi

        cls.wsgi = passenger_wsgi

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(str(APP_ROOT))
        super().tearDownClass()

    def test_server_https_indicators_set_the_scheme(self):
        for key, value in (("HTTPS", "on"), ("REQUEST_SCHEME", "https"), ("SSL_PROTOCOL", "TLSv1.3")):
            env = self.wsgi._fix_environ({"wsgi.url_scheme": "http", key: value, "PATH_INFO": "/"}, "")
            self.assertEqual(env["wsgi.url_scheme"], "https", key)

    def test_a_forwarded_header_alone_is_not_trusted(self):
        env = self.wsgi._fix_environ({"wsgi.url_scheme": "http", "HTTP_X_FORWARDED_PROTO": "https", "PATH_INFO": "/"}, "")
        self.assertEqual(env["wsgi.url_scheme"], "http")

    def test_prefix_moves_from_path_to_script_name(self):
        env = self.wsgi._fix_environ({"SCRIPT_NAME": "", "PATH_INFO": "/site/blog/x/"}, "/site")
        self.assertEqual((env["SCRIPT_NAME"], env["PATH_INFO"]), ("/site", "/blog/x/"))
        env = self.wsgi._fix_environ({"SCRIPT_NAME": "", "PATH_INFO": "/site"}, "/site")
        self.assertEqual((env["SCRIPT_NAME"], env["PATH_INFO"]), ("/site", "/"))

    def test_prefix_already_handled_by_the_server_is_left_alone(self):
        env = self.wsgi._fix_environ({"SCRIPT_NAME": "/site", "PATH_INFO": "/blog/"}, "/site")
        self.assertEqual((env["SCRIPT_NAME"], env["PATH_INFO"]), ("/site", "/blog/"))

    def test_a_path_that_only_starts_with_the_prefix_text_is_not_cut(self):
        env = self.wsgi._fix_environ({"SCRIPT_NAME": "", "PATH_INFO": "/sitemap.xml"}, "/site")
        self.assertEqual((env["SCRIPT_NAME"], env["PATH_INFO"]), ("", "/sitemap.xml"))

    def test_startup_failure_gives_a_safe_503_and_a_log_file(self):
        data = self.tmp()
        with mock.patch.dict(os.environ, {"RAVIX_DATA_DIR": str(data)}):
            try:
                raise RuntimeError("secret-looking detail")
            except RuntimeError as exc:
                app = self.wsgi._startup_error_app(exc)
        seen = {}
        body = b"".join(app({}, lambda status, headers: seen.update(status=status, headers=dict(headers))))
        self.assertTrue(seen["status"].startswith("503"))
        self.assertNotIn(b"secret-looking detail", body)  # the reason goes to the private file, not to the visitor
        self.assertIn(b"startup-error.txt", body)
        log = data / "logs" / "startup-error.txt"
        self.assertIn("secret-looking detail", log.read_text())
        self.assertEqual(oct(log.stat().st_mode & 0o777), "0o600")

    def test_old_python_is_refused_with_a_plain_message(self):
        code = ("import sys; sys.version_info = (3, 10, 12, 'final', 0)\n"
                "import runpy; ns = runpy.run_path('passenger_wsgi.py')\n"
                "seen = {}\n"
                "body = b''.join(ns['application']({}, lambda s, h: seen.update(s=s)))\n"
                "print(seen['s'][:3], b'Python 3.11' in body or b'3.11' in body)\n")
        data = self.tmp()
        env = {**os.environ, "RAVIX_DATA_DIR": str(data), "DJANGO_SETTINGS_MODULE": "config.settings.development"}
        r = subprocess.run([sys.executable, "-c", code], cwd=APP_ROOT, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.stdout.strip(), "503 True", r.stderr[-400:])


# ------------------------------------------------------------------ ravix_cron
class FakeDb:
    name = "fake"

    def backup(self, dest):
        Path(dest).write_bytes(b"dump")


class CronTests(TmpMixin, TestCase):
    def setUp(self):
        patcher = mock.patch("apps.updater.backup.default_db_backend", return_value=FakeDb())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = self.tmp()
        for ctx in (override_settings(RAVIX_DATA_DIR=self.root, RAVIX_BACKUP_DIR=self.root / "backups", RAVIX_CLI_LANG="en"),):  # the asserts read the English text
            ctx.enable()
            self.addCleanup(ctx.disable)

    def run_cron(self, *args):
        out = io.StringIO()
        call_command("ravix_cron", *args, "--no-backup", stdout=out)
        return out.getvalue()

    def test_runs_the_jobs_and_records_the_run(self):
        out = self.run_cron("--no-keep-alive")
        run = CronRun.objects.get()
        self.assertTrue(run.ok)
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(set(run.summary), {"publish", "orders", "mail", "cleanup"})
        self.assertIn("finished", out)

    def test_publishes_a_due_scheduled_post(self):
        from apps.content.models import Post

        post = Post.objects.create(title="t", slug="t", body="b", status=PublishStatus.SCHEDULED, publish_at=timezone.now() - timedelta(minutes=1))
        self.run_cron("--no-keep-alive")
        post.refresh_from_db()
        self.assertEqual(post.status, PublishStatus.PUBLISHED)

    def test_overlapping_runs_do_not_overlap(self):
        from apps.core.management.commands import ravix_cron

        with ravix_cron.RunLock(ravix_cron.lock_path()):
            out = self.run_cron("--no-keep-alive")
        self.assertIn("did nothing", out)
        self.assertFalse(CronRun.objects.exists())
        self.run_cron("--no-keep-alive")  # the lock was released
        self.assertEqual(CronRun.objects.count(), 1)

    def test_one_failing_job_does_not_stop_the_others(self):
        with mock.patch("apps.core.management.commands.ravix_cron.Command.publish", side_effect=RuntimeError("x")):
            self.run_cron("--no-keep-alive")
        run = CronRun.objects.get()
        self.assertFalse(run.ok)
        self.assertEqual(run.summary["publish"], "failed: RuntimeError")
        self.assertEqual(run.summary["mail"], "ok")

    def test_keep_alive_opens_the_home_page(self):
        with override_settings(SITE_URL="https://example.com", RAVIX_KEEP_ALIVE=True), mock.patch("urllib.request.urlopen") as opened:
            self.run_cron()
        self.assertEqual(opened.call_args[0][0].full_url, "https://example.com/")
        self.assertEqual(CronRun.objects.get().summary["keep_alive"], "ok")

    def test_keep_alive_can_be_turned_off_in_the_panel_and_in_the_settings(self):
        with override_settings(SITE_URL="https://example.com"), mock.patch("urllib.request.urlopen") as opened:
            SiteSettings.objects.update_or_create(pk=1, defaults={"keep_alive": False})
            self.run_cron()
            self.assertFalse(opened.called)
            SiteSettings.objects.filter(pk=1).update(keep_alive=True)
            with override_settings(RAVIX_KEEP_ALIVE=False):
                self.run_cron()
            self.assertFalse(opened.called)

    def test_keep_alive_never_follows_a_non_http_address(self):
        with override_settings(SITE_URL="file:///etc/passwd"), mock.patch("urllib.request.urlopen") as opened:
            self.run_cron()
        self.assertFalse(opened.called)
        self.assertIn("skipped", CronRun.objects.get().summary["keep_alive"])

    def test_keep_alive_failure_is_recorded_not_raised(self):
        with override_settings(SITE_URL="https://example.com"), mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            self.run_cron()
        run = CronRun.objects.get()
        self.assertFalse(run.ok)
        self.assertEqual(run.summary["keep_alive"], "failed: OSError")

    def test_daily_backup_runs_once_a_day_and_stays_in_the_private_folder(self):
        out = io.StringIO()
        call_command("ravix_cron", "--no-keep-alive", stdout=out)
        folders = [p for p in (self.root / "backups").iterdir() if p.is_dir()]
        self.assertEqual(len(folders), 1)
        self.assertTrue((folders[0] / "done").exists())
        call_command("ravix_cron", "--no-keep-alive", stdout=out)
        self.assertEqual(len([p for p in (self.root / "backups").iterdir() if p.is_dir()]), 1)
        self.assertEqual(CronRun.objects.order_by("-pk").first().summary["backup"].split(":")[0], "skipped")

    def test_old_cron_rows_are_trimmed(self):
        for _i in range(70):
            CronRun.objects.create(started_at=timezone.now(), ok=True, summary={})
        self.run_cron("--no-keep-alive")
        self.assertLessEqual(CronRun.objects.count(), 62)

    def test_cleanup_restores_the_protective_files(self):
        media = self.root / "public_media"
        with override_settings(MEDIA_ROOT=media):
            self.run_cron("--no-keep-alive")
        self.assertTrue((media / ".htaccess").exists())


class HostBackupTests(TmpMixin, TestCase):
    def test_backup_holds_database_media_and_settings_and_is_private(self):
        from apps.core import hostbackup

        root, media, envf = self.tmp(), self.tmp() / "media", self.tmp() / "ravix.env"
        media.mkdir()
        (media / "a.txt").write_text("hi")
        envf.write_text("K=v\n")
        with override_settings(RAVIX_BACKUP_DIR=root / "b", MEDIA_ROOT=media, RAVIX_ENV_FILE=envf, RAVIX_BACKUP_KEEP=2), mock.patch("apps.updater.backup.default_db_backend", return_value=FakeDb()):
            self.assertTrue(hostbackup.due(24))
            info = hostbackup.create()
            folder = root / "b" / info["dir"]
            self.assertEqual({p.name for p in folder.iterdir()}, {"db.dump", "media.tar.gz", "ravix.env", "done"})
            self.assertEqual(oct(folder.stat().st_mode & 0o777), "0o700")
            self.assertEqual(oct((folder / "ravix.env").stat().st_mode & 0o777), "0o600")
            self.assertFalse(hostbackup.due(24))
            for _i in range(3):
                hostbackup.create()
            self.assertEqual(len(hostbackup.list_backups()), 2)  # only the newest N are kept

    def test_large_media_is_left_out_but_the_database_is_always_saved(self):
        from apps.core import hostbackup

        root, media = self.tmp(), self.tmp() / "media"
        media.mkdir()
        (media / "big.bin").write_bytes(b"x" * 2048)
        with override_settings(RAVIX_BACKUP_DIR=root, MEDIA_ROOT=media, RAVIX_BACKUP_MAX_MEDIA_MB=0), mock.patch("apps.updater.backup.default_db_backend", return_value=FakeDb()):
            info = hostbackup.create()
        self.assertFalse(info["media"])
        self.assertTrue((root / info["dir"] / "db.dump").exists())

    def test_an_empty_database_file_is_an_error_not_a_backup(self):
        from apps.core import hostbackup

        class Empty(FakeDb):
            def backup(self, dest):
                Path(dest).write_bytes(b"")

        root = self.tmp()
        with override_settings(RAVIX_BACKUP_DIR=root), mock.patch("apps.updater.backup.default_db_backend", return_value=Empty()):
            with self.assertRaises(RuntimeError):
                hostbackup.create()
        self.assertEqual(list(root.iterdir()), [])


# ------------------------------------------------------------------ mail
class MailTests(TestCase):
    def setUp(self):
        for ctx in (override_settings(EMAIL_BACKEND="apps.core.mailbackend.QueuedEmailBackend", EMAIL_HOST="smtp.invalid", DEFAULT_FROM_EMAIL="site@example.com"),):
            ctx.enable()
            self.addCleanup(ctx.disable)

    def message(self):
        from django.core.mail import EmailMessage

        return EmailMessage("subject", "body", "site@example.com", ["to@example.com"])

    def test_a_failed_send_is_queued_and_does_not_raise(self):
        with mock.patch("django.core.mail.backends.smtp.EmailBackend.send_messages", side_effect=OSError("down")):
            self.assertEqual(self.message().send(), 1)
        item = QueuedEmail.objects.get()
        self.assertEqual(item.recipients, ["to@example.com"])
        self.assertEqual(item.attempts, 1)
        self.assertGreater(item.next_attempt, timezone.now())
        self.assertIn(b"subject", bytes(item.raw))

    def test_retry_sends_and_removes_a_due_message(self):
        with mock.patch("django.core.mail.backends.smtp.EmailBackend.send_messages", side_effect=OSError("down")):
            self.message().send()
        QueuedEmail.objects.update(next_attempt=timezone.now() - timedelta(seconds=1))
        with mock.patch.object(mailbackend.DirectEmailBackend, "send_raw") as sent:
            self.assertEqual(mailbackend.send_queued(), {"sent": 1, "retry": 0, "dropped": 0})
        self.assertEqual(sent.call_args[0][1], ["to@example.com"])
        self.assertFalse(QueuedEmail.objects.exists())

    def test_retry_backs_off_then_gives_up(self):
        QueuedEmail.objects.create(raw=b"x", from_email="a@b.c", recipients=["d@e.f"], attempts=1, next_attempt=timezone.now() - timedelta(seconds=1))
        with mock.patch.object(mailbackend.DirectEmailBackend, "send_raw", side_effect=OSError("down")):
            self.assertEqual(mailbackend.send_queued()["retry"], 1)
            item = QueuedEmail.objects.get()
            self.assertEqual(item.attempts, 2)
            self.assertGreater(item.next_attempt, timezone.now() + timedelta(minutes=10))
            QueuedEmail.objects.update(attempts=len(mailbackend.RETRY_DELAYS) + 1, next_attempt=timezone.now() - timedelta(seconds=1))
            self.assertEqual(mailbackend.send_queued()["dropped"], 1)
        self.assertFalse(QueuedEmail.objects.exists())

    def test_messages_not_yet_due_are_left_alone(self):
        QueuedEmail.objects.create(raw=b"x", from_email="a@b.c", recipients=["d@e.f"], attempts=1, next_attempt=timezone.now() + timedelta(hours=1))
        with mock.patch.object(mailbackend.DirectEmailBackend, "send_raw") as sent:
            self.assertEqual(mailbackend.send_queued(), {"sent": 0, "retry": 0, "dropped": 0})
        self.assertFalse(sent.called)

    def test_panel_smtp_settings_are_used_and_the_password_is_stored_encrypted(self):
        row = MailSettings.load()
        row.enabled, row.host, row.port, row.security, row.username, row.from_email = True, "smtp.panel.test", 465, "ssl", "u", "panel@example.com"
        row.password_encrypted = secretbox.encrypt("hunter2-secret")
        row.save()
        self.assertNotIn("hunter2", MailSettings.objects.get().password_encrypted)
        conf = mailbackend.panel_settings()
        self.assertEqual((conf["host"], conf["port"], conf["password"], conf["use_ssl"], conf["use_tls"]), ("smtp.panel.test", 465, "hunter2-secret", True, False))
        backend = mailbackend.DirectEmailBackend()
        self.assertEqual((backend.host, backend.port, backend.username, backend.password), ("smtp.panel.test", 465, "u", "hunter2-secret"))
        msg = self.message()
        msg.from_email = "site@example.com"
        with mock.patch("django.core.mail.backends.smtp.EmailBackend.send_messages", return_value=1):
            backend.send_messages([msg])
        self.assertEqual(msg.from_email, "panel@example.com")

    def test_panel_settings_off_or_incomplete_fall_back_to_the_server(self):
        row = MailSettings.load()
        row.enabled, row.host = False, "smtp.panel.test"
        row.save()
        self.assertIsNone(mailbackend.panel_settings())
        self.assertEqual(mailbackend.DirectEmailBackend().host, "smtp.invalid")

    def test_secretbox_roundtrip_and_tamper(self):
        token = secretbox.encrypt("abc")
        self.assertEqual(secretbox.decrypt(token), "abc")
        self.assertEqual(secretbox.decrypt(token[:-4] + "AAAA"), "")
        self.assertEqual(secretbox.encrypt(""), "")

    def test_send_test_raises_so_the_owner_sees_the_error(self):
        with mock.patch("django.core.mail.backends.smtp.EmailBackend.send_messages", side_effect=OSError("down")):
            with self.assertRaises(OSError):
                mailbackend.send_test("to@example.com")
        self.assertFalse(QueuedEmail.objects.exists())  # not hidden in the retry queue


# ------------------------------------------------------------------ page cache
@override_settings(RAVIX_PAGE_CACHE="on", DEBUG=False, ALLOWED_HOSTS=["*"])
class PageCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        SiteSettings.objects.update_or_create(pk=1, defaults={"language": "en", "page_cache": "auto", "content_per_language": False})
        self.page = Page.objects.create(title="About", slug="about", body="hello", status=PublishStatus.PUBLISHED, language="en")
        self.client = Client()
        self.client.cookies["ravix_lang"] = "en"
        self.url = "/en/about/"
        Client().get(self.url)  # the very first request creates default rows (a content change); start from a settled cache
        cache.clear()

    def cache_state(self, response):
        return response.headers.get("X-Ravix-Cache")

    def test_guest_pages_are_stored_then_served_from_the_cache(self):
        first = self.client.get(self.url)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.cache_state(first), "MISS")
        second = self.client.get(self.url)
        self.assertEqual(self.cache_state(second), "HIT")
        self.assertEqual(first.content, second.content)
        self.assertNotIn("Set-Cookie", second.headers)

    def test_a_cache_hit_needs_no_database_query(self):
        self.client.get(self.url)
        with self.assertNumQueries(0):
            self.assertEqual(self.cache_state(self.client.get(self.url)), "HIT")

    def test_a_cache_hit_is_fast(self):
        self.client.get(self.url)
        best = min(self._timed() for _i in range(5))
        self.assertLess(best, 0.2)

    def _timed(self):
        started = time.perf_counter()
        self.client.get(self.url)
        return time.perf_counter() - started

    def test_conditional_requests_get_304(self):
        self.client.get(self.url)
        etag = self.client.get(self.url).headers.get("ETag")
        self.assertTrue(etag)
        r = self.client.get(self.url, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(r.status_code, 304)

    def test_signed_in_users_are_never_served_or_stored(self):
        self.client.get(self.url)  # a guest filled the cache
        user = make_user("reader")
        self.client.force_login(user)
        r = self.client.get(self.url)
        self.assertEqual(self.cache_state(r), "BYPASS")
        self.assertEqual(r.status_code, 200)
        other = Client()
        other.cookies["ravix_lang"] = "en"
        other.force_login(user)
        other.get("/en/")
        self.assertNotEqual(self.cache_state(Client().get("/en/")), "HIT")  # the signed-in page was not stored for guests

    def test_a_session_or_messages_cookie_bypasses_the_cache(self):
        self.client.get(self.url)
        for name in ("sessionid", "messages"):
            c = Client()
            c.cookies["ravix_lang"] = "en"
            c.cookies[name] = "x"
            self.assertEqual(self.cache_state(c.get(self.url)), "BYPASS", name)

    def test_authorization_header_bypasses(self):
        self.client.get(self.url)
        self.assertEqual(self.cache_state(self.client.get(self.url, HTTP_AUTHORIZATION="Bearer x")), "BYPASS")

    def test_pages_with_a_csrf_token_are_never_stored(self):
        for path in ("/accounts/login/", "/accounts/register/"):
            r = self.client.get(path)
            self.assertNotEqual(self.cache_state(r), "HIT", path)
            self.assertNotEqual(self.cache_state(self.client.get(path)), "HIT", path)

    def test_the_panel_the_api_and_the_shop_are_not_public_views(self):
        for path in ("/panel/", "/api/", "/shop/cart/", "/cart/", "/shop/checkout/"):
            r = self.client.get(path)
            self.assertNotEqual(self.cache_state(self.client.get(path)), "HIT", path)

    def test_only_plain_listing_queries_are_cached(self):
        self.client.get("/en/?q=abc")
        self.assertEqual(self.cache_state(self.client.get("/en/?q=abc")), "BYPASS")
        self.client.get("/en/?page=2")
        self.assertNotEqual(self.cache_state(self.client.get("/en/?utm_source=x")), "HIT")

    def test_post_requests_are_untouched(self):
        self.client.get(self.url)
        r = self.client.post(self.url)
        self.assertIsNone(self.cache_state(r))

    def test_editing_content_clears_the_cache_at_once(self):
        self.client.get(self.url)
        self.assertEqual(self.cache_state(self.client.get(self.url)), "HIT")
        self.page.body = "changed text"
        self.page.save()
        r = self.client.get(self.url)
        self.assertEqual(self.cache_state(r), "MISS")
        self.assertIn("changed text", r.content.decode())

    def test_deleting_and_unpublishing_clear_the_cache(self):
        self.client.get(self.url)
        Page.objects.filter(pk=self.page.pk).update(status=PublishStatus.DRAFT)
        self.page.refresh_from_db()
        self.page.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_site_settings_changes_clear_the_cache(self):
        self.client.get("/en/")
        self.assertEqual(self.cache_state(self.client.get("/en/")), "HIT")
        s = SiteSettings.load()
        s.site_name = "Another name"
        s.save()
        r = self.client.get("/en/")
        self.assertEqual(self.cache_state(r), "MISS")
        self.assertIn("Another name", r.content.decode())

    def test_unrelated_writes_do_not_clear_it(self):
        self.client.get(self.url)
        CronRun.objects.create(started_at=timezone.now(), ok=True, summary={})
        QueuedEmail.objects.create(raw=b"x", from_email="a@b.c", recipients=[], next_attempt=timezone.now())
        self.assertEqual(self.cache_state(self.client.get(self.url)), "HIT")

    def test_languages_have_separate_entries(self):
        a = self.client.get("/en/")
        c = Client()
        c.cookies["ravix_lang"] = "fa"
        c.get("/fa/")
        self.assertEqual(self.cache_state(self.client.get("/en/")), "HIT")
        self.assertIn('lang="en"', self.client.get("/en/").content.decode()[:300])
        self.assertTrue(a.content)

    def test_switch_in_the_panel_and_in_the_settings(self):
        SiteSettings.objects.filter(pk=1).update(page_cache="off")
        cache.delete(pagecache.CFG_KEY)
        self.client.get(self.url)
        self.assertIsNone(self.cache_state(self.client.get(self.url)))
        SiteSettings.objects.filter(pk=1).update(page_cache="on")
        cache.delete(pagecache.CFG_KEY)
        self.client.get(self.url)
        self.assertEqual(self.cache_state(self.client.get(self.url)), "HIT")
        with override_settings(RAVIX_PAGE_CACHE="off"):
            SiteSettings.objects.filter(pk=1).update(page_cache="auto")
            cache.delete(pagecache.CFG_KEY)
            self.assertIsNone(self.cache_state(self.client.get(self.url)))

    @override_settings(RAVIX_PAGE_CACHE="off")
    def test_a_vps_default_is_off_but_the_panel_can_turn_it_on(self):
        cache.delete(pagecache.CFG_KEY)
        self.client.get(self.url)
        self.assertIsNone(self.cache_state(self.client.get(self.url)))
        SiteSettings.objects.filter(pk=1).update(page_cache="on")
        cache.delete(pagecache.CFG_KEY)
        self.client.get(self.url)
        self.assertEqual(self.cache_state(self.client.get(self.url)), "HIT")

    @override_settings(DEBUG=True)
    def test_never_active_in_debug(self):
        self.client.get(self.url)
        self.assertIsNone(self.cache_state(self.client.get(self.url)))

    def test_setup_mode_never_uses_the_cache(self):
        with override_settings(SETUP_MODE=True):
            self.assertIsNone(self.cache_state(self.client.get(self.url)))


# ------------------------------------------------------------------ .htaccess files
class HtaccessTests(TmpMixin, SimpleTestCase):
    def test_media_file_blocks_scripts_listing_and_sniffing(self):
        text = hosthtaccess.MEDIA
        self.assertIn("Options -Indexes -ExecCGI", text)
        for ext in ("php", "py", "cgi", "sh", "htaccess"):
            self.assertIn(ext, text)
        self.assertIn("X-Content-Type-Options", text)
        self.assertIn("sandbox", text)
        self.assertIn("AddType text/plain", text)

    def test_only_directives_both_apache_and_litespeed_read(self):
        allowed = {"Options", "FilesMatch", "IfModule", "Require", "Order", "Deny", "RemoveHandler", "RemoveType", "AddType", "Header"}
        for text in (hosthtaccess.MEDIA, hosthtaccess.APP_ROOT, hosthtaccess.DATA):
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("</"):
                    continue
                self.assertIn(line.lstrip("<").split()[0].rstrip(">"), allowed, line)
            for forbidden in ("php_value", "php_flag", "SetHandler", "AddHandler", "Satisfy", "ExpiresActive"):
                self.assertNotIn(forbidden, text)

    def test_every_deny_has_the_apache_2_2_fallback(self):
        for text in (hosthtaccess.APP_ROOT, hosthtaccess.DATA, hosthtaccess.MEDIA):
            self.assertIn("Require all denied", text)
            self.assertIn("Deny from all", text)

    def test_the_code_folder_file_denies_secrets_and_databases(self):
        for ext in ("env", "sqlite3", "sql", "log", "key", "zip", "py", "bak"):
            self.assertIn(ext, hosthtaccess.APP_ROOT)

    def test_ensure_writes_files_and_respects_the_owners_own_file(self):
        root = self.tmp()
        media, data = root / "media", root / "data"
        out = hosthtaccess.ensure(media_root=media, data_dir=data, app_root=root / "nothing")
        self.assertEqual((out["media"], out["data"]), (True, True))
        self.assertTrue((media / ".htaccess").read_text().startswith(hosthtaccess.MARK))
        (media / ".htaccess").write_text("# my own rules\n")
        hosthtaccess.ensure(media_root=media, data_dir=data, app_root=root / "nothing")
        self.assertEqual((media / ".htaccess").read_text(), "# my own rules\n")

    def test_ensure_refreshes_a_managed_file(self):
        root = self.tmp()
        (root / ".htaccess").write_text(hosthtaccess.MARK + "\nold\n")
        hosthtaccess.ensure(media_root=root, data_dir=root / "d", app_root=root / "n")
        self.assertEqual((root / ".htaccess").read_text(), hosthtaccess.MEDIA)


# ------------------------------------------------------------------ settings in a real, separate process
class HostingSettingsProcessTests(TmpMixin, SimpleTestCase):
    def settings_in_process(self, env_values, expr, extra_env=None):
        data = self.tmp()
        hosting.write_env_file(env_values, data / "ravix.env")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "RAVIX_", "DATABASE_URL", "REDIS"))}
        env.update(RAVIX_DATA_DIR=str(data), DJANGO_SETTINGS_MODULE="config.settings.hosting", PYTHONPATH=str(APP_ROOT), **(extra_env or {}))
        code = "import django.conf as c; s = c.settings; print(" + expr + ")"
        r = subprocess.run([sys.executable, "-c", code], cwd=APP_ROOT, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-600:])
        return r.stdout.strip(), data

    BASE = {"DJANGO_SECRET_KEY": "k" * 50, "DJANGO_ALLOWED_HOSTS": "example.com", "RAVIX_SITE_URL": "https://example.com"}

    def test_the_sites_own_origin_is_trusted_for_forms(self):
        out, _ = self.settings_in_process(self.BASE, "s.CSRF_TRUSTED_ORIGINS")
        self.assertEqual(out, "['https://example.com', 'https://www.example.com']")

    def test_security_defaults_for_an_https_site(self):
        out, _ = self.settings_in_process(self.BASE, "s.DEBUG, s.SETUP_MODE, s.SECURE_SSL_REDIRECT, s.SESSION_COOKIE_SECURE, s.CSRF_COOKIE_SECURE, s.SECURE_HSTS_SECONDS > 0, s.SECURE_PROXY_SSL_HEADER")
        self.assertEqual(out, "False False True True True True None")

    def test_an_http_only_site_does_not_force_https(self):
        out, _ = self.settings_in_process({**self.BASE, "RAVIX_SITE_URL": "http://example.com"}, "s.SECURE_SSL_REDIRECT, s.SESSION_COOKIE_SECURE, s.SECURE_HSTS_SECONDS")
        self.assertEqual(out, "False False 0")

    def test_debug_cannot_be_turned_on_from_the_environment(self):
        out, _ = self.settings_in_process(self.BASE, "s.DEBUG", extra_env={"DJANGO_DEBUG": "true", "DEBUG": "1"})
        self.assertEqual(out, "False")

    def test_forwarded_proto_is_trusted_only_behind_a_declared_proxy(self):
        out, _ = self.settings_in_process({**self.BASE, "RAVIX_BEHIND_PROXY": "true"}, "s.SECURE_PROXY_SSL_HEADER")
        self.assertEqual(out, "('HTTP_X_FORWARDED_PROTO', 'https')")

    def test_private_files_default_outside_the_code_folder_and_public_html(self):
        out, data = self.settings_in_process(
            self.BASE, "[str(s.DATABASES['default']['NAME']), str(s.CACHES['default']['LOCATION']), str(s.RAVIX_BACKUP_DIR), str(s.RAVIX_UPDATE_BACKUP_DIR), str(s.RAVIX_PLUGIN_DATA_DIR), str(s.SHOP_PRIVATE_ROOT), str(s.RAVIX_INSTALL_TOKEN_FILE)]",
            extra_env={"RAVIX_PUBLIC_HTML": "/nonexistent/public_html"})
        paths = eval(out)  # noqa: S307 - our own subprocess output
        for p in paths:
            self.assertTrue(Path(p).is_relative_to(data), p)
            self.assertFalse(Path(p).is_relative_to(APP_ROOT), p)
        self.assertTrue(paths[0].endswith("ravix.sqlite3"))

    def test_a_sqlite_path_inside_the_code_folder_is_moved_to_the_data_folder(self):
        out, data = self.settings_in_process({**self.BASE, "DATABASE_URL": "sqlite:///db.sqlite3"}, "s.DATABASES['default']['NAME']")
        self.assertEqual(Path(out), data / "db" / "ravix.sqlite3")

    def test_media_goes_under_public_html_and_static_comes_from_whitenoise(self):
        pub = self.tmp()
        out, _ = self.settings_in_process(self.BASE, "s.MEDIA_ROOT, s.STORAGES['staticfiles']['BACKEND'].rsplit('.', 1)[-1], 'whitenoise.middleware.WhiteNoiseMiddleware' in s.MIDDLEWARE",
                                          extra_env={"RAVIX_PUBLIC_HTML": str(pub)})
        self.assertEqual(out, f"{pub / 'media'} RavixStaticFilesStorage True")

    def test_sub_folder_install_sets_the_script_name(self):
        out, _ = self.settings_in_process({**self.BASE, "RAVIX_SCRIPT_NAME": "site"}, "s.FORCE_SCRIPT_NAME")
        self.assertEqual(out, "/site")

    def test_file_cache_by_default_and_redis_when_present(self):
        out, _ = self.settings_in_process(self.BASE, "s.CACHES['default']['BACKEND'].rsplit('.', 1)[-1]")
        self.assertEqual(out, "SafeFileBasedCache")
        out, _ = self.settings_in_process({**self.BASE, "REDIS_URL": "redis://localhost:6379/1"}, "s.CACHES['default']['BACKEND'].rsplit('.', 1)[-1]")
        self.assertEqual(out, "RedisCache")

    def test_mysql_url_uses_utf8mb4_strict_and_decodes_the_password(self):
        out, _ = self.settings_in_process({**self.BASE, "DATABASE_URL": "mysql://u%40x:p%3Aw%2F%40@db.local:3307/my%20db"},
                                          "[s.DATABASES['default'][k] for k in ('ENGINE', 'NAME', 'USER', 'PASSWORD', 'HOST', 'PORT')], s.DATABASES['default']['OPTIONS']")
        self.assertIn("django.db.backends.mysql", out)
        self.assertIn("'u@x'", out)
        self.assertIn("'p:w/@'", out)
        self.assertIn("'my db'", out)
        self.assertIn("'charset': 'utf8mb4'", out)
        self.assertIn("STRICT_TRANS_TABLES", out)

    def test_missing_secret_key_or_hosts_stop_the_site(self):
        for values in ({"DJANGO_SECRET_KEY": "short", "DJANGO_ALLOWED_HOSTS": "example.com"}, {"DJANGO_SECRET_KEY": "k" * 50}):
            data = self.tmp()
            hosting.write_env_file(values, data / "ravix.env")
            env = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "RAVIX_", "DATABASE_URL"))}
            env.update(RAVIX_DATA_DIR=str(data), DJANGO_SETTINGS_MODULE="config.settings.hosting", PYTHONPATH=str(APP_ROOT))
            r = subprocess.run([sys.executable, "-c", "import django.conf as c; c.settings.DEBUG"], cwd=APP_ROOT, env=env, capture_output=True, text=True, timeout=60)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("ImproperlyConfigured", r.stderr)

    def test_the_data_folder_is_not_where_the_code_is(self):
        self.assertNotEqual(hosting.data_dir().resolve(), APP_ROOT.resolve())

    def test_no_private_path_is_under_static_root_or_media_url_space(self):
        out, data = self.settings_in_process(self.BASE, "str(s.STATIC_ROOT), s.STATIC_URL, s.MEDIA_URL", extra_env={"RAVIX_PUBLIC_HTML": str(self.tmp())})
        static_root = Path(out.split()[0])
        self.assertFalse(data.is_relative_to(static_root))
        self.assertFalse((data / "ravix.env").is_relative_to(static_root))


# ------------------------------------------------------------------ health page and the two settings pages
class HostingHealthTests(TestCase):
    def setUp(self):
        cache.clear()
        for ctx in (override_settings(RAVIX_HOSTING=True),):
            ctx.enable()
            self.addCleanup(ctx.disable)

    def test_cron_never_ran_is_critical_with_the_exact_line(self):
        from apps.core import sitehealth

        [check] = sitehealth.check_cron()
        self.assertEqual(check.status, sitehealth.CRITICAL)
        self.assertIn("manage.py ravix_cron", check.fix)

    def test_cron_statuses_by_age(self):
        from apps.core import sitehealth

        now = timezone.now()
        for minutes, expected in ((3, sitehealth.GOOD), (45, sitehealth.RECOMMENDED), (60 * 8, sitehealth.CRITICAL)):
            CronRun.objects.all().delete()
            CronRun.objects.create(started_at=now - timedelta(minutes=minutes), ok=True, summary={})
            self.assertEqual(sitehealth.check_cron(now)[0].status, expected, minutes)

    def test_a_failed_last_run_is_flagged(self):
        from apps.core import sitehealth

        CronRun.objects.create(started_at=timezone.now(), ok=False, summary={"mail": "failed: OSError"})
        self.assertEqual(sitehealth.check_cron()[0].status, sitehealth.RECOMMENDED)

    def test_host_group_reports_cache_queue_database_python(self):
        from apps.core import sitehealth

        with override_settings(RAVIX_PAGE_CACHE="on"):
            checks = {c.key: c for c in sitehealth.check_hosting()}
        self.assertEqual(checks["host_cache"].status, sitehealth.GOOD)
        self.assertEqual(checks["host_python"].status, sitehealth.GOOD)
        self.assertEqual(checks["host_mail_queue"].status, sitehealth.GOOD)
        for i in range(25):
            QueuedEmail.objects.create(raw=b"x", from_email="a@b.c", recipients=[], next_attempt=timezone.now())
        self.assertEqual({c.key: c for c in sitehealth.check_hosting()}["host_mail_queue"].status, sitehealth.RECOMMENDED)

    def test_backup_check_reads_the_hosting_backup_folder(self):
        from apps.core import hostbackup, sitehealth

        with tempfile.TemporaryDirectory() as tmp, override_settings(RAVIX_BACKUP_DIR=Path(tmp)):
            self.assertEqual(sitehealth.check_backup()[0].status, sitehealth.RECOMMENDED)
            folder = Path(tmp) / "20260101-000000"
            folder.mkdir()
            (folder / "done").write_text("")
            self.assertTrue(hostbackup.latest_backup_time())
            self.assertEqual(sitehealth.check_backup(now=hostbackup.latest_backup_time() + timedelta(hours=2))[0].status, sitehealth.GOOD)
            self.assertEqual(sitehealth.check_backup(now=hostbackup.latest_backup_time() + timedelta(days=10))[0].status, sitehealth.CRITICAL)

    def test_no_systemd_checks_in_hosting_mode_and_the_page_renders(self):
        admin = make_user("boss", role=Role.SUPER_ADMIN)
        c = Client()
        c.force_login(admin)
        r = c.get(reverse("core:site_health"))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "ravix-backup.timer")


class SettingsPagesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = make_user("boss", role=Role.SUPER_ADMIN)
        self.editor = make_user("ed", role=Role.AUTHOR)
        self.client.force_login(self.admin)
        self.client.cookies["ravix_lang"] = "en"

    def test_speed_page_saves_and_clears_the_cache(self):
        r = self.client.get(reverse("core:speed_settings"))
        self.assertContains(r, "Page cache")
        pagecache.clear()
        before = cache.get(pagecache.GEN_KEY)
        r = self.client.post(reverse("core:speed_settings"), {"page_cache": "on", "keep_alive": ""})
        self.assertEqual(r.status_code, 302)
        site = SiteSettings.load()
        self.assertEqual((site.page_cache, site.keep_alive), ("on", False))
        self.assertNotEqual(cache.get(pagecache.GEN_KEY), before)

    def test_speed_page_rejects_a_bad_choice_and_other_roles(self):
        self.assertEqual(self.client.post(reverse("core:speed_settings"), {"page_cache": "maybe"}).status_code, 200)
        self.client.force_login(self.editor)
        self.assertEqual(self.client.get(reverse("core:speed_settings")).status_code, 403)

    @override_settings(EMAIL_BACKEND="apps.core.mailbackend.QueuedEmailBackend")
    def test_email_page_saves_encrypts_and_never_shows_the_password(self):
        url = reverse("core:email_settings")
        data = {"enabled": "on", "host": "smtp.example.com", "port": "587", "security": "starttls", "username": "me", "from_email": "me@example.com", "password": "s3cr3t-pw"}
        r = self.client.post(url, data)
        self.assertEqual(r.status_code, 302)
        row = MailSettings.objects.get()
        self.assertTrue(row.enabled)
        self.assertEqual(secretbox.decrypt(row.password_encrypted), "s3cr3t-pw")
        page = self.client.get(url)
        self.assertNotContains(page, "s3cr3t-pw")
        self.assertNotContains(page, row.password_encrypted)
        # an empty password keeps the saved one; the box removes it
        self.client.post(url, {**data, "password": ""})
        self.assertEqual(secretbox.decrypt(MailSettings.objects.get().password_encrypted), "s3cr3t-pw")
        self.client.post(url, {**data, "password": "", "clear_password": "on"})
        self.assertEqual(MailSettings.objects.get().password_encrypted, "")

    @override_settings(EMAIL_BACKEND="apps.core.mailbackend.QueuedEmailBackend")
    def test_email_page_needs_a_host_when_enabled(self):
        r = self.client.post(reverse("core:email_settings"), {"enabled": "on", "host": "", "port": "587", "security": "starttls"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(MailSettings.load().enabled)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend")
    def test_email_page_is_absent_when_the_site_does_not_use_ravix_mail(self):
        self.assertEqual(self.client.get(reverse("core:email_settings")).status_code, 404)

    @override_settings(EMAIL_BACKEND="apps.core.mailbackend.QueuedEmailBackend", EMAIL_HOST="smtp.invalid")
    def test_test_email_shows_the_error_instead_of_queueing(self):
        with mock.patch("django.core.mail.backends.smtp.EmailBackend.send_messages", side_effect=OSError("down")):
            r = self.client.post(reverse("core:site_health_test_email"), {"back": "email"}, follow=True)
        self.assertContains(r, "Sending failed")
        self.assertEqual(r.redirect_chain[-1][0], reverse("core:email_settings"))
        self.assertFalse(QueuedEmail.objects.exists())

    @override_settings(EMAIL_BACKEND="apps.core.mailbackend.QueuedEmailBackend", EMAIL_HOST="smtp.invalid")
    def test_test_email_success_message(self):
        with mock.patch("django.core.mail.backends.smtp.EmailBackend.send_messages", return_value=1):
            r = self.client.post(reverse("core:site_health_test_email"), follow=True)
        self.assertContains(r, "handed to the mail server")
        self.assertEqual(r.redirect_chain[-1][0], reverse("core:site_health"))
