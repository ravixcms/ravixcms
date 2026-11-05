"""Restart the application and confirm that the new version really answers — the two things an update must do before it may call itself done.

The executor never restarts anything with its own rights. On a VPS it drops a request file that a tiny root-owned systemd path unit turns into the one fixed
command ``systemctl restart ravix`` (nothing from the file is executed). On shared hosting the application server is asked to start a fresh process with
``tmp/restart.txt``. After the restart ``/healthz`` has to answer ``ok`` within ``RAVIX_HEALTH_TIMEOUT`` seconds; if it does not, the caller rolls back.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.utils.translation import gettext

REQUEST_NAME = "restart-request"


class Lifecycle:
    """Does not restart (the owner does); ``automatic`` is False so the pipeline neither waits nor checks."""

    name = "none"
    automatic = False

    def restart(self) -> bool:  # pragma: no cover - interface
        return False

    def wait_healthy(self, timeout: float | None = None) -> tuple[bool, str]:  # pragma: no cover - interface
        return True, ""


def _timeout(timeout):
    return float(timeout if timeout is not None else getattr(settings, "RAVIX_HEALTH_TIMEOUT", 60))


def _poll(probe, timeout: float, interval: float = 1.0) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout
    last = gettext("no answer")
    while True:
        ok, detail = probe()
        if ok:
            return True, detail
        last = detail or last
        if time.monotonic() >= deadline:
            return False, last
        time.sleep(interval)


def _is_ok(status: int, body: bytes) -> tuple[bool, str]:
    try:
        good = status == 200 and json.loads(body.decode("utf-8", "replace")).get("status") == "ok"
    except ValueError:
        good = False
    return good, f"HTTP {status}"


def _host_header() -> str:
    for host in getattr(settings, "ALLOWED_HOSTS", []) or []:
        if host and not host.startswith((".", "*")) and host not in ("localhost", "127.0.0.1"):
            return host
    return "localhost"


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


def probe_unix(path: str) -> tuple[bool, str]:
    conn = _UnixConnection(path, 5)
    try:
        conn.request("GET", "/healthz", headers={"Host": _host_header(), "X-Forwarded-Proto": "https"})
        resp = conn.getresponse()
        return _is_ok(resp.status, resp.read(2000))
    except (OSError, http.client.HTTPException) as exc:
        return False, type(exc).__name__
    finally:
        conn.close()


def probe_url(url: str) -> tuple[bool, str]:
    if urlsplit(url).scheme not in ("http", "https"):
        return False, gettext("The health-check address must be http(s).")
    req = urllib.request.Request(url, headers={"User-Agent": "Ravix-updater/1.0", "X-Forwarded-Proto": "https"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310  # nosec B310 - the owner's own address, scheme checked above
            return _is_ok(resp.status, resp.read(2000))
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (OSError, ValueError) as exc:
        return False, type(exc).__name__


def health_probe():
    """The function that asks /healthz once: an explicit URL, else the Gunicorn socket (VPS), else the site's own address (hosting)."""
    explicit = (getattr(settings, "RAVIX_HEALTHZ_URL", "") or "").strip()
    if explicit:
        return lambda: probe_url(explicit)
    bind = os.environ.get("GUNICORN_BIND", "unix:/run/ravix/gunicorn.sock")
    if bind.startswith("unix:") and not getattr(settings, "RAVIX_HOSTING", False):
        return lambda: probe_unix(bind[5:])
    site = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
    if site:
        return lambda: probe_url(site + "/healthz")
    return lambda: (False, gettext("No health-check address is configured (RAVIX_HEALTHZ_URL)."))


class SystemdLifecycle(Lifecycle):
    """VPS. The restart is a request file; a root path unit runs ``systemctl restart ravix`` and removes the file when it is done."""

    name = "systemd"
    automatic = True

    def __init__(self, jobs_dir: Path | None = None, *, wait_seconds: float = 90):
        self.dir = Path(jobs_dir or settings.RAVIX_UPDATE_JOBS_DIR)
        self.wait_seconds = wait_seconds

    def restart(self) -> bool:
        self.dir.mkdir(parents=True, exist_ok=True)
        request = self.dir / REQUEST_NAME
        request.write_text(str(int(time.time())) + "\n", encoding="utf-8")
        deadline = time.monotonic() + self.wait_seconds
        while request.exists():  # the root unit removes it after the restart command returned
            if time.monotonic() > deadline:
                request.unlink(missing_ok=True)
                return False
            time.sleep(0.5)
        return True

    def wait_healthy(self, timeout: float | None = None) -> tuple[bool, str]:
        return _poll(health_probe(), _timeout(timeout))


class TouchLifecycle(Lifecycle):
    """Shared hosting: Passenger / LiteSpeed start a new process when ``tmp/restart.txt`` changes."""

    name = "touch"
    automatic = True

    def restart(self) -> bool:
        from apps.core import hosting

        return hosting.restart_app()

    def wait_healthy(self, timeout: float | None = None) -> tuple[bool, str]:
        return _poll(health_probe(), _timeout(timeout), interval=2.0)


def get() -> Lifecycle:
    mode = (getattr(settings, "RAVIX_UPDATE_RESTART", "") or "").strip().lower()
    if mode == "systemd":
        return SystemdLifecycle()
    if mode == "touch":
        return TouchLifecycle()
    return Lifecycle()
