"""The web installer for shared hosting (cPanel), used only when ``settings.RAVIX_HOSTING`` is on. The VPS installer (views.py) is separate.

Security model: the page shows nothing but a box for the one-time *install code* until the right code is typed. The code lives in a file outside
public_html (mode 600), is compared in constant time, wrong attempts are rate limited (per address and globally), the code is never written to a
log in this mode, and it is deleted when the installation ends. When the site has working HTTPS the installer is reachable only over HTTPS.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import sys
import time
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext as _

from apps.core import hosting

KEY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L: easy to read aloud and to type
MAX_FAILS_PER_IP = 5
FAIL_WINDOW = 15 * 60
MAX_FAILS_GLOBAL = 100
GLOBAL_WINDOW = 60 * 60


# ---------------------------------------------------------------- the install code
def _key_path() -> Path:
    return hosting.install_key_file()


def normalize_key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def ensure_key() -> str:
    """Return the install code, creating the file (mode 600, outside public_html) on first use. Never logged."""
    path = _key_path()
    try:
        value = path.read_text(encoding="utf-8").strip()
        if normalize_key(value):
            return value
    except OSError:
        pass
    raw = "".join(secrets.choice(KEY_ALPHABET) for _i in range(16))
    value = "-".join(raw[i:i + 4] for i in range(0, 16, 4))
    try:
        hosting.ensure_dir(path.parent)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(value + "\n")
    except FileExistsError:  # another worker was first: use its code
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    except OSError:
        pass  # unwritable folder: the host check reports it; with no file nobody can unlock (the code only exists in memory of this worker)
        _MEMORY["key"] = _MEMORY.get("key") or value
        return _MEMORY["key"]
    return value


_MEMORY: dict[str, str] = {}


def remove_key() -> None:
    try:
        _key_path().unlink()
    except OSError:
        pass
    _MEMORY.clear()


def key_matches(candidate: str) -> bool:
    expected = normalize_key(ensure_key())
    got = normalize_key(candidate)
    return bool(expected) and hmac.compare_digest(got.encode(), expected.encode())


# ---------------------------------------------------------------- rate limit (the cache is the file cache, shared by all workers)
def client_ip(request) -> str:
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def _ip_key(request) -> str:
    return "ravix:install:fail:" + re.sub(r"[^0-9a-fA-F:.]", "_", client_ip(request))


def blocked(request) -> bool:
    return (cache.get(_ip_key(request), 0) >= MAX_FAILS_PER_IP) or (cache.get("ravix:install:fail:all", 0) >= MAX_FAILS_GLOBAL)


def _bump(key: str, window: int) -> None:
    if cache.add(key, 1, window):
        return
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)


def note_failure(request) -> None:
    _bump(_ip_key(request), FAIL_WINDOW)
    _bump("ravix:install:fail:all", GLOBAL_WINDOW)


def note_success(request) -> None:
    cache.delete(_ip_key(request))


# ---------------------------------------------------------------- HTTPS-only when the site has a working certificate
def _public_dns_name(host: str) -> bool:
    if not host or len(host) > 253 or host.lower() == "localhost" or not re.fullmatch(r"[A-Za-z0-9.-]+", host) or "." not in host:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    try:
        for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
    except OSError:
        return False
    return True


def tls_works(host: str) -> bool:
    """True when ``https://host`` completes a TLS handshake with a valid certificate (checked from the server itself, cached 10 minutes)."""
    host = host.split(":")[0].lower()
    key = "ravix:install:tls:" + host
    known = cache.get(key)
    if known is not None:
        return bool(known)
    ok = False
    if _public_dns_name(host):
        try:
            with socket.create_connection((host, 443), timeout=4) as raw, ssl.create_default_context().wrap_socket(raw, server_hostname=host):
                ok = True
        except (OSError, ssl.SSLError):
            ok = False
    cache.set(key, ok, 600)
    return ok


def https_required(request) -> bool:
    """Should this plain-HTTP request be sent to HTTPS? ``RAVIX_INSTALL_FORCE_HTTPS`` = true / false / auto (default: probe the certificate)."""
    if request.is_secure():
        return False
    mode = os.environ.get("RAVIX_INSTALL_FORCE_HTTPS", "auto").strip().lower()
    if mode in {"1", "true", "yes", "on"}:
        return True
    if mode in {"0", "false", "no", "off"}:
        return False
    return tls_works(request.get_host())


# ---------------------------------------------------------------- host checks
def _memory_mb() -> int | None:
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            raw = Path(path).read_text().strip()
            if raw.isdigit() and int(raw) < 1 << 50:
                return int(raw) // (1024 * 1024)
        except OSError:
            continue
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return None


def _writable(path: Path) -> bool:
    try:
        hosting.ensure_dir(path)
        probe = path / f".w{secrets.token_hex(3)}"
        probe.write_text("x")
        probe.unlink()
        return True
    except OSError:
        return False


def _server_name() -> str:
    software = os.environ.get("SERVER_SOFTWARE", "")
    if any(k.startswith("PASSENGER") for k in os.environ) or "Passenger" in software:
        return "Phusion Passenger"
    if "LiteSpeed" in software or os.environ.get("LSWS_EDITION") or os.environ.get("LSAPI_CHILDREN"):
        return "LiteSpeed (LSAPI)"
    return software or _("unknown")


def native_help() -> str:
    return _("Your hosting seems too old for this package (it needs a “manylinux2014” or newer wheel). Ask your hosting support to update the server's Python/OS, or to install it for you, or move to a newer plan.")


def host_checks(request=None) -> list[dict]:
    """Rows of {id, status: ok|warn|error|info, title, detail}. Nothing here changes the host."""
    rows: list[dict] = []

    def add(rid, status, title, detail=""):
        rows.append({"id": rid, "status": status, "title": title, "detail": detail})

    ver = ".".join(map(str, sys.version_info[:3]))
    if hosting.python_ok():
        add("python", "ok", _("Python %(v)s") % {"v": ver})
    else:
        add("python", "error", _("Python %(v)s is too old") % {"v": ver},
            _("Ravix needs Python 3.11 or newer (Python 3.10 reached end of life and is not supported). In cPanel open “Setup Python App”, edit the application and choose a newer Python version."))

    add("server", "info", _("Application server: %(name)s") % {"name": _server_name()})

    data = hosting.data_dir()
    if _writable(data):
        add("data", "ok", _("Private data folder is writable"), str(data))
    else:
        add("data", "error", _("The private data folder is not writable"), _("Make this folder writable by your account, or create it in File Manager: %(path)s") % {"path": data})
    media = Path(settings.MEDIA_ROOT)
    if _writable(media):
        add("media", "ok", _("The media (uploads) folder is writable"), str(media))
    else:
        add("media", "warn", _("The media folder is not writable; uploading files will not work"), str(media))
    static_root = Path(settings.STATIC_ROOT)
    if _writable(static_root):
        add("static", "ok", _("The static files folder is writable"))
    else:
        add("static", "error", _("The static files folder is not writable"), str(static_root))
    if _writable(hosting.APP_ROOT / "tmp"):
        add("restart", "ok", _("The application can be restarted from the panel (tmp/restart.txt)"))
    else:
        add("restart", "warn", _("The tmp folder is not writable, so the application cannot restart itself after an update"), str(hosting.APP_ROOT / "tmp"))

    mem = _memory_mb()
    if mem is None:
        add("memory", "info", _("Memory limit could not be detected"))
    elif mem < 384:
        add("memory", "warn", _("Only %(mb)s MB of memory is available to your account") % {"mb": mem}, _("Ravix runs best with at least 512 MB. Installation may fail on such a small limit."))
    else:
        add("memory", "ok", _("Memory available: %(mb)s MB") % {"mb": mem})

    add("upload", "info", _("Upload limit"), _("Ravix itself does not limit file uploads, but your host's web server might (often 8–64 MB). If large uploads fail, ask your host to raise it."))

    for module, label in (("PIL", "Pillow"), ("cryptography", "cryptography"), ("nh3", "nh3")):
        try:
            __import__(module)
            add("wheel-" + module, "ok", _("Package %(name)s is installed") % {"name": label})
        except Exception:  # noqa: BLE001 - ImportError, or a native library that cannot load
            add("wheel-" + module, "error", _("Package %(name)s cannot be loaded") % {"name": label}, native_help())
    try:
        import pymysql  # noqa: F401

        add("pymysql", "ok", _("MySQL/MariaDB driver (PyMySQL) is installed"))
    except ImportError:
        add("pymysql", "warn", _("The MySQL/MariaDB driver (PyMySQL) is not installed"), _("Install the requirements again (pip install -r requirements-cpanel.txt) or choose SQLite."))

    secure = bool(request and request.is_secure())
    if secure:
        add("ssl", "ok", _("This page is served over HTTPS"))
    else:
        add("ssl", "warn", _("This page is served over plain HTTP"), _("Turn on AutoSSL / Let’s Encrypt in cPanel (SSL/TLS Status) so logins are encrypted. Ravix switches to HTTPS automatically once the certificate works."))
    return rows


def has_blockers(rows: list[dict]) -> bool:
    return any(r["status"] == "error" for r in rows)


# ---------------------------------------------------------------- database
def database_url(cfg: dict) -> str:
    engine = cfg["engine"]
    if engine == "sqlite":
        return ""  # the hosting settings put the file in the private data folder
    scheme = "mysql" if engine == "mysql" else "postgres"
    return f"{scheme}://{quote(cfg['user'], safe='')}:{quote(cfg['password'], safe='')}@{cfg['host']}:{cfg['port']}/{quote(cfg['name'], safe='')}"


def test_database(cfg: dict) -> tuple[bool, str, list[str]]:
    """(ok, error message, notes). Connects with the driver directly (Django's own settings do not know this database yet)."""
    notes: list[str] = []
    engine = cfg["engine"]
    try:
        if engine == "sqlite":
            data = hosting.data_dir() / "db"
            if not _writable(data):
                return False, _("The folder for the SQLite file is not writable: %(path)s") % {"path": data}, notes
            notes.append(_("SQLite is fine for a small site; for larger sites, or many editors at once, a MySQL/MariaDB database from cPanel is better."))
            return True, "", notes
        if engine == "mysql":
            import pymysql

            conn = pymysql.connect(host=cfg["host"], port=int(cfg["port"]), user=cfg["user"], password=cfg["password"], database=cfg["name"],
                                   charset="utf8mb4", connect_timeout=8)
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT VERSION()")
                    version = str(cur.fetchone()[0])
                    maria = "mariadb" in version.lower()
                    nums = tuple(int(x) for x in re.findall(r"\d+", version)[:3])
                    if (maria and nums < (10, 5)) or (not maria and nums < (8, 0)):
                        return False, _("This database server (%(v)s) is too old. Ravix needs MariaDB 10.5+ or MySQL 8.0+.") % {"v": version}, notes
                    cur.execute("SELECT @@character_set_database")
                    charset = str(cur.fetchone()[0])
                    if charset != "utf8mb4":
                        try:
                            cur.execute(f"ALTER DATABASE `{cfg['name'].replace('`', '')}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                            notes.append(_("The database character set was changed to utf8mb4."))
                        except Exception:  # noqa: BLE001
                            return False, _("The database uses the character set “%(cs)s”. Create it again with the collation utf8mb4_unicode_ci (cPanel ▸ MySQL Databases).") % {"cs": charset}, notes
                    cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE()")
                    if int(cur.fetchone()[0]):
                        notes.append(_("The database is not empty. Existing tables with the same names as Ravix's will be reused: use an empty database for a fresh site."))
            finally:
                conn.close()
            return True, "", notes
        import psycopg

        with psycopg.connect(host=cfg["host"], port=int(cfg["port"]), user=cfg["user"], password=cfg["password"], dbname=cfg["name"], connect_timeout=8):
            pass
        return True, "", notes
    except ImportError:
        return False, _("The database driver for this choice is not installed on the host."), notes
    except Exception as exc:  # noqa: BLE001 - the driver's message is helpful and holds no secret (it never echoes the password)
        text = re.sub(r"\s+", " ", str(exc))[:200]
        if cfg.get("password"):
            text = text.replace(cfg["password"], "***")
        return False, _("Could not connect to the database: %(err)s") % {"err": text}, notes


# ---------------------------------------------------------------- the installation job (runs detached; see ravix_host_install)
def jobs_dir() -> Path:
    return hosting.ensure_dir(hosting.data_dir() / "setup-jobs")


def status_path() -> Path:
    return jobs_dir() / "status.json"


def read_status() -> dict:
    try:
        return json.loads(status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_status(**fields) -> dict:
    current = read_status()
    current.update(fields)
    current["updated"] = time.time()
    tmp = status_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(current), encoding="utf-8")
    os.replace(tmp, status_path())
    return current


STEPS = ("database", "static", "admin", "settings", "finish")


def start_job(payload: dict) -> None:
    """Write the job file (mode 600; it holds passwords and is deleted by the job as soon as it is read) and start the job detached."""
    import subprocess

    path = jobs_dir() / f"job-{secrets.token_hex(6)}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    write_status(state="running", step="", done_steps=[], error="", started=time.time(), pid=0)
    env = dict(os.environ, DJANGO_SETTINGS_MODULE=hosting.SETTINGS_MODULE)
    log = open(jobs_dir() / "job.log", "ab")  # noqa: SIM115 - handed to the child process
    proc = subprocess.Popen([hosting.python_executable(), str(hosting.APP_ROOT / "manage.py"), "ravix_host_install", "--job", str(path)],
                            cwd=str(hosting.APP_ROOT), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
    log.close()
    write_status(pid=proc.pid)


def job_alive(status: dict) -> bool:
    pid = int(status.get("pid") or 0)
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie (finished, not yet reaped by this worker) is not alive
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except (OSError, IndexError):
        return True


def manual_command() -> str:
    return f"cd {hosting.APP_ROOT} && DJANGO_SETTINGS_MODULE={hosting.SETTINGS_MODULE} {hosting.python_executable()} manage.py ravix_host_install --resume"
