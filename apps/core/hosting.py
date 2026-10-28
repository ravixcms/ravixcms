"""Shared-hosting (cPanel) mode.

The mode is on only when ``config.settings.hosting`` is the settings module. ``manage.py`` and ``passenger_wsgi.py`` choose it when the
marker file ``ravix-hosting.marker`` sits next to them (it is shipped in the cPanel package only) or ``RAVIX_HOSTING=shared`` is set.
A VPS install never has the marker, so nothing here changes how it behaves.

This module must stay importable before Django settings exist (settings/hosting.py uses it): no Django imports at module level.
"""
from __future__ import annotations

import os
import re
import secrets
import stat
import sys
from pathlib import Path

MARKER = "ravix-hosting.marker"
MIN_PYTHON = (3, 11)
SETTINGS_MODULE = "config.settings.hosting"

APP_ROOT = Path(__file__).resolve().parent.parent.parent


def wanted() -> bool:
    """True when this checkout is a cPanel/shared-hosting install."""
    return (APP_ROOT / MARKER).exists() or (os.environ.get("RAVIX_HOSTING", "").strip().lower() in {"shared", "cpanel", "1", "true"})


def choose_settings(default: str) -> str:
    return SETTINGS_MODULE if wanted() else default


def is_active() -> bool:
    """True when the running process uses the hosting settings."""
    return os.environ.get("DJANGO_SETTINGS_MODULE") == SETTINGS_MODULE


# ---------------------------------------------------------------- folders
def data_dir() -> Path:
    """Everything private lives here, outside the code folder and outside public_html (so updates never touch it)."""
    explicit = os.environ.get("RAVIX_DATA_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return APP_ROOT.parent / f"{APP_ROOT.name}-data"


def env_file() -> Path:
    explicit = os.environ.get("RAVIX_ENV_FILE", "").strip()
    return Path(explicit).expanduser() if explicit else data_dir() / "ravix.env"


def lock_file() -> Path:
    return data_dir() / "installed.lock"


def install_key_file() -> Path:
    return data_dir() / "install-key.txt"


def public_html() -> Path | None:
    explicit = os.environ.get("RAVIX_PUBLIC_HTML", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    candidate = Path.home() / "public_html"
    return candidate if candidate.is_dir() else None


def ensure_dir(path: Path, mode: int = 0o700) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    return path


# ---------------------------------------------------------------- env file (KEY=value, mode 600)
_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        key = key.strip()
        raw = raw.strip()
        if not _KEY.match(key):
            continue
        if len(raw) >= 2 and raw[0] == raw[-1] == '"':
            raw = raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        elif len(raw) >= 2 and raw[0] == raw[-1] == "'":
            raw = raw[1:-1]
        values[key] = raw
    return values


def quote_env(value: str) -> str:
    if value == "" or re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def read_env_file(path: Path | None = None) -> dict[str, str]:
    try:
        return parse_env((path or env_file()).read_text(encoding="utf-8"))
    except OSError:
        return {}


def write_env_file(values: dict[str, str], path: Path | None = None) -> Path:
    """Write the env file atomically with mode 600 (the file holds the secret key and the database password)."""
    path = path or env_file()
    ensure_dir(path.parent)
    for key, value in values.items():
        if not _KEY.match(key) or "\n" in str(value) or "\r" in str(value):
            raise ValueError(f"bad env entry: {key!r}")
    body = "# Ravix settings (private: never put this file inside public_html)\n" + "".join(f"{k}={quote_env(str(v))}\n" for k, v in values.items())
    tmp = path.with_name(path.name + f".{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.replace(tmp, path)
    if os.name == "posix":
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return path


def load_env_into_environ() -> dict[str, str]:
    """Real environment variables win over the file (so a host panel's own variables can override it)."""
    values = read_env_file()
    for key, value in values.items():
        os.environ.setdefault(key, value)
    return values


# ---------------------------------------------------------------- install state
def installed_lock_exists() -> bool:
    try:
        return lock_file().is_file()
    except OSError:
        return False


def write_install_lock() -> None:
    ensure_dir(data_dir())
    fd = os.open(lock_file(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("installed\n")


def python_ok() -> bool:
    return sys.version_info[:2] >= MIN_PYTHON


# ---------------------------------------------------------------- restart / cron
def restart_app() -> bool:
    """Ask the application server to start a fresh process: Passenger and LiteSpeed (LSAPI) both watch ``tmp/restart.txt``."""
    try:
        tmp = ensure_dir(APP_ROOT / "tmp", 0o755)
        (tmp / "restart.txt").touch()
        wsgi = APP_ROOT / "passenger_wsgi.py"
        if wsgi.exists():
            os.utime(wsgi, None)
        return True
    except OSError:
        return False


def python_executable() -> str:
    return sys.executable or "python3"


def cron_command(app_root: Path | None = None) -> str:
    """The line to paste into cPanel ▸ Cron Jobs (every 5 minutes)."""
    root = app_root or APP_ROOT
    return f"*/5 * * * * cd {root} && DJANGO_SETTINGS_MODULE={SETTINGS_MODULE} {python_executable()} manage.py ravix_cron >/dev/null 2>&1"
