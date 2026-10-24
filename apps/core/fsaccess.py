"""Can this process really read a folder? (used by preflight, the site-health page and plugin/theme discovery)

``os.access`` and a plain ``is_dir`` say little about a folder that is ``root:ravix`` mode 750: what matters is the groups the
*running process* has (primary + supplementary, set by systemd's ``Group=`` / ``SupplementaryGroups=``), not the groups of the
account that happens to run ``manage.py`` from a shell. So the check actually lists the folder and, when that fails, says which
user and groups the process has and who owns the folder, which is what the operator needs to fix it.
"""
from __future__ import annotations

import errno
import os
from pathlib import Path

from .clilang import cli_gettext


def _name_of(kind: str, ident: int) -> str:
    try:
        if kind == "user":
            import pwd

            return pwd.getpwuid(ident).pw_name
        import grp

        return grp.getgrgid(ident).gr_name
    except (ImportError, KeyError):
        return str(ident)


def process_identity() -> str:
    """``ravix (groups: www-data)``: the effective user and every group of this process, by name."""
    if not hasattr(os, "geteuid"):
        return ""
    groups = {os.getegid(), *os.getgroups()}
    names = ", ".join(sorted(_name_of("group", g) for g in groups))
    return f"{_name_of('user', os.geteuid())} (groups: {names})"


def owner_text(path: Path) -> str:
    try:
        st = os.stat(path)
    except OSError:
        return "?"
    return f"{_name_of('user', st.st_uid)}:{_name_of('group', st.st_gid)} {st.st_mode & 0o7777:o}"


def list_error(path) -> str:
    """"" when the process can list ``path``; otherwise a short operator-facing reason (not translated for the web: callers wrap it)."""
    try:
        with os.scandir(path) as it:
            next(it, None)
    except FileNotFoundError:
        return "missing"
    except NotADirectoryError:
        return "not a directory"
    except PermissionError:
        return "denied"
    except OSError as exc:
        return errno.errorcode.get(exc.errno or 0, "error")
    return ""


def unreadable_detail(path) -> str:
    """Operator text (CLI language) for a folder that cannot be listed, with the process's identity and the folder's owner."""
    return cli_gettext("Cannot list %(path)s: the service runs as %(who)s but the folder is %(owner)s. Add the folder's group to the unit (SupplementaryGroups=ravix in ravix.service) and restart.") % {
        "path": path, "who": process_identity(), "owner": owner_text(Path(path))}
