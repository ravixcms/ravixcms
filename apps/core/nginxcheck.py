"""Does nginx hand private plugin files over? A read-only look at the nginx configuration files.

With ``RAVIX_ACCEL_REDIRECT_PREFIX`` set the application answers a private plugin download with ``X-Accel-Redirect: <prefix>…`` and nginx must have an
``internal`` location with that prefix whose ``alias`` is ``RAVIX_PLUGIN_DATA_DIR``. Without it every such download turns into a 404. A live probe is not
possible (an internal location answers 404 to everyone from outside), so the configuration is read instead; if it cannot be found or read the answer is "unknown".
"""
from __future__ import annotations

import glob
import os
import re
from pathlib import Path

OK, MISSING, NOT_INTERNAL, WRONG_ALIAS, UNKNOWN = "ok", "missing", "not_internal", "wrong_alias", "unknown"

DEFAULT_PATTERNS = ("/etc/nginx/sites-enabled/*", "/etc/nginx/conf.d/*.conf", "/etc/nginx/nginx.conf")


def config_text(patterns=None) -> str | None:
    """All readable nginx configuration files joined, or None when none was found (nginx elsewhere, in a container, or not readable)."""
    if patterns is None:
        override = os.environ.get("RAVIX_NGINX_CONFIG", "").strip()
        patterns = tuple(p for p in override.split(":") if p) if override else DEFAULT_PATTERNS
    parts: list[str] = []
    for pattern in patterns:
        for name in sorted(glob.glob(pattern)):
            try:
                path = Path(name)
                if path.is_file():
                    parts.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    return "\n".join(parts) if parts else None


def plugin_files_status(prefix: str, data_dir, text: str | None) -> str:
    prefix = (prefix or "").strip()
    if not prefix or text is None:
        return UNKNOWN
    code = re.sub(r"(?m)#.*$", "", text)  # comments out
    blocks = re.findall(r"location\s+(?:\^~\s+)?" + re.escape(prefix) + r"\s*\{([^}]*)\}", code)
    if not blocks:
        return MISSING
    wanted = str(data_dir).rstrip("/") + "/"
    for block in blocks:
        if not re.search(r"(?m)^\s*internal\s*;", block):
            continue
        alias = re.search(r"(?m)^\s*alias\s+([^;\s]+)\s*;", block)
        if alias and alias.group(1).rstrip("/") + "/" == wanted:
            return OK
    for block in blocks:
        if re.search(r"(?m)^\s*internal\s*;", block):
            return WRONG_ALIAS
    return NOT_INTERNAL


def current_status() -> str:
    from django.conf import settings

    prefix = getattr(settings, "RAVIX_ACCEL_REDIRECT_PREFIX", "") or ""
    if not prefix:
        return UNKNOWN
    return plugin_files_status(prefix, settings.RAVIX_PLUGIN_DATA_DIR, config_text())
