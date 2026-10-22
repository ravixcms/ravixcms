"""بررسی ایستای روش‌های نصب (Docker، install.sh، nginx). اجرای واقعی Docker/systemd/certbot در این آزمون‌ها نیست."""
import os
import re
import shutil
import subprocess
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[3]
BASH = shutil.which("bash")


class ShellScriptTests(SimpleTestCase):
    SCRIPTS = ["install.sh", "docker/entrypoint.sh", "docker/backup-loop.sh", "docker/init-env.sh", "deploy/backup.sh", "deploy/restore.sh"]

    def test_syntax(self):
        for s in self.SCRIPTS:
            r = subprocess.run([BASH, "-n", str(ROOT / s)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{s}: {r.stderr}")

    def test_install_help_and_unknown_option(self):
        r = subprocess.run([BASH, str(ROOT / "install.sh"), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("--domain", r.stdout)
        r = subprocess.run([BASH, str(ROOT / "install.sh"), "--bogus"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_install_refuses_non_root(self):
        if os.getuid() == 0:
            self.skipTest("به‌عنوان root اجرا می‌شود")
        r = subprocess.run([BASH, str(ROOT / "install.sh"), "--dry-run", "--domain", "x.example.com"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("root", r.stderr)

    def test_install_dry_run_changes_nothing(self):
        if os.getuid() != 0:
            self.skipTest("نیازمند root (dry-run هم ابتدا root را می‌سنجد)")
        r = subprocess.run([BASH, str(ROOT / "install.sh"), "--dry-run", "--no-ssl", "--domain", "x.example.com"],
                           capture_output=True, text=True, env={**os.environ, "RAVIX_SKIP_OS_CHECK": "1"})
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout[-500:])
        self.assertIn("[dry-run]", r.stdout)

    def test_init_env_generates_secrets_and_refuses_overwrite(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "docker").mkdir()
            shutil.copy(ROOT / "docker/init-env.sh", Path(d) / "docker/init-env.sh")
            run = lambda *a: subprocess.run([BASH, str(Path(d) / "docker/init-env.sh"), *a], capture_output=True, text=True, cwd=d)  # noqa: E731
            r = run("example.com", "--tls")
            self.assertEqual(r.returncode, 0, r.stderr)
            env = dict(line.split("=", 1) for line in (Path(d) / ".env").read_text().splitlines() if "=" in line and not line.startswith("#"))
            self.assertEqual(env["RAVIX_PUBLIC_URL"], "https://example.com")
            self.assertEqual(env["DJANGO_SECURE_SSL_REDIRECT"], "true")
            self.assertGreaterEqual(len(env["DJANGO_SECRET_KEY"]), 50)
            self.assertEqual(len(env["POSTGRES_PASSWORD"]), 48)
            self.assertEqual(oct((Path(d) / ".env").stat().st_mode & 0o777), "0o600")
            self.assertNotEqual(run("example.com").returncode, 0)  # بازنویسی نمی‌کند
            self.assertNotEqual(run("bad domain!").returncode, 0)


class DockerArtifactTests(SimpleTestCase):
    def test_dockerfile_is_multistage_non_root_with_healthcheck(self):
        text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertGreaterEqual(len(re.findall(r"^FROM ", text, re.M)), 2)
        self.assertRegex(text, r"(?m)^USER ravix$")
        self.assertIn("HEALTHCHECK", text)
        self.assertIn("collectstatic", text)
        self.assertNotRegex(text, r"(?i)\bDJANGO_SECRET_KEY=(?!build-only)")  # هیچ کلید واقعی در تصویر نیست

    def test_dockerignore_excludes_secrets_and_data(self):
        lines = (ROOT / ".dockerignore").read_text().split()
        for must in (".git", ".env", "db.sqlite3", "media", "*.key", "venv"):
            self.assertIn(must, lines)

    def test_compose_wiring(self):
        try:
            import yaml

            data = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
        except ImportError:
            import json

            if not shutil.which("docker"):
                self.skipTest("نه PyYAML هست و نه docker CLI")
            r = subprocess.run(["docker", "compose", "-f", str(ROOT / "docker-compose.yml"), "config", "--format", "json"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                self.skipTest("docker compose config در دسترس نیست")
            data = json.loads(r.stdout)
        svc = data["services"]
        self.assertEqual({"db", "redis", "ravix", "nginx", "backup"}, set(svc))
        self.assertNotIn("ports", svc["db"])      # دیتابیس بیرون منتشر نمی‌شود
        self.assertNotIn("ports", svc["ravix"])
        self.assertTrue({"pgdata", "ravix_media", "ravix_data", "ravix_backups"} <= set(data["volumes"]))
        def vols(name):  # «src:dst[:ro]» (compose config --format json شکل بلند می‌دهد)
            out = []
            for v in svc[name]["volumes"]:
                out.append(v if isinstance(v, str) else f"{v.get('source')}:{v.get('target')}" + (":ro" if v.get("read_only") else ""))
            return out

        self.assertTrue(any("ravix_backups" in v for v in vols("backup")))
        self.assertIn("ravix_media:/srv/media:ro", vols("nginx"))

    def test_compose_env_names_exist_in_settings(self):
        text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        settings_src = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "config/settings").glob("*.py"))
        for name in set(re.findall(r"^\s+((?:RAVIX|DJANGO)_[A-Z_]+):", text, re.M)) - {"DJANGO_SETTINGS_MODULE", "RAVIX_DOMAIN"}:  # دومی فقط برای envsubst در nginx است
            self.assertIn(f'"{name}"', settings_src, f"{name} در تنظیمات خوانده نمی‌شود")

    def test_nginx_templates_only_substitute_domain(self):
        for name in ("default.conf.template", "tls.conf.template"):
            text = (ROOT / "docker/nginx" / name).read_text(encoding="utf-8")
            self.assertEqual(set(re.findall(r"\$\{(\w+)\}", text)) - {"RAVIX_DOMAIN"}, set(), name)
            self.assertIn("client_max_body_size", text)
            self.assertIn("proxy_pass", (ROOT / "docker/nginx/ravix-proxy.conf").read_text())
