#!/usr/bin/env python3
"""Real-server migration test (runs as root on an Ubuntu machine with a real systemd, e.g. the GitHub runner `ubuntu-24.04`).

1. Installs the OLD release (default: the commit the production server runs) with ITS OWN install.sh: PostgreSQL, nginx, systemd, plain virtualenv.
2. Makes it look like the owner's server: an installed site with content in several languages (translation groups, Studio), a user, an MCP connection,
   SMTP settings in ravix.env, a theme and a plugin that live OUTSIDE the repository, a media file, and the manual /_plugin_files/ nginx block.
3. Runs the NEW install.sh over it (the migration: ravix-deploy user, versioned virtualenv, new units) and checks that nothing of the owner's was changed.
4. Runs real updates through the real executor (systemd path unit) and the failure scenarios: broken migration, unhealthy /healthz after restart,
   a full disk, a tampered package, and `kill -9` of the whole executor in the middle of an update (recovered by the timer).

Usage:  sudo -E python3 scripts/e2e_server_migration.py [--old-ref 2c9c074]
Needs: git history of the old ref, pip-installed requirements.lock in the python that runs this script (to build signed test releases).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOMAIN = "ravix.test"
BASE = Path("/srv/ravix")
APP = BASE / "app"
ENV_FILE = Path("/etc/ravix/ravix.env")
DIST = BASE / "e2e_dist"          # the "official source" of the test (a folder source; readable by the deploy user)
SITE_FILE = Path("/etc/nginx/sites-available/ravix")
FAILED: list[str] = []
SEEN_KEYS: set[str] = set()


# install.sh picks python3.13/3.12/3.11 from PATH. On the owner's Ubuntu 24.04 that is /usr/bin/python3.12. The GitHub runner has its own
# Python 3.13 first in PATH (setup-python), which hid a lock-file problem that only shows on 3.12. install.sh therefore runs with the system PATH only.
SYSTEM_PATH = os.environ.get("E2E_PATH_PREFIX", "") + ":/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
SYSTEM_ENV = {"PATH": SYSTEM_PATH.lstrip(":"), "HOME": "/root", "LANG": "C.UTF-8"}
# a network proxy (not set on a GitHub runner) is the only thing that is passed through
SYSTEM_ENV.update({k: os.environ[k] for k in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "PIP_CERT", "CURL_CA_BUNDLE") if k in os.environ})


def install_sh(src: Path, *, check_rc=True):
    """The install.sh of a source folder, the way the owner runs it (system Python only, no -E leftovers)."""
    extra = ["--no-systemd", "--no-nginx"] if PARTIAL else []
    return sh(["bash", str(src / "install.sh"), "--domain", DOMAIN, "--no-ssl", "--no-www", *extra, "--source", str(src), "-y"], timeout=2400, env=SYSTEM_ENV, check_rc=check_rc)


ENV_LOADER = (ROOT / "deploy" / "_envfile.sh").read_text(encoding="utf-8")


def say(msg):
    print(msg, flush=True)


NEW_INSTALL: dict = {}   # the output of the new install.sh
PARTIAL = False   # --partial: a machine without systemd (a container). Everything that needs a running service is listed as SKIPPED, never as passed.
SKIPPED: list[str] = []


def svc(fn, msg):
    """A check that needs a running service (systemd, nginx, gunicorn)."""
    if PARTIAL:
        say("  - SKIPPED (no systemd): " + msg)
        SKIPPED.append(msg)
        return None
    return check(fn(), msg)


def check(cond, msg):
    say(("  ✔ " if cond else "  ✘ ") + msg)
    if not cond:
        FAILED.append(msg)
    return cond


def sh(cmd, *, check_rc=True, timeout=1800, env=None, input_text=None):
    """Runs a command (a list or a shell string) and returns the CompletedProcess; fails loudly on a non-zero exit."""
    r = subprocess.run(cmd, check=False, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout, env=env, input=input_text)
    if check_rc and r.returncode != 0:
        say(f"--- command failed ({r.returncode}): {cmd}\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}")
        raise SystemExit(f"command failed: {cmd if isinstance(cmd, str) else ' '.join(cmd)}")
    return r


def site(args: str, *, user="ravix", input_text=None, check_rc=True, timeout=600):
    """Runs `manage.py ...` (or any python args) the way the service does: the service user, ravix.env loaded, the real virtualenv."""
    # the loader comes from this checkout, not from /srv/ravix/app: the old release that is installed first does not have deploy/_envfile.sh
    script = f'{ENV_LOADER}\nrav_load_env {ENV_FILE}; cd {APP}; exec {BASE}/venv/bin/python {args}'
    return sh(["runuser", "-u", user, "--", "bash", "-c", script], input_text=input_text, check_rc=check_rc, timeout=timeout)


def curl(path, *, host=DOMAIN, headers=(), data=None, method=None):
    cmd = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-H", f"Host: {host}", "-H", "X-Forwarded-Proto: https", "--max-time", "20"]
    for h in headers:
        cmd += ["-H", h]
    if data is not None:
        cmd += ["-X", method or "POST", "--data", data]
    cmd.append(f"http://127.0.0.1{path}")
    return sh(cmd, check_rc=False).stdout.strip()


def healthy(timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        if curl("/healthz") == "200":
            return True
        time.sleep(2)
    return False


def tree_hash(path: Path) -> str:
    h = hashlib.sha256()
    if not path.exists():
        return "missing"
    files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    for f in files:
        h.update(str(f.relative_to(path if path.is_dir() else path.parent)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def env_pairs() -> dict:
    out = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def unit_active(name) -> bool:
    return sh(["systemctl", "is-active", "--quiet", name], check_rc=False).returncode == 0


def unit_running(name) -> bool:
    """A oneshot service that is working says "activating", which is-active --quiet reports as not active."""
    return sh(["systemctl", "is-active", name], check_rc=False).stdout.strip() in ("active", "activating")


def psql(sql: str) -> str:
    return sh(["runuser", "-u", "postgres", "--", "psql", "-At", "-d", "ravix", "-c", sql]).stdout.strip()


# ------------------------------------------------------------------------------------------------ data snapshot
ADDITIVE = ("auth_permission", "django_content_type")  # a new model of a new release adds rows here; every row that existed must stay identical
VOLATILE = {"auth_permission", "django_content_type", "django_session", "django_migrations", "updater_updaterun", "updater_updatelock", "updater_updatejob", "audit_auditlog", "core_cronrun", "ravin_cache",
            "mcp_mcpconnection", "accounts_user"}  # last_login / last_used change when someone logs in or a token is used; checked separately below


def snapshot() -> dict:
    """{table: (columns, md5 of all rows)} for every table of the site's database, using only the columns given by the table itself."""
    tables = [t for t in psql("select tablename from pg_tables where schemaname='public' order by 1").splitlines() if t]
    snap = {}
    for t in tables:
        cols = psql(f"select string_agg(column_name, ',' order by column_name) from information_schema.columns where table_schema='public' and table_name='{t}'")
        snap[t] = {"cols": cols.split(","), "count": int(psql(f'select count(*) from "{t}"'))}
    return snap


def table_hash(table: str, cols: list[str]) -> str:
    sel = ", ".join(f'"{c}"' for c in cols)
    return psql(f'select coalesce(md5(string_agg(r::text, \'|\' order by r::text)), \'-\') from (select {sel} from "{table}") r')


def data_fingerprint(before: dict | None = None) -> dict:
    snap = snapshot()
    out = {}
    for t, info in snap.items():
        if t in VOLATILE:
            continue
        cols = info["cols"]
        if before and t in before:
            cols = [c for c in before[t]["cols"] if c in cols]  # only the columns that existed before (a new migration adds columns)
        out[t] = {"cols": cols, "count": info["count"], "hash": table_hash(t, cols)}
    return out


def additive_rows(columns: dict | None = None) -> dict:
    out = {}
    for t in ADDITIVE:
        cols = (columns or {}).get(t) or [c for c in psql(f"select string_agg(column_name, ',' order by column_name) from information_schema.columns where table_schema='public' and table_name='{t}'").split(",")]
        sel = ", ".join(f'"{c}"' for c in cols)
        out[t] = {"cols": cols, "rows": set(psql(f'select r::text from (select {sel} from "{t}") r').splitlines())}
    return out


def check_additive(before: dict, label: str):
    now = additive_rows({t: v["cols"] for t, v in before.items()})
    for t, v in before.items():
        missing = v["rows"] - now[t]["rows"]
        check(not missing, f"{label}: every old row of {t} is unchanged ({len(v['rows'])} rows; new rows added: {len(now[t]['rows'] - v['rows'])})" + (f" — changed or removed: {sorted(missing)[:3]}" if missing else ""))


def compare_data(before: dict, after: dict, label: str):
    bad = [t for t in before if t not in after or before[t]["hash"] != table_hash(t, before[t]["cols"]) or before[t]["count"] != after[t]["count"]]
    check(not bad, f"{label}: every content table is identical to before ({len(before)} tables)" + (f" — differences: {bad[:6]}" if bad else ""))


# ------------------------------------------------------------------------------------------------ seeding
SEED = r'''
import uuid, hashlib, datetime
from pathlib import Path
from django.utils import timezone
from django.contrib.auth import get_user_model
from apps.core.models import SiteSettings
from apps.content.models import Post, Page, Redirect
from apps.mcp.models import McpSettings, OAuthClient, McpConnection, McpToken
U = get_user_model()
owner = U.objects.create_user(username="owner", email="owner@example.com", password="Zx9!kLm2#pQrs-Tt7", role="super_admin")
writer = U.objects.create_user(username="writer", email="writer@example.com", password="Zx9!kLm2#pQrs-Tt7", role="author")
SiteSettings.objects.update_or_create(pk=1, defaults={"installed_at": timezone.now(), "site_name": "Ravix E2E", "tagline": "tagline", "language": "fa"})
group = uuid.uuid4()
for lang in ("fa", "en", "ar", "tr"):
    p = Post.objects.create(title=f"post {lang}", slug=f"post-{lang}", body=f"hello [out](https://example.org/{lang}) and [in](/fa/post-fa/)", status="published",
                            language=lang, translation_group=group, author=writer)
g2 = uuid.uuid4()
for lang in ("fa", "de"):
    Page.objects.create(title=f"page {lang}", slug=f"page-{lang}", body="page body", status="published", language=lang, translation_group=g2)
Redirect.objects.create(old_path="/old/", new_path="/fa/post-fa/")
try:
    from apps.studio.models import StudioDocument
    StudioDocument.objects.create(post=Post.objects.get(slug="post-fa"), updated_by=owner)
    print("studio seeded")
except Exception as exc:  # noqa: BLE001
    print("studio NOT seeded:", type(exc).__name__, exc)
Path("/srv/ravix/media").mkdir(exist_ok=True)
(Path("/srv/ravix/media") / "e2e-upload.txt").write_text("media bytes that must survive")
McpSettings.objects.update_or_create(pk=1, defaults={"enabled": True})
client = OAuthClient.objects.create(client_id="https://client.example/e2e", kind="cimd", name="e2e", redirect_uris=["https://client.example/cb"])
conn = McpConnection.objects.create(user=owner, client=client, scope="content:read", resource="https://ravix.test/mcp/")
TOKEN = "e2e-mcp-token-" + "x" * 30
McpToken.objects.create(connection=conn, kind="access", token_hash=hashlib.sha256(TOKEN.encode()).hexdigest(), expires_at=timezone.now() + datetime.timedelta(days=30))
print("seeded")
'''


def preflight(label: str):
    """ravix_preflight on the real server. This test installs with --no-ssl, so exactly one error is expected: the HTTPS redirect is off. Any other
    error fails. (The warning about a writable plugins_installed folder is the long-standing advisory for the folder the owner's plugins live in.)"""
    r = site("manage.py ravix_preflight", check_rc=False)
    errors = [ln.strip() for ln in r.stdout.splitlines() if ln.strip().startswith("✘")]
    unexpected = [e for e in errors if "SECURE_SSL_REDIRECT" not in e]
    check(not unexpected, f"preflight {label}: no error except the HTTPS redirect of this --no-ssl test" + (f" — {unexpected}" if unexpected else ""))
    say("\n".join("    " + ln for ln in r.stdout.splitlines() if ln.strip().startswith(("✘", "!")) or "passed" in ln))


def mcp_status() -> str:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    return curl("/mcp/", headers=["Authorization: Bearer e2e-mcp-token-" + "x" * 30, "Content-Type: application/json", "Accept: application/json, text/event-stream"], data=body)


def seed_external(old_site_version: str):
    """A theme and a plugin that are NOT part of the repository (like ravixcms-site / ravix_releases on the real server)."""
    theme = BASE / "themes_installed" / "e2e-theme"
    shutil.copytree(ROOT / "themes/default", theme, ignore=shutil.ignore_patterns("__pycache__"))
    m = json.loads((theme / "theme.json").read_text())
    m.update(slug="e2e-theme", name="E2E theme", version="1.0.0")
    (theme / "theme.json").write_text(json.dumps(m))
    plug = BASE / "plugins_installed" / "e2e_plugin"
    plug.mkdir(parents=True, exist_ok=True)
    (plug / "plugin.json").write_text(json.dumps({"slug": "e2e_plugin", "name": "E2E plugin", "version": "1.0.0", "description": "outside the repo", "api_version": 1,
                                                    "ravix": ">=0.5.0,<2.0.0", "capabilities": {"e2e_plugin.use": "use"}, "settings": [], "admin_pages": [], "public_routes": []}))
    (plug / "__init__.py").write_text("")
    (plug / "views.py").write_text("")
    (BASE / "plugin_data").mkdir(exist_ok=True)
    (BASE / "plugin_data" / "keep.bin").write_bytes(b"plugin data that must survive")
    for p in (theme, plug, BASE / "plugin_data" / "keep.bin"):
        sh(["chown", "-R", "ravix:ravix" if "plugin_data" in str(p) else "root:ravix", str(p)])
    sh(["chmod", "-R", "u=rwX,g=rX,o-rwx", str(theme), str(plug)])


def add_manual_nginx_block():
    """The owner's server has its own /_plugin_files/ block. After the new install.sh there must still be exactly one."""
    text = SITE_FILE.read_text(encoding="utf-8")
    n = len(re.findall(r"location\s+(\^~\s+)?/_plugin_files/\s*\{", text))
    say(f"  (the installed nginx site already has {n} /_plugin_files/ block(s))")
    if n == 0:
        block = ("    # manual block added by the owner\n    location /_plugin_files/ {\n        internal;\n        alias /srv/ravix/plugin_data/;\n    }\n")
        text = re.sub(r"(\n\s*location\s+/\s*\{)", "\n" + block + r"\1", text, count=1)
        SITE_FILE.write_text(text, encoding="utf-8")
        sh(["nginx", "-t"])
        sh(["systemctl", "reload", "nginx"])


# ------------------------------------------------------------------------------------------------ releases
def last_migration(folder: Path):
    names = sorted(p.stem for p in Path(folder).glob("[0-9][0-9][0-9][0-9]_*.py"))
    return names[-1], int(names[-1][:4]) + 1


def set_version(tree: Path, version: str):
    f = tree / "apps/core/version.py"
    f.write_text(re.sub(r'RAVIX_VERSION = "[^"]+"', f'RAVIX_VERSION = "{version}"', f.read_text()), encoding="utf-8")


def build_release(tmp: Path, version: str, *, note_migration=False, boom=None, bad_health=False, key: Path):
    """Builds and signs ravix-core `version` from the checked-out tree into DIST. boom: None | 'raise' | 'sleep'."""
    src = tmp / f"src-{version}"
    shutil.copytree(ROOT, src, ignore=shutil.ignore_patterns(".git", ".venv", "venv", "__pycache__", "*.pyc", "db.sqlite3*", "media", "staticfiles", "update_work", "update_backups",
                                                              "plugins_installed", "plugin_backups", ".env", "*.sqlite3", "node_modules"))
    set_version(src, version)
    mig = src / "apps/updater/migrations"
    last, number = last_migration(mig)
    if note_migration:
        name = f"{number:04d}_e2e_note_{version.replace('.', '_')}"
        (mig / f"{name}.py").write_text("from django.db import migrations\n\n\nclass Migration(migrations.Migration):\n"
                                        f"    dependencies = [('updater', '{last}')]\n    operations = [migrations.RunSQL('select 1', migrations.RunSQL.noop)]\n")
        last, number = name, number + 1
    if boom:
        body = "raise RuntimeError('migration exploded postgres://user:S3CR3TPASS@db/x')" if boom == "raise" else "import time; time.sleep(600)"
        (mig / f"{number:04d}_boom_{version.replace('.', '_')}.py").write_text(
            "from django.db import migrations\n\n\ndef boom(apps, schema_editor):\n    " + body + "\n\n\nclass Migration(migrations.Migration):\n"
            f"    dependencies = [('updater', '{last}')]\n    operations = [migrations.RunPython(boom, migrations.RunPython.noop)]\n")
    if bad_health:
        f = src / "apps/core/health.py"
        f.write_text(f.read_text().replace('return JsonResponse({"status": "ok"})', 'return JsonResponse({"status": "broken-on-purpose"}, status=500)'))
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.development", "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("DATABASE_URL", None)
    sh([sys.executable, "manage.py", "ravix_release", "core", "--source", str(src), "--out", str(DIST), "--key-file", str(key)], env=env)
    sh(["chown", "-R", "root:ravix", str(DIST)])
    sh(["chmod", "-R", "u=rwX,g=rX,o-rwx", str(DIST)])


def current_version() -> str:
    return site("-c \"from apps.core.version import RAVIX_VERSION as v; print(v)\"").stdout.strip().splitlines()[-1]


def newest_job() -> dict:
    r = site("manage.py shell -c \"from apps.updater.models import UpdateJob as J; j=J.objects.order_by('-pk').first(); print(j.pk, j.status, ' '.join((j.message or '').split())) if j else print('none')\"", check_rc=False)
    if r.returncode != 0 or not r.stdout.strip():
        return {"id": "", "status": "?", "message": "the site did not answer (restarting?)"}
    out = r.stdout.strip().splitlines()[-1]
    parts = out.split(" ", 2)
    return {"id": parts[0], "status": parts[1] if len(parts) > 1 else "", "message": parts[2] if len(parts) > 2 else ""}


def queue_update(version: str):
    site(f"manage.py ravix_update queue core --to {version}", user="ravix")


def wait_job(done=("succeeded", "failed", "rolled_back", "needs_recovery", "cancelled"), timeout=900) -> dict:
    end = time.time() + timeout
    last = {}
    while time.time() < end:
        last = newest_job()
        if last.get("status") in done:
            return last
        time.sleep(4)
    return last


def code_manifest() -> dict:
    out = {}
    for top in ("apps", "config", "templates"):
        base = APP / top
        for f in sorted(base.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                out[str(f.relative_to(APP))] = hashlib.sha1(f.read_bytes()).hexdigest()
    return out


def code_is(good: dict, msg: str) -> bool:
    """True when the program files are exactly ``good``; on a mismatch the first differing paths are printed."""
    now = code_manifest()
    if now == good:
        return check(True, msg)
    changed = sorted(k for k in good if k in now and good[k] != now[k])
    added = sorted(set(now) - set(good))
    removed = sorted(set(good) - set(now))
    return check(False, f"{msg} — changed {changed[:6]}, added {added[:6]}, removed {removed[:6]}")


def diagnostics():
    say("\n===== diagnostics =====")
    for cmd in ("journalctl -u ravix.service -n 40 --no-pager", "journalctl -u ravix-update.service -n 60 --no-pager", "journalctl -u ravix-restart.service -n 20 --no-pager",
                "tail -n 30 /var/log/nginx/error.log", "ls -la /srv/ravix /srv/ravix/jobs /srv/ravix/jobs/queue", "systemctl list-units 'ravix*' --all --no-pager"):
        say(f"--- {cmd}\n" + sh(cmd, check_rc=False).stdout[-2500:])


# ------------------------------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-ref", default=os.environ.get("E2E_OLD_REF", "2c9c074"))
    ap.add_argument("--partial", action="store_true", help="a machine without systemd (a container): run only what needs no running service; the rest is listed as SKIPPED")
    ap.add_argument("--skip-kill", action="store_true", help="skip the slow kill -9 scenario")
    args = ap.parse_args()
    global PARTIAL
    if os.geteuid() != 0:
        raise SystemExit("run as root")
    if args.partial:
        PARTIAL = True
    elif not Path("/run/systemd/system").is_dir():
        raise SystemExit("this test needs a real systemd (use --partial in a container to run only the part that does not need it)")
    tmp = Path(tempfile.mkdtemp(prefix="ravix-e2e-srv-"))
    try:
        run(tmp, args)
    except BaseException:
        diagnostics()
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if FAILED:
        say(f"\n✘ {len(FAILED)} check(s) failed:")
        for f in FAILED:
            say("   - " + f)
        sys.exit(1)
    if SKIPPED:
        say(f"\n{len(SKIPPED)} check(s) SKIPPED because there is no systemd here (they are NOT passed):")
        for f in SKIPPED:
            say("   - " + f)
        say("\n✔ the PARTIAL migration test passed (everything that could run without systemd)")
        return
    say("\n✔ the real-server migration test passed")


def read_site_file() -> str:
    return SITE_FILE.read_text() if SITE_FILE.exists() else ""


def copy_source(tmp: Path, name: str) -> Path:
    dst = tmp / name
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(".git", ".venv", "venv", "__pycache__", "node_modules", "*.pyc"), symlinks=True)
    return dst


def nothing_changed(label: str, app_before: str, env_before: str, nginx_before: str, out: str):
    """After an install that failed: the program code and the virtualenv are exactly as they were and the site still answers."""
    check("NOTHING WAS CHANGED" in out or "put back" in out, f"{label}: the message tells the operator what state the server is in")
    check(tree_hash(APP) == app_before, f"{label}: /srv/ravix/app is byte-identical to before")
    venv = BASE / "venv"
    check(venv.is_dir() and not venv.is_symlink(), f"{label}: the active virtualenv is still the old plain folder")
    check(not list(BASE.glob("venv.legacy-*")), f"{label}: the old virtualenv was not moved")
    check(ENV_FILE.read_text() == env_before, f"{label}: ravix.env is unchanged")
    check(read_site_file() == nginx_before, f"{label}: the nginx site is unchanged")
    svc(lambda: unit_active("ravix.service") and healthy(), f"{label}: the site is still healthy")
    svc(lambda: mcp_status() == "200", f"{label}: the MCP token still works")


def failed_installs(tmp: Path, before: dict, data0: dict):
    """The new install.sh must be transactional: the Python environment is built first, and only a good one lets the code change."""
    say("۲ب) install.sh that cannot finish must leave the server exactly as it was")
    app_before, env_before, nginx_before = tree_hash(APP), ENV_FILE.read_text(), read_site_file()
    sys_py = tuple(int(x) for x in sh(["/usr/bin/python3", "-c", "import sys; print(*sys.version_info[:2])"]).stdout.split())
    say(f"   (system Python: {sys_py[0]}.{sys_py[1]})")

    # (a) a wrong hash: pip refuses the package, on every Python
    bad = copy_source(tmp, "bad-hash")
    lock = bad / "requirements.hashes.lock"
    lock.write_text(re.sub(r"^asgiref==[^\n]*\n(?:[ \t]+[^\n]*\n)*", lambda m: re.sub(r"[0-9a-f]{64}", "0" * 64, m.group(0)), lock.read_text(), count=1, flags=re.M))
    r = install_sh(bad, check_rc=False)
    check(r.returncode != 0, f"a lock file with a wrong hash makes install.sh fail (exit {r.returncode})")
    nothing_changed("wrong hash", app_before, env_before, nginx_before, r.stdout + r.stderr)
    check(not list(BASE.glob("venv-*")), "wrong hash: the half-built virtualenv was removed")

    # (b) the owner's real failure: a dependency missing from the lock (typing-extensions, needed by psycopg on Python < 3.13)
    if sys_py < (3, 13):
        miss = copy_source(tmp, "missing-dep")
        lock = miss / "requirements.hashes.lock"
        lock.write_text(re.sub(r"^typing-extensions==[^\n]*\n(?:[ \t]+[^\n]*\n)*", "", lock.read_text(), flags=re.M))
        r = install_sh(miss, check_rc=False)
        check(r.returncode != 0, f"a lock file without typing-extensions makes install.sh fail on Python {sys_py[0]}.{sys_py[1]} (exit {r.returncode})")
        nothing_changed("missing dependency", app_before, env_before, nginx_before, r.stdout + r.stderr)
    else:
        say(f"   - SKIPPED: the missing-typing-extensions case only fails on Python < 3.13 (this system has {sys_py[0]}.{sys_py[1]})")

    # (c) the packages install, but the new code cannot start: the previous code is put back by install.sh itself
    broken = copy_source(tmp, "bad-code")
    with (broken / "config" / "settings" / "production.py").open("a", encoding="utf-8") as f:
        f.write('\nraise RuntimeError("e2e: broken settings")\n')
    r = install_sh(broken, check_rc=False)
    check(r.returncode != 0, f"code that cannot start makes install.sh fail (exit {r.returncode})")
    nothing_changed("broken code", app_before, env_before, nginx_before, r.stdout + r.stderr)
    check(bool(list(BASE.glob("app.failed-*"))), "broken code: the rejected code was kept as app.failed-*")
    check(bool(list((BASE / "update_backups").glob("code-before-install-*.tar.gz"))), "broken code: a copy of the previous code was saved before it was replaced")

    # what is left now looks like the owner's server after his failed install: a half-built venv-<version>, app.failed-*, jobs, update_*, ravix-deploy
    ver = re.search(r'RAVIX_VERSION\s*=\s*"([^"]+)"', (ROOT / "apps" / "core" / "version.py").read_text()).group(1)
    half = BASE / f"venv-{ver}"
    shutil.rmtree(half, ignore_errors=True)
    sh(["runuser", "-u", "ravix-deploy", "--", "/usr/bin/python3", "-m", "venv", str(half)])
    check((half / "bin" / "python").exists(), f"a half-built {half.name} is on the server (as after the owner's failed install)")
    check(not list(BASE.glob(".install-lock.*")), "no temporary lock copy is left in /srv/ravix")
    check(tree_hash(APP) == app_before, "the code is still the old one")


def run(tmp: Path, args):
    old = tmp / "old"
    old.mkdir()
    say(f"۱) installing the OLD release {args.old_ref} with its own install.sh (real PostgreSQL, nginx and systemd)")
    sh(f"git -C {ROOT} cat-file -e {args.old_ref}^{{commit}}")
    sh(f"git -C {ROOT} archive {args.old_ref} | tar -x -C {old}")
    install_sh(old)
    svc(lambda: unit_active("ravix.service"), "the old install runs ravix.service")
    svc(lambda: not (BASE / "jobs").exists() or not unit_active("ravix-update.path"), "the old install has no update executor yet (this is the pre-migration state)")
    check(sh(["id", "ravix-deploy"], check_rc=False).returncode != 0, "the user ravix-deploy does not exist yet")
    check((BASE / "venv").is_dir() and not (BASE / "venv").is_symlink(), "the old virtualenv is a plain folder (like the owner's server)")
    old_version = current_version()

    say("۲) making it look like the owner's server")
    r = site("manage.py shell", input_text=SEED)
    check("seeded" in r.stdout, "content, users, Studio, redirects and an MCP connection were created")
    seed_external(old_version)
    if not PARTIAL:
        add_manual_nginx_block()
    with ENV_FILE.open("a", encoding="utf-8") as f:
        f.write("\nEMAIL_HOST=smtp.example.org\nEMAIL_PORT=587\nEMAIL_HOST_USER=mailer\nEMAIL_HOST_PASSWORD=smtp-secret-123\nEMAIL_USE_TLS=true\nDEFAULT_FROM_EMAIL=Ravix <ravixcms@gmail.com>\n")
    svc(healthy, "the old site answers /healthz")
    svc(lambda: mcp_status() == "200", "the MCP token works before the migration")

    before = {
        "theme": tree_hash(BASE / "themes_installed" / "e2e-theme"),
        "plugin": tree_hash(BASE / "plugins_installed" / "e2e_plugin"),
        "plugin_data": tree_hash(BASE / "plugin_data"),
        "media": tree_hash(BASE / "media"),
        "env": env_pairs(),
    }
    data0 = data_fingerprint()
    additive0 = additive_rows()
    say(f"   (snapshot: {len(data0)} tables, {sum(v['count'] for v in data0.values())} rows)")

    failed_installs(tmp, before, data0)

    say("۳) the NEW install.sh over the old installation (the migration)")
    r = install_sh(ROOT)
    NEW_INSTALL["out"] = r.stdout + r.stderr
    check("unexpected token" not in NEW_INSTALL["out"], "install.sh reads a ravix.env whose value has spaces and < > without quotes (the owner's DEFAULT_FROM_EMAIL)")
    check(sh(["id", "ravix-deploy"], check_rc=False).returncode == 0, "the user ravix-deploy was created")
    link = BASE / "venv"
    check(link.is_symlink() and re.match(r"^venv-[0-9.]+", os.readlink(link) or ""), f"venv is now a symlink to a versioned folder ({os.readlink(link) if link.is_symlink() else 'not a link'})")
    check(any(p.name.startswith("venv.legacy-") for p in BASE.iterdir()), "the old plain virtualenv was kept as venv.legacy-*")
    for u in ("ravix.service", "ravix-update.path", "ravix-restart.path", "ravix-update.timer", "ravix-backup.timer", "nginx.service"):
        svc(lambda u=u: unit_active(u), f"{u} is active")
    svc(healthy, "the site answers /healthz after the migration")
    svc(lambda: mcp_status() == "200", "the old MCP token still works")
    check(tree_hash(BASE / "themes_installed" / "e2e-theme") == before["theme"], "the external theme is byte-identical")
    check(tree_hash(BASE / "plugins_installed" / "e2e_plugin") == before["plugin"], "the external plugin is byte-identical")
    check(tree_hash(BASE / "plugin_data") == before["plugin_data"], "plugin_data is byte-identical")
    check(tree_hash(BASE / "media") == before["media"], "media is byte-identical")
    after_env = env_pairs()
    # install.sh deliberately removes RAVIX_INSTALL_TOKEN once the site is installed (the install page is closed for good). The owner's real server
    # has no token any more, so it is not part of the "old keys stay as they are" comparison; its removal is checked on its own.
    check("RAVIX_INSTALL_TOKEN" in before["env"] and "RAVIX_INSTALL_TOKEN" not in after_env, "the install token was removed from ravix.env because the site is installed (by design)")
    before["env"].pop("RAVIX_INSTALL_TOKEN", None)
    changed = {k: (v, after_env.get(k)) for k, v in before["env"].items() if after_env.get(k) != v}
    check(not changed, "every old key of ravix.env kept its value" + (f" — changed: {sorted(changed)}" if changed else ""))
    check(all(after_env.get(k) for k in ("EMAIL_HOST", "EMAIL_HOST_PASSWORD")), "the SMTP settings are still in ravix.env")
    new_keys = sorted(set(after_env) - set(before["env"]))
    say(f"   (new keys in ravix.env: {', '.join(new_keys)})")
    svc(lambda: {"RAVIX_VENV_LINK", "RAVIX_UPDATE_JOBS_DIR", "RAVIX_UPDATE_EXECUTOR"} <= set(new_keys), "the update keys were added to ravix.env")
    check(oct(ENV_FILE.stat().st_mode & 0o777) == "0o640", "ravix.env is still mode 640")
    text = read_site_file()
    svc(lambda: len(re.findall(r"location\s+(\^~\s+)?/_plugin_files/\s*\{", text)) == 1, "exactly one /_plugin_files/ block in the nginx site (nothing was duplicated)")
    svc(lambda: sh(["nginx", "-t"], check_rc=False).returncode == 0, "nginx -t passes")
    compare_data(data0, data_fingerprint(data0), "after the migration")
    check_additive(additive0, "after the migration")
    check(sh(["runuser", "-u", "ravix", "--", "test", "-w", str(BASE / "jobs" / "queue")], check_rc=False).returncode == 0, "the web user can put a wake-up file in jobs/queue")
    check(sh(["runuser", "-u", "ravix", "--", "test", "-w", str(APP / "apps")], check_rc=False).returncode != 0, "the web user cannot write the program code")
    check(sh(["runuser", "-u", "ravix-deploy", "--", "test", "-w", str(APP / "apps")], check_rc=False).returncode == 0, "the deploy user can write the program code")
    preflight("after the migration")
    picked = re.search(r"Python: (\S+) \(Python (\d+\.\d+)", NEW_INSTALL["out"])
    venv_ver = sh([str(BASE / "venv" / "bin" / "python"), "-V"]).stdout.split()[1].rsplit(".", 1)[0]
    check(bool(picked) and picked.group(1).startswith("/usr/"), f"install.sh used a system Python ({picked.group(1) if picked else 'none found'}), not a toolcache one")
    check(bool(picked) and venv_ver == picked.group(2), f"the new virtualenv has the Python that install.sh chose ({venv_ver}); the system python3 here is {sh(['/usr/bin/python3', '-V']).stdout.split()[1]}")
    pf = site("manage.py ravix_preflight", check_rc=False).stdout
    check("DEFAULT_FROM_EMAIL" in pf and "double quotes" in pf, "preflight names the unquoted ravix.env value (key and line, not the value) and says how to fix it")
    check("ravixcms@gmail.com" not in pf, "preflight does not print the value")
    bk = sh(["runuser", "-u", "ravix", "--", "bash", str(APP / "deploy" / "backup.sh")], check_rc=False)
    check(bk.returncode == 0, f"deploy/backup.sh works with the unquoted DEFAULT_FROM_EMAIL line in ravix.env (exit {bk.returncode}) {bk.stderr[-300:]}")

    if PARTIAL:
        say("   PARTIAL run: stopped here. The real updates, the failure cases with the executor and the second install.sh need a real systemd.")
        return

    # ---------------------------------------------------------------- real updates
    say("۴) a real update through the real executor (systemd path unit)")
    key = tmp / "release.key"
    envb = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.development"}
    envb.pop("DATABASE_URL", None)
    pub = sh([sys.executable, "manage.py", "ravix_release", "keygen", "--key-file", str(key)], env=envb).stdout.strip().splitlines()[-1]
    DIST.mkdir(parents=True, exist_ok=True)
    with ENV_FILE.open("a", encoding="utf-8") as f:
        f.write(f"\nRAVIX_UPDATE_DIR={DIST}\nRAVIX_TRUSTED_PUBLIC_KEYS=e2e:{pub}\nRAVIX_UPDATE_WEB_APPLY=false\n")
    sh(["systemctl", "restart", "ravix.service"])
    check(healthy(), "the site is healthy after the settings of the test source were added")
    base_version = current_version()

    # A: a good update with a real migration
    build_release(tmp, "0.10.1", note_migration=True, key=key)
    queue_update("0.10.1")
    job = wait_job()
    check(job["status"] == "succeeded", f"update to 0.10.1 succeeded through the executor ({job})")
    check(healthy(), "the site is healthy after the update (restart + health check by the executor)")
    check(current_version() == "0.10.1", f"the site runs 0.10.1 (was {base_version})")
    check(tree_hash(BASE / "themes_installed" / "e2e-theme") == before["theme"] and tree_hash(BASE / "plugins_installed" / "e2e_plugin") == before["plugin"],
          "the external theme and plugin are still byte-identical after a real update")
    check(tree_hash(BASE / "media") == before["media"] and tree_hash(BASE / "plugin_data") == before["plugin_data"], "media and plugin_data are byte-identical after the update")
    check({k: env_pairs().get(k) for k in before["env"]} == before["env"], "the old keys of ravix.env are unchanged after the update")
    check(mcp_status() == "200", "the old MCP token still works after the update")
    data1 = data_fingerprint(data0)
    compare_data(data0, data1, "after the update")
    check_additive(additive0, "after the update")
    preflight("after the update")
    good_code = code_manifest()
    good_link = os.readlink(BASE / "venv")

    # B: a migration that fails in the middle
    build_release(tmp, "0.10.2", note_migration=True, boom="raise", key=key)
    queue_update("0.10.2")
    job = wait_job()
    check(job["status"] in ("rolled_back", "failed"), f"a failing migration ends as rolled back ({job})")
    check("migration exploded" in job["message"], "the job message names the real reason (the end of the migrate output), not just its first lines")
    check("S3CR3TPASS" not in job["message"], "the secret inside the error text is masked in the job message")
    check(healthy() and current_version() == "0.10.1", "the site is healthy and still on 0.10.1")
    code_is(good_code, "the program files are exactly as before the failed update")
    check("S3CR3TPASS" not in sh("journalctl -u ravix-update.service --no-pager", check_rc=False).stdout, "the secret inside the error text is not in the executor's log")
    compare_data(data0, data_fingerprint(data0), "after the failed migration")

    # C: the new code starts but /healthz is unhealthy
    build_release(tmp, "0.10.3", bad_health=True, key=key)
    queue_update("0.10.3")
    job = wait_job(timeout=900)
    check(job["status"] in ("rolled_back", "failed"), f"an unhealthy site after the restart is rolled back automatically ({job})")
    check(healthy() and current_version() == "0.10.1", "the site is healthy again on 0.10.1")
    code_is(good_code, "the program files are back to the previous version")
    check(os.readlink(BASE / "venv") == good_link, "the virtualenv link is back to the previous version")

    # D: a full disk (the backup folder cannot take the backup)
    build_release(tmp, "0.10.4", key=key)
    backups = Path(env_pairs().get("RAVIX_UPDATE_BACKUP_DIR") or APP / "update_backups")
    backups.mkdir(parents=True, exist_ok=True)
    sh(["mount", "-t", "tmpfs", "-o", f"size=2m,uid={int(sh(['id', '-u', 'ravix-deploy']).stdout)},gid={int(sh(['id', '-g', 'ravix']).stdout)},mode=2750", "tmpfs", str(backups)])
    try:
        queue_update("0.10.4")
        job = wait_job(timeout=600)
    finally:
        sh(["umount", str(backups)], check_rc=False)
    check(job["status"] in ("failed", "rolled_back"), f"a full backup disk stops the update ({job})")
    check(str(backups) in job["message"] and "unexpected" not in job["message"].lower(), "the message names the full folder (not \"an unexpected error\")")
    check(healthy() and current_version() == "0.10.1", "the site is healthy on 0.10.1")
    code_is(good_code, "nothing was changed on the site")

    # E: a tampered package
    pkg = next(DIST.glob("ravix-core-0.10.4*.zip"), None)
    if pkg is not None:
        with pkg.open("ab") as f:
            f.write(b"tampered")
        queue_update("0.10.4")
        job = wait_job(timeout=300)
        check(job["status"] in ("failed", "rolled_back"), f"a tampered package is refused ({job})")
        check(healthy() and current_version() == "0.10.1", "the site is healthy on 0.10.1")
        code_is(good_code, "nothing was changed on the site")
    else:
        check(False, "the 0.10.4 package was not found to tamper with")

    # F: kill -9 of the whole executor in the middle of an update
    if not args.skip_kill:
        say("۵) kill -9 of the executor in the middle of an update (recovered by the timer)")
        build_release(tmp, "0.10.5", note_migration=True, boom="sleep", key=key)
        queue_update("0.10.5")
        end = time.time() + 300
        in_migrate = False
        while time.time() < end and not in_migrate:
            time.sleep(3)
            r = site("manage.py shell -c \"from apps.updater.models import UpdateRun as R; r=R.objects.order_by('-pk').first(); print(','.join(s['name'] for s in r.steps) if r else '')\"", check_rc=False)
            in_migrate = "swap" in r.stdout and unit_running("ravix-update.service")
        check(in_migrate, "the executor reached the point after the file swap (the migration is sleeping)")
        sh(["systemctl", "kill", "-s", "SIGKILL", "ravix-update.service"], check_rc=False)
        say("   (the executor and all its children were killed with SIGKILL; waiting for the timer to recover — up to 8 minutes)")
        job = wait_job(done=("rolled_back", "failed", "succeeded", "cancelled"), timeout=540)
        check(job["status"] == "rolled_back", f"the killed update was recovered automatically ({job})")
        check(healthy(timeout=120), "the site answers again after the recovery")
        check(current_version() == "0.10.1", "the site runs 0.10.1 again")
        code_is(good_code, "files are back to 0.10.1 exactly")
        compare_data(data0, data_fingerprint(data0), "after the kill -9 recovery")
        check(tree_hash(BASE / "themes_installed" / "e2e-theme") == before["theme"] and tree_hash(BASE / "plugins_installed" / "e2e_plugin") == before["plugin"], "the external theme and plugin are still byte-identical")
        check(mcp_status() == "200", "the MCP token still works after the recovery")

    say("۶) running install.sh again changes nothing (idempotent)")
    pre_env, pre_nginx = ENV_FILE.read_text(), SITE_FILE.read_text()
    install_sh(ROOT)
    check(len(re.findall(r"location\s+(\^~\s+)?/_plugin_files/\s*\{", SITE_FILE.read_text())) == 1, "still exactly one /_plugin_files/ block after a second install.sh")
    check({k: env_pairs().get(k) for k in before["env"]} == before["env"], "ravix.env old keys unchanged after a second install.sh")
    check("RAVIX_INSTALL_TOKEN" not in env_pairs(), "an installed site never gets a new install token from a second install.sh")
    check(healthy(), "the site is healthy after a second install.sh")
    check(tree_hash(BASE / "themes_installed" / "e2e-theme") == before["theme"] and tree_hash(BASE / "plugins_installed" / "e2e_plugin") == before["plugin"], "the external theme and plugin are still byte-identical")
    del pre_env, pre_nginx


if __name__ == "__main__":
    main()
