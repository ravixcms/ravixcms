"""Performance defaults and install-script behaviour: nginx template, Gunicorn workers, HTTP/2, install token.

* the nginx template and proxy include are syntax-checked with the real ``nginx -t`` when nginx is installed;
* the Gunicorn worker rule is checked on the pure function and through the real config file;
* the bash helpers of ``install.sh`` are cut out of the script and run for real on temporary files (and, for the "is the site
  installed?" question, against a real migrated SQLite database).
"""
import os
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest import mock, skipUnless

from django.conf import settings
from django.contrib.staticfiles.storage import ManifestStaticFilesStorage
from django.core.files.base import ContentFile
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)
SCRIPT = (ROOT / "install.sh").read_text(encoding="utf-8")
NGINX = (ROOT / "deploy" / "nginx-ravix.conf").read_text(encoding="utf-8")
PROXY = (ROOT / "deploy" / "ravix-proxy.conf").read_text(encoding="utf-8")
GUNICORN = ROOT / "deploy" / "gunicorn.conf.py"


def bash_function(name: str) -> str:
    """The source of one function of install.sh (from ``name() {`` to its closing brace at column 0 or on the same line)."""
    start = SCRIPT.index(f"\n{name}() {{") + 1
    line_end = SCRIPT.index("\n", start)
    first = SCRIPT[start:line_end]
    if first.rstrip().endswith("}") and first.count("{") == first.count("}"):
        return first
    end = SCRIPT.index("\n}\n", start) + 3
    return SCRIPT[start:end]


def run_bash(code: str, **env) -> subprocess.CompletedProcess:
    full = {"PATH": os.environ.get("PATH", ""), "LC_ALL": "C.UTF-8", **env}
    return subprocess.run(["bash", "-c", "set -uo pipefail\n" + textwrap.dedent(code)], capture_output=True, text=True, env=full, timeout=120)


def free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def nginx_test(site_text: str, workdir: str) -> subprocess.CompletedProcess:
    """``nginx -t`` on ``site_text`` as an unprivileged user without IPv6: [::] listens are dropped (install.sh does the same on such a
    system) and the privileged ports 80/443 are replaced by free ones."""
    site_text = "\n".join(line for line in site_text.splitlines() if not re.match(r"\s*listen\s+\[::\]", line))
    site_text = re.sub(r"listen 80;", f"listen 127.0.0.1:{free_port()};", site_text)
    site_text = re.sub(r"listen 443 ssl", f"listen 127.0.0.1:{free_port()} ssl", site_text)
    site = Path(workdir, "site.conf")
    site.write_text(site_text)
    main = Path(workdir, "nginx.conf")
    main.write_text(f"pid {workdir}/nginx.pid;\nerror_log {workdir}/error.log;\nevents {{}}\nhttp {{\n  access_log off;\n"
                    + "".join(f"  {k}_temp_path {workdir}/{k};\n" for k in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi"))
                    + f"  include {site};\n}}\n")
    return subprocess.run(["nginx", "-t", "-c", str(main), "-p", workdir], capture_output=True, text=True)


# --------------------------------------------------------------------------------------------------------------------------- nginx
class NginxTemplateTests(SimpleTestCase):
    def test_gzip_is_on_for_text_and_varies_on_accept_encoding(self):
        for directive in ("gzip on;", "gzip_vary on;", "gzip_comp_level 5;", "gzip_proxied any;"):
            self.assertIn(directive, NGINX)
        types = re.search(r"gzip_types([^;]+);", NGINX).group(1).split()
        for mime in ("text/css", "application/javascript", "application/json", "image/svg+xml", "text/plain", "application/xml"):
            self.assertIn(mime, types)
        for compressed in ("image/png", "image/jpeg", "image/webp", "font/woff2"):
            self.assertNotIn(compressed, types)  # already compressed: gzip only costs CPU

    def test_keepalive_to_the_browser_and_to_gunicorn(self):
        self.assertRegex(NGINX, r"upstream ravix_app \{[^}]*keepalive 16;")
        self.assertIn("keepalive_timeout 65s;", NGINX)
        self.assertIn("proxy_http_version 1.1;", PROXY)
        self.assertRegex(PROXY, r'proxy_set_header Connection "";')  # without it the upstream connection is closed after every request

    def test_gunicorn_keeps_connections_longer_than_nginx_does(self):
        nginx_idle = int(re.search(r"upstream ravix_app \{[^}]*keepalive_timeout (\d+)s;", NGINX).group(1))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GUNICORN_KEEPALIVE", None)
            config = runpy.run_path(str(GUNICORN))
        self.assertGreater(config["keepalive"], nginx_idle)  # nginx closes first: no request ever lands on a connection Gunicorn just dropped

    def test_only_hashed_static_names_are_immutable(self):
        pattern = re.search(r'"~(\\\.\[0-9a-f\]\{12\}[^"]+)"\s+"public, max-age=31536000, immutable"', NGINX)
        self.assertIsNotNone(pattern)
        regex = re.compile(pattern.group(1).replace("\\.", r"\."))
        self.assertIn('default                                    "public, max-age=3600";', NGINX)
        self.assertNotIn("expires 1y", NGINX)
        with tempfile.TemporaryDirectory() as root:  # the hashed names Django really writes
            storage = ManifestStaticFilesStorage(location=root)
            storage.save("css/app.css", ContentFile(b"body{}"))
            hashed = storage.hashed_name("css/app.css")
        self.assertRegex(hashed, r"^css/app\.[0-9a-f]{12}\.css$")
        self.assertTrue(regex.search("/static/" + hashed))
        for plain in ("/static/css/app.css", "/static/css/app.min.css", "/static/img/logo.png", "/static/fonts/Inter.woff2"):
            self.assertFalse(regex.search(plain), plain)

    def test_media_is_cached_for_30_days_but_not_immutable(self):
        media = NGINX[NGINX.index("location /media/"):NGINX.index("location /panel/media/")]
        self.assertIn('add_header Cache-Control "public, max-age=2592000";', media)
        self.assertNotIn("immutable", media)
        self.assertIn("Content-Security-Policy", media)  # the hardening stays

    @skipUnless(shutil.which("nginx"), "nginx is not installed")
    def test_the_template_passes_nginx_t(self):
        with tempfile.TemporaryDirectory() as d:
            proxy = Path(d, "ravix-proxy.conf")
            proxy.write_text(PROXY)
            result = nginx_test(NGINX.replace("/etc/nginx/ravix-proxy.conf", str(proxy)), d)
        self.assertEqual(result.returncode, 0, result.stderr)


# --------------------------------------------------------------------------------------------------------------------------- gunicorn
class GunicornWorkersTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with mock.patch.dict(os.environ):
            for name in ("GUNICORN_WORKERS", "WEB_CONCURRENCY"):
                os.environ.pop(name, None)
            cls.conf = runpy.run_path(str(GUNICORN))

    def test_a_2_gb_server_gets_a_safe_number(self):
        workers = self.conf["default_workers"]
        self.assertEqual(workers(1, 2048), 3)
        self.assertEqual(workers(2, 2048), 5)
        self.assertEqual(workers(4, 2048), 5)  # more CPUs do not help when memory is the limit
        per_worker, reserved = self.conf["WORKER_MB"], self.conf["RESERVED_MB"]
        self.assertLessEqual(5 * per_worker + reserved, 2048)  # and five workers really fit

    def test_it_follows_cpu_and_memory(self):
        workers = self.conf["default_workers"]
        self.assertEqual(workers(1, 512), 1)
        self.assertEqual(workers(1, 1024), 1)
        self.assertEqual(workers(2, 4096), 5)
        self.assertEqual(workers(4, 4096), 9)
        self.assertEqual(workers(8, 8192), 16)
        self.assertEqual(workers(64, 262144), 16)  # capped
        self.assertEqual(workers(1, 100), 1)  # never zero
        self.assertEqual(workers(2, None), 5)  # unknown memory: the CPU rule, capped for a small server

    def test_environment_overrides(self):
        def chosen(**env):
            with mock.patch.dict(os.environ, env):
                for name in ("GUNICORN_WORKERS", "WEB_CONCURRENCY"):
                    if name not in env:
                        os.environ.pop(name, None)
                return runpy.run_path(str(GUNICORN))["workers"]

        self.assertEqual(chosen(GUNICORN_WORKERS="7"), 7)
        self.assertEqual(chosen(WEB_CONCURRENCY="4"), 4)
        self.assertEqual(chosen(GUNICORN_WORKERS="2", WEB_CONCURRENCY="9"), 2)
        auto = chosen()
        self.assertGreaterEqual(auto, 1)
        for bad in ("abc", "0", "-3", ""):
            self.assertEqual(chosen(GUNICORN_WORKERS=bad), auto, bad)  # nonsense falls back to the automatic value
        self.assertEqual(chosen(RAVIX_WORKER_MB="100", RAVIX_RESERVED_MB="100") >= auto, True)

    def test_memory_and_cpu_limits_of_a_container_count(self):
        conf = self.conf

        def reader(files):
            return lambda path: files.get(path, "")

        mem_files = {"/proc/meminfo": "MemTotal:       16000000 kB\nMemFree: 1 kB", "/sys/fs/cgroup/memory.max": str(2 * 1024 ** 3)}
        with mock.patch.dict(conf["memory_mb"].__globals__, {"_read": reader(mem_files)}):
            self.assertEqual(conf["memory_mb"](), 2048)  # the container limit, not the host's 15.6 GB
        with mock.patch.dict(conf["memory_mb"].__globals__, {"_read": reader({**mem_files, "/sys/fs/cgroup/memory.max": "max"})}):
            self.assertEqual(conf["memory_mb"](), 15625)
        with mock.patch.dict(conf["memory_mb"].__globals__, {"_read": reader({})}):
            self.assertIsNone(conf["memory_mb"]())
        with mock.patch.dict(conf["cpu_count"].__globals__, {"_read": reader({"/sys/fs/cgroup/cpu.max": "150000 100000"})}), \
                mock.patch("os.sched_getaffinity", return_value=set(range(16))):
            self.assertEqual(conf["cpu_count"](), 2)  # a 1.5 CPU quota rounds to 2
        with mock.patch.dict(conf["cpu_count"].__globals__, {"_read": reader({"/sys/fs/cgroup/cpu.max": "max 100000"})}), \
                mock.patch("os.sched_getaffinity", return_value=set(range(4))):
            self.assertEqual(conf["cpu_count"](), 4)

    def test_the_real_config_file_exposes_what_gunicorn_needs(self):
        for key in ("bind", "workers", "threads", "timeout", "keepalive", "max_requests", "on_starting"):
            self.assertIn(key, self.conf)
        self.assertIsInstance(self.conf["workers"], int)


# --------------------------------------------------------------------------------------------------------------------------- install.sh: HTTP/2
CERTBOT_SITE = """\
server {
    server_name example.com;
    location / { proxy_pass http://ravix_app; }
    listen [::]:443 ssl ipv6only=on; # managed by Certbot
    listen 443 ssl; # managed by Certbot
    ssl_certificate /etc/letsencrypt/live/example.com/fullchain.pem; # managed by Certbot
}
server {
    if ($host = example.com) { return 301 https://$host$request_uri; } # managed by Certbot
    listen 80;
    listen [::]:80;
    server_name example.com;
    return 404; # managed by Certbot
}
"""


class Http2Tests(SimpleTestCase):
    def enable(self, text: str, version: str):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "site")
            path.write_text(text)
            code = f"{bash_function('version_ge')}\n{bash_function('nginx_enable_http2')}\nnginx_enable_http2 {path} '{version}'; echo \"rc=$?\""
            first = run_bash(code)
            after_first = path.read_text()
            second = run_bash(code)
            return first, after_first, second, path.read_text()

    def test_old_nginx_gets_the_http2_parameter_once(self):
        for version in ("1.18.0", "1.24.0", ""):
            with self.subTest(version=version):
                first, text, second, text2 = self.enable(CERTBOT_SITE, version)
                self.assertIn("rc=0", first.stdout, first.stderr)
                self.assertIn("listen 443 ssl http2; # managed by Certbot", text)
                self.assertIn("listen [::]:443 ssl http2 ipv6only=on;", text)
                self.assertIn("listen 80;", text)  # the plain-HTTP block is untouched
                self.assertNotIn("http2 on;", text)
                self.assertIn("rc=1", second.stdout)  # nothing left to do ...
                self.assertEqual(text, text2)  # ... and the file did not change again

    def test_new_nginx_gets_the_http2_directive_once_per_server_block(self):
        first, text, second, text2 = self.enable(CERTBOT_SITE, "1.27.3")
        self.assertIn("rc=0", first.stdout, first.stderr)
        self.assertEqual(text.count("http2 on;"), 1)
        self.assertNotIn("443 ssl http2", text)  # the deprecated form is not used
        https_block = text.split("server {")[1]
        self.assertIn("    listen [::]:443 ssl ipv6only=on; # managed by Certbot\n    http2 on;", https_block)  # in the HTTPS block, same indentation
        self.assertNotIn("http2", text.split("server {")[2])
        self.assertIn("rc=1", second.stdout)
        self.assertEqual(text, text2)

    def test_two_https_blocks_each_get_one(self):
        two = CERTBOT_SITE + CERTBOT_SITE.split("server {", 2)[1].join(["server {", ""]).replace("example.com", "other.org")
        _first, text, _second, _ = self.enable(two, "1.26.0")
        self.assertEqual(text.count("http2 on;"), 2)

    def test_without_an_https_block_nothing_changes(self):
        plain = "server {\n    listen 80;\n    listen [::]:80;\n}\n"
        for version in ("1.24.0", "1.27.0"):
            first, text, _second, _ = self.enable(plain, version)
            self.assertIn("rc=1", first.stdout)
            self.assertEqual(text, plain)

    def test_already_http2_is_left_alone(self):
        done = CERTBOT_SITE.replace("443 ssl", "443 ssl http2")
        first, text, _s, _t = self.enable(done, "1.24.0")
        self.assertIn("rc=1", first.stdout)
        self.assertEqual(text, done)

    def test_version_comparison(self):
        code = bash_function("version_ge") + '\nfor p in "1.24.0 1.25.1" "1.25.1 1.25.1" "1.27.0 1.25.1" "1.18.0 1.25.1" "1.9.9 1.25.1" "1.100.0 1.25.1"; do set -- $p; version_ge "$1" "$2" && echo "$1 yes" || echo "$1 no"; done'
        out = dict(line.split() for line in run_bash(code).stdout.splitlines())
        self.assertEqual(out, {"1.24.0": "no", "1.25.1": "yes", "1.27.0": "yes", "1.18.0": "no", "1.9.9": "no", "1.100.0": "yes"})

    @skipUnless(shutil.which("nginx") and shutil.which("openssl"), "nginx/openssl are not installed")
    def test_the_edited_certbot_file_is_accepted_by_nginx(self):
        installed = subprocess.run(["nginx", "-v"], capture_output=True, text=True).stderr
        version = re.search(r"nginx/([\d.]+)", installed).group(1)
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=example.com",
                            "-keyout", f"{d}/k.pem", "-out", f"{d}/c.pem"], capture_output=True, check=True)
            site = Path(d, "edited")
            site.write_text("upstream ravix_app { server 127.0.0.1:9; }\n" + CERTBOT_SITE.replace("/etc/letsencrypt/live/example.com/fullchain.pem", f"{d}/c.pem")
                            .replace("ssl_certificate ", f"ssl_certificate_key {d}/k.pem;\n    ssl_certificate "))
            code = f"{bash_function('version_ge')}\n{bash_function('nginx_enable_http2')}\nnginx_enable_http2 {site} '{version}'"
            self.assertEqual(run_bash(code).returncode, 0)
            result = nginx_test(site.read_text(), d)
        self.assertEqual(result.returncode, 0, result.stderr)


# --------------------------------------------------------------------------------------------------------------------------- install.sh: token
class InstallTokenTests(SimpleTestCase):
    PRELUDE = """
        t() { local m="$1"; shift; printf -- "$m" "$@"; }
        ok() { echo "OK: $(t "$@")"; }
        run() { "$@"; }
        chown() { :; }
        APP_USER=ravix
    """

    def env_file(self, d, text):
        path = Path(d, "ravix.env")
        path.write_text(text)
        path.chmod(0o640)
        return path

    def call(self, path, dry="0"):
        return run_bash(f"{self.PRELUDE}\nENV_FILE={path}; DRY_RUN={dry}\n{bash_function('forget_install_token')}\nforget_install_token; echo rc=$?")

    def test_the_token_line_is_removed_and_everything_else_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.env_file(d, "A=1\nRAVIX_INSTALL_TOKEN=abc123\nDATABASE_URL=postgres://u:p@h/d\nRAVIX_INSTALL_TOKEN_FILE=/x\n")
            result = self.call(path)
            self.assertIn("rc=0", result.stdout, result.stderr)
            self.assertIn("RAVIX_INSTALL_TOKEN was removed", result.stdout)
            self.assertEqual(path.read_text(), "A=1\nDATABASE_URL=postgres://u:p@h/d\nRAVIX_INSTALL_TOKEN_FILE=/x\n")  # the _FILE key is a different one
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)

    def test_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            text = "A=1\nRAVIX_INSTALL_TOKEN=abc123\n"
            path = self.env_file(d, text)
            result = self.call(path, dry="1")
            self.assertIn("would be removed", result.stdout)
            self.assertEqual(path.read_text(), text)

    def test_nothing_to_remove_is_fine(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.env_file(d, "A=1\n")
            result = self.call(path)
            self.assertIn("rc=0", result.stdout)
            self.assertNotIn("OK:", result.stdout)
            self.assertEqual(path.read_text(), "A=1\n")
        self.assertIn("rc=0", self.call("/nonexistent/ravix.env").stdout)

    def test_the_final_message_and_the_token_step_depend_on_the_install_state(self):
        # fresh install: the token is created and the "Last step" block is printed; installed site: neither
        self.assertIn('if site_installed; then forget_install_token; else env_set RAVIX_INSTALL_TOKEN "$(rand_hex 16)"; fi', SCRIPT)
        final = SCRIPT[SCRIPT.index("INSTALLED=0\n"):]
        self.assertIn("if site_installed; then INSTALLED=1; forget_install_token; fi", final)
        installed_branch, fresh_branch = final.split('if [ "$INSTALLED" = 1 ]; then', 1)[1].split("\nelse\n", 1)
        self.assertNotIn("Last step", installed_branch)
        self.assertNotIn("Install token", installed_branch)
        self.assertNotIn("$TOKEN", installed_branch)
        self.assertIn("Last step", fresh_branch)
        self.assertIn("Install token:", fresh_branch)
        self.assertIn("RAVIX_INSTALL_TOKEN line", fresh_branch)  # how the token goes away after the web install

    def test_site_installed_without_a_database_means_not_installed(self):
        with tempfile.TemporaryDirectory() as d:
            helper = f"{self.PRELUDE}\nenv_get() {{ grep -E \"^$1=\" \"$ENV_FILE\" | head -1 | cut -d= -f2-; }}\n{bash_function('site_installed')}\n"
            for env_text in (None, "A=1\n"):  # no file / no DATABASE_URL
                path = Path(d, "x.env")
                path.unlink(missing_ok=True)
                if env_text:
                    path.write_text(env_text)
                result = run_bash(helper + f"ENV_FILE={path}; VENV={sys.prefix}; envsh() {{ false; }}\nsite_installed; echo rc=$?")
                self.assertIn("rc=1", result.stdout, result.stderr)

    def test_site_installed_against_a_real_database(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d, "db.sqlite3")
            relative = os.path.relpath(db, ROOT)
            env = {"DJANGO_SETTINGS_MODULE": "config.settings.development", "DATABASE_URL": f"sqlite:///{relative}", "DJANGO_SECRET_KEY": "k" * 60}
            migrate = subprocess.run([sys.executable, "manage.py", "migrate", "-v", "0"], cwd=ROOT, capture_output=True, text=True, env={**os.environ, **env})
            self.assertEqual(migrate.returncode, 0, migrate.stderr[-500:])
            envfile = Path(d, "ravix.env")
            envfile.write_text(f"DATABASE_URL=sqlite:///{relative}\n")
            code = (f"{self.PRELUDE}\nenv_get() {{ grep -E \"^$1=\" \"$ENV_FILE\" | head -1 | cut -d= -f2-; }}\n{bash_function('site_installed')}\n"
                    f"ENV_FILE={envfile}; APP_DIR={ROOT}; VENV={Path(sys.executable).parent.parent}\n"
                    f"envsh() {{ (cd \"$APP_DIR\" && DATABASE_URL=sqlite:///{relative} DJANGO_SETTINGS_MODULE=config.settings.development DJANGO_SECRET_KEY={'k' * 60} \"$@\"); }}\n"
                    "site_installed; echo rc=$?")
            self.assertIn("rc=1", run_bash(code, PATH=os.environ["PATH"]).stdout)  # migrated but empty: not installed
            subprocess.run([sys.executable, "manage.py", "shell", "-c",
                            "from django.utils import timezone; from apps.core.models import SiteSettings; "
                            "o = SiteSettings.load(); o.installed_at = timezone.now(); o.save()"],
                           cwd=ROOT, capture_output=True, text=True, env={**os.environ, **env}, check=True)
            self.assertIn("rc=0", run_bash(code, PATH=os.environ["PATH"]).stdout)  # installed_at is set
            subprocess.run([sys.executable, "manage.py", "shell", "-c", "from apps.core.models import SiteSettings; SiteSettings.objects.update(installed_at=None)"],
                           cwd=ROOT, capture_output=True, text=True, env={**os.environ, **env}, check=True)
            self.assertIn("rc=1", run_bash(code, PATH=os.environ["PATH"]).stdout)
            subprocess.run([sys.executable, "manage.py", "createsuperuser", "--noinput", "--username", "legacy", "--email", "l@example.com"],
                           cwd=ROOT, capture_output=True, text=True, env={**os.environ, **env}, check=True)
            self.assertIn("rc=0", run_bash(code, PATH=os.environ["PATH"]).stdout)  # an older site: a user exists


# --------------------------------------------------------------------------------------------------------------------------- private plugin files
PLUGIN_FUNCS = "\n".join(bash_function(n) for n in ("nginx_safe_prefix", "nginx_safe_dir", "nginx_plugin_location_exists", "nginx_render_plugin_files", "nginx_add_plugin_files"))
PF_CERTBOT_SITE = """\
server {
    server_name example.com;
    location /static/ { alias /srv/ravix/app/staticfiles/; }
    location / {
        include /etc/nginx/ravix-proxy.conf;
    }
    listen 443 ssl; # managed by Certbot
}
server {
    if ($host = example.com) { return 301 https://$host$request_uri; } # managed by Certbot
    listen 80;
    server_name example.com;
    return 404; # managed by Certbot
}
"""
PF_MANUAL_SITE = PF_CERTBOT_SITE.replace("    location / {", "    location /_plugin_files/ {\n        internal;\n        alias /srv/ravix/plugin_data/;\n    }\n    location / {", 1)


def add_block(text: str, prefix="/_plugin_files/", folder="/srv/ravix/plugin_data") -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp, "site")
        path.write_text(text)
        proc = run_bash(f"{PLUGIN_FUNCS}\nnginx_add_plugin_files {path} '{prefix}' '{folder}'; echo \"rc=$?\"")
        return int(re.search(r"rc=(\d+)", proc.stdout).group(1)), path.read_text()


class PluginFilesLocationTests(SimpleTestCase):
    def test_the_template_has_an_internal_location_with_the_plugin_data_folder(self):
        block = re.search(r"location /_plugin_files/ \{([^}]*)\}", NGINX).group(1)
        self.assertIn("internal;", block)
        self.assertIn("alias /srv/ravix/plugin_data/;", block)
        self.assertIn("plugin_data", SCRIPT)  # install.sh sets RAVIX_PLUGIN_DATA_DIR to this same folder
        self.assertIn('env_set RAVIX_ACCEL_REDIRECT_PREFIX "/_plugin_files/"', SCRIPT)
        self.assertIn('env_set RAVIX_PLUGIN_DATA_DIR "$BASE/plugin_data"', SCRIPT)

    def test_a_site_file_without_the_block_gets_it_before_location_slash(self):
        rc, text = add_block(PF_CERTBOT_SITE)
        self.assertEqual(rc, 0)
        block = re.search(r"location /_plugin_files/ \{([^}]*)\}", text).group(1)
        self.assertIn("internal;", block)
        self.assertIn("alias /srv/ravix/plugin_data/;", block)
        self.assertLess(text.index("location /_plugin_files/"), text.index("    location / {"))
        self.assertEqual(text.count("location /_plugin_files/"), 1)  # the port-80 redirect block has no "location /": nothing was added there

    def test_running_it_again_changes_nothing(self):
        _rc, once = add_block(PF_CERTBOT_SITE)
        rc, twice = add_block(once)
        self.assertEqual(rc, 1)
        self.assertEqual(once, twice)

    def test_the_hand_made_block_on_the_live_site_is_not_duplicated(self):
        rc, text = add_block(PF_MANUAL_SITE)
        self.assertEqual(rc, 1)
        self.assertEqual(text, PF_MANUAL_SITE)
        self.assertEqual(text.count("_plugin_files"), 1)

    def test_a_custom_prefix_and_folder_are_used_and_matched_exactly(self):
        rc, text = add_block(PF_CERTBOT_SITE, "/private-files/", "/data/ravix/plugin_data/")
        self.assertEqual(rc, 0)
        self.assertIn("location /private-files/ {", text)
        self.assertIn("alias /data/ravix/plugin_data/;", text)
        rc2, _ = add_block(text, "/private/", "/data/ravix/plugin_data")  # /private/ is not /private-files/
        self.assertEqual(rc2, 0)

    def test_unsafe_values_are_refused(self):
        for prefix, folder in (("/x/ {} location /evil/ {", "/srv/a"), ("/x/", "/srv/a; root /;"), ("x/", "/srv/a"), ("/x", "/srv/a"), ("/x/", "relative/dir")):
            rc, text = add_block(PF_CERTBOT_SITE, prefix, folder)
            self.assertEqual(rc, 1, (prefix, folder))
            self.assertEqual(text, PF_CERTBOT_SITE)

    def test_a_file_without_location_slash_is_left_alone(self):
        rc, text = add_block("server {\n    listen 80;\n}\n")
        self.assertEqual(rc, 1)

    def test_the_template_is_rewritten_for_the_real_prefix_and_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "site")
            path.write_text(NGINX)
            proc = run_bash(f"{PLUGIN_FUNCS}\nnginx_render_plugin_files {path} /files/ /data/pd/; echo rc=$?")
            self.assertIn("rc=0", proc.stdout)
            text = path.read_text()
            self.assertIn("location /files/ {", text)
            self.assertIn("alias /data/pd/;", text)
            self.assertNotIn("/_plugin_files/", text)

    @skipUnless(shutil.which("nginx"), "nginx is not installed")
    def test_the_patched_file_passes_nginx_t_and_the_location_is_internal(self):
        _rc, text = add_block(NGINX.replace("location /_plugin_files/", "location /gone/"), "/_plugin_files/", "/tmp")
        with tempfile.TemporaryDirectory() as tmp:
            proc = nginx_test(text.replace("/srv/ravix/", f"{tmp}/").replace("include /etc/nginx/ravix-proxy.conf;", "return 204;"), tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr)


# --------------------------------------------------------------------------------------------------------------------------- official update files
UPDATES_FUNCS = "\n".join(bash_function(n) for n in ("nginx_safe_prefix", "nginx_safe_dir", "nginx_plugin_location_exists", "nginx_updates_location_exists", "nginx_add_updates"))


def add_updates(text: str, folder="/srv/ravix/updates") -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp, "site")
        path.write_text(text)
        proc = run_bash(f"{UPDATES_FUNCS}\nnginx_add_updates {path} '{folder}'; echo \"rc=$?\"")
        return int(re.search(r"rc=(\d+)", proc.stdout).group(1)), path.read_text()


class UpdatesLocationTests(SimpleTestCase):
    def test_a_site_file_gets_a_public_read_only_location_before_location_slash(self):
        rc, text = add_updates(PF_CERTBOT_SITE)
        self.assertEqual(rc, 0)
        block = re.search(r"location /updates/v1/ \{([^}]*(?:\{[^}]*\}[^}]*)*)\}", text).group(1)
        self.assertIn("alias /srv/ravix/updates/;", block)
        self.assertIn("autoindex off;", block)
        self.assertNotIn("internal;", block)  # sites outside fetch these files
        self.assertNotIn("try_files", block)  # try_files together with alias is a known nginx trap
        self.assertLess(text.index("location /updates/v1/"), text.index("    location / {"))
        self.assertEqual(text.count("location /updates/v1/"), 1)

    def test_running_it_again_or_with_a_hand_made_block_changes_nothing(self):
        _rc, once = add_updates(PF_CERTBOT_SITE)
        rc, twice = add_updates(once)
        self.assertEqual((rc, once), (1, twice))
        hand = PF_CERTBOT_SITE.replace("    location / {", "    location /updates/v1/ {\n        alias /var/www/updates/v1/;\n    }\n    location / {", 1)
        rc, text = add_updates(hand)
        self.assertEqual((rc, text), (1, hand))

    def test_unsafe_folders_are_refused(self):
        for folder in ("/srv/a; root /;", "relative/dir", "/srv/a b", "/srv/{x}", ""):
            rc, text = add_updates(PF_CERTBOT_SITE, folder)
            self.assertEqual((rc, text), (1, PF_CERTBOT_SITE), folder)

    def test_install_sh_wires_the_option_to_the_folder_the_environment_and_the_checks(self):
        for needle in ("--serve-updates)", 'env_set RAVIX_UPDATES_PUBLISH_DIR "$UPDATES_DIR"', "nginx_add_updates \"$TMP_SITE\"", "nginx_add_updates \"$UP_TMP\"",
                       "must not be inside", 'chown "$APP_USER:www-data" "$UPDATES_DIR"'):
            self.assertIn(needle, SCRIPT, needle)
        self.assertNotIn("UPDATES_DIR=", NGINX)  # the template stays the same for every other site

    @skipUnless(shutil.which("nginx"), "nginx is not installed")
    def test_the_patched_file_passes_nginx_t(self):
        _rc, text = add_updates(NGINX.replace("location /_plugin_files/", "location /gone/"), "/tmp/updates")
        with tempfile.TemporaryDirectory() as tmp:
            proc = nginx_test(text.replace("/srv/ravix/", f"{tmp}/").replace("include /etc/nginx/ravix-proxy.conf;", "return 204;"), tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr)

    @skipUnless(shutil.which("nginx"), "nginx is not installed")
    def test_a_real_nginx_serves_the_signed_files_and_nothing_else(self):
        import urllib.error
        import urllib.request

        with tempfile.TemporaryDirectory() as tmp:
            files = Path(tmp, "updates")
            files.mkdir()
            (files / "index.json").write_text('{"schema":1}')
            (files / "index.json.sig").write_text("{}")
            (files / "ravix-core-1.0.0.zip").write_bytes(b"PK")
            Path(tmp, "secret.txt").write_text("not for the web")
            for d in (tmp, files):
                os.chmod(d, 0o755)  # nginx started by root runs its worker as "nobody", which must be able to read the folder
            for f in (*files.iterdir(), Path(tmp, "secret.txt")):
                os.chmod(f, 0o644)
            port = free_port()
            _rc, site = add_updates(f"server {{\n    listen 127.0.0.1:{port};\n    location / {{\n        return 404;\n    }}\n}}\n", str(files))
            Path(tmp, "site.conf").write_text(site)
            main = Path(tmp, "nginx.conf")
            main.write_text(f"daemon on;\npid {tmp}/nginx.pid;\nerror_log {tmp}/error.log;\nevents {{}}\nhttp {{\n  access_log off;\n"
                            + "".join(f"  {k}_temp_path {tmp}/{k};\n" for k in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi")) + f"  include {tmp}/site.conf;\n}}\n")
            started = subprocess.run(["nginx", "-c", str(main), "-p", tmp], capture_output=True, text=True)
            self.assertEqual(started.returncode, 0, started.stderr)
            try:
                def get(path):
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:  # noqa: S310
                            return r.status, dict(r.headers), r.read()
                    except urllib.error.HTTPError as exc:
                        return exc.code, dict(exc.headers), b""

                status, headers, body = get("/updates/v1/index.json")
                self.assertEqual((status, headers["Content-Type"], body), (200, "application/json", b'{"schema":1}'))
                self.assertIn("max-age=60", headers["Cache-Control"])
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(get("/updates/v1/ravix-core-1.0.0.zip")[1]["Content-Type"], "application/zip")
                self.assertTrue(get("/updates/v1/index.json.sig")[1]["Content-Type"].startswith("text/plain"))
                self.assertEqual(get("/updates/v1/missing.zip")[0], 404)
                self.assertIn(get("/updates/v1/")[0], (403, 404))  # no directory listing
                self.assertEqual(get("/updates/v1/../secret.txt")[0], 404)
                self.assertEqual(get("/updates/v1/%2e%2e/secret.txt")[0], 404)
            finally:
                subprocess.run(["nginx", "-c", str(main), "-p", tmp, "-s", "stop"], capture_output=True)


class ShellcheckTests(SimpleTestCase):
    @skipUnless(shutil.which("shellcheck"), "shellcheck is not installed")
    def test_the_deployment_scripts_have_no_shellcheck_findings(self):
        # SC1111: the typographic quotes in the messages are the keys of deploy/install.fa.tsv; SC2016: intentional single-quoted code handed to bash -c / awk;
        # SC1091: /etc/os-release is not part of the repository.
        scripts = [str(ROOT / "install.sh"), *sorted(str(p) for p in (ROOT / "deploy").glob("*.sh"))]  # default severity (style): notes count too
        proc = subprocess.run(["shellcheck", "-s", "bash", "-e", "SC1111,SC2016,SC1091", "-f", "gcc", *scripts], capture_output=True, text=True)
        self.assertEqual(proc.stdout.strip(), "", proc.stdout)
