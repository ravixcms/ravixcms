#!/usr/bin/env python3
"""Build ``ravix-<version>-cpanel.zip``: the package for cPanel shared hosting ("Setup Python App": Passenger or LiteSpeed).

    python scripts/build_cpanel_zip.py [--out dist] [--no-static] [--include-untracked]

The zip is flat (extract it straight into the application folder, for example ~/ravix). It is the normal source release (tracked files, minus what
.gitattributes keeps out of releases) with: the hosting marker, ``passenger_wsgi.py``, ``requirements-cpanel.txt``, a protective ``.htaccess``,
the collected static files, and without the VPS-only files (deploy/, docker/, install.sh ...). Nothing private is added; the script refuses to
finish if a forbidden or secret-looking file would be inside.
"""
import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VPS_ONLY = ("deploy", "docker", "Dockerfile", "docker-compose.yml", "docker-compose.tls.yml", "install.sh", ".dockerignore", "scripts/vps_smoke.sh",
            "scripts/vps_smoke_check_log.sh", ".env.example", "requirements.lock", ".bandit-baseline.json", ".gitleaks.toml", ".well-known")
FORBIDDEN = re.compile(r"(^|/)(\.env(\..+)?|.*\.sqlite3?|.*\.pem|.*\.key|id_rsa.*|\.git|\.venv|__pycache__|.*\.pyc|tests?|test_[^/]*\.py)(/|$)|^media/|^themes_installed/|^plugins_installed/")
PRIVATE_NAMES = ("ravixcms-site", "ravix_releases", "ravix_ai_pro", "ravixcms-private")
SECRET = re.compile(rb"-----BEGIN (RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}")
README = """Ravix for cPanel shared hosting
================================

1. cPanel > Setup Python App > Create Application: Python 3.11 or newer, Application root = the folder you extract this ZIP into (for example ravix),
   Application URL = your domain, startup file passenger_wsgi.py, entry point application.
2. Extract this ZIP into the Application root (File Manager > Extract).
3. In Setup Python App press "Run Pip Install" (requirements-cpanel.txt) - or in Terminal:
       source ~/virtualenv/ravix/3.11/bin/activate && pip install --only-binary=:all: -r requirements-cpanel.txt
4. Create a MySQL database + user in cPanel > MySQL Databases (all privileges), then open your site: the installer asks for the install code.
   The code is in the file install-key.txt inside the folder ravix-data next to your application folder (File Manager).
5. Finish the installer, then add the cron line it shows in cPanel > Cron Jobs.

Full guide with pictures: docs/cpanel.md (English), docs/cpanel.fa.md (Persian) and docs/cpanel/ (other languages).
"""


def tracked_files(include_untracked: bool) -> list[str]:
    args = ["git", "ls-files", "-z", "--cached"] + (["--others", "--exclude-standard"] if include_untracked else [])
    names = [n for n in subprocess.run(args, cwd=ROOT, capture_output=True, check=True).stdout.decode().split("\0") if n]
    paths = set(names)
    for n in names:  # a pattern such as "apps/*/tests" names the folder, so every parent folder is asked as well (like git archive does)
        parts = n.split("/")
        paths.update("/".join(parts[:i]) for i in range(1, len(parts)))
    attrs = subprocess.run(["git", "check-attr", "-z", "export-ignore", "--stdin"], cwd=ROOT, input="\0".join(sorted(paths)) + "\0", capture_output=True, check=True, text=True).stdout.split("\0")
    ignored = {attrs[i] for i in range(0, len(attrs) - 2, 3) if attrs[i + 2] == "set"}

    def kept(name):
        parts = name.split("/")
        return not any("/".join(parts[:i]) in ignored for i in range(1, len(parts) + 1))

    return [n for n in names if kept(n) and (ROOT / n).is_file()]


def vps_only(name: str) -> bool:
    return any(name == v or name.startswith(v + "/") for v in VPS_ONLY)


def collect_static(stage: Path) -> None:
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.hosting", "RAVIX_HOSTING": "shared", "RAVIX_DATA_DIR": str(stage.parent / "build-data"),
           "PYTHONDONTWRITEBYTECODE": "1"}
    for key in ("DJANGO_SECRET_KEY", "DATABASE_URL"):
        env.pop(key, None)
    r = subprocess.run([sys.executable, "manage.py", "collectstatic", "--noinput", "-v", "0"], cwd=stage, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("collectstatic failed:\n" + r.stderr[-1500:])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dist")
    ap.add_argument("--no-static", action="store_true")
    ap.add_argument("--include-untracked", action="store_true", help="also take new files not yet committed (local trial builds)")
    a = ap.parse_args()

    from apps.core import hosthtaccess
    from apps.core.version import RAVIX_VERSION

    work = Path(tempfile.mkdtemp(prefix="ravix-cpanel-"))
    stage = work / "app"
    try:
        for name in tracked_files(a.include_untracked):
            if vps_only(name):
                continue
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, dest)
        (stage / "ravix-hosting.marker").write_text("This folder is a Ravix shared-hosting (cPanel) install. Do not delete this file.\n")
        (stage / ".htaccess").write_text(hosthtaccess.APP_ROOT)
        (stage / "README-CPANEL.txt").write_text(README)
        if not a.no_static:
            collect_static(stage)
        for must in ("manage.py", "passenger_wsgi.py", "requirements-cpanel.txt", "config/settings/hosting.py", "apps/core/version.py", "ravix-hosting.marker", "locale/fa/LC_MESSAGES/django.mo"):
            if not (stage / must).exists():
                raise SystemExit(f"missing in the package: {must}")
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        target = out / f"ravix-{RAVIX_VERSION}-cpanel.zip"
        problems = []
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for path in sorted(p for p in stage.rglob("*") if p.is_file()):
                rel = path.relative_to(stage).as_posix()
                if FORBIDDEN.search(rel):
                    problems.append(f"forbidden file: {rel}")
                    continue
                blob = path.read_bytes()
                if any(n.encode() in blob for n in PRIVATE_NAMES) or rel.startswith(tuple(PRIVATE_NAMES)):
                    problems.append(f"private name inside: {rel}")
                if SECRET.search(blob):
                    problems.append(f"secret-looking content: {rel}")
                info = zipfile.ZipInfo(rel, date_time=(2020, 1, 1, 0, 0, 0))
                info.external_attr = (0o755 if rel == "manage.py" else 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, blob)
        if problems:
            target.unlink()
            raise SystemExit("refusing to build:\n  " + "\n  ".join(problems))
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        (out / f"{target.name}.sha256").write_text(f"{digest}  {target.name}\n")
        print(f"{target}  {target.stat().st_size:,} bytes  sha256 {digest}")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
