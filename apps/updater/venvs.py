"""The Python environment of an update: which packages change, where the new ones are installed, and how the switch (and the way back) is done.

* **VPS** (``RAVIX_VENV_LINK`` is a symlink such as ``/srv/ravix/venv``): when the new release pins other packages, a *new* virtualenv ``venv-<version>`` is
  built next to the old one with ``pip --require-hashes --only-binary=:all:`` from the release's ``requirements.hashes.lock``. The running site is not touched;
  the new code is checked and migrated with the new environment, and only then the symlink is switched (one atomic rename). The way back is the same rename.
* **Shared hosting** (the Python app's virtualenv is fixed by cPanel): if the release changes packages, the signed package must carry a ``wheelhouse/`` with the
  wheels; they are installed with ``--no-index --find-links --require-hashes``. If no wheel fits, the update stops *before* any change and the owner is told
  to install the full ZIP instead.
* Anything else (a development checkout, a container image): the packages are not managed here, exactly as before.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.utils.translation import gettext

from . import deps
from .errors import UpdateBlocked
from .logs import sanitize

VERSIONED, INPLACE, LEGACY, NONE = "versioned", "inplace", "legacy", "none"
VENV_NAME = re.compile(r"^venv-[A-Za-z0-9][A-Za-z0-9._+-]*$")
MIN_FREE_BYTES = 600 * 1024 * 1024
PIP_TIMEOUT = 1500


def link_path() -> Path | None:
    raw = getattr(settings, "RAVIX_VENV_LINK", "") or ""
    return Path(raw) if raw else None


def mode() -> str:
    if getattr(settings, "RAVIX_HOSTING", False):
        return INPLACE
    link = link_path()
    if link is None:
        return NONE
    if link.is_symlink() or not link.exists():
        return VERSIONED
    return LEGACY  # a real folder: install.sh has not moved this server to the versioned layout yet


def manages_packages() -> bool:
    """True when this install can change its own packages (so the package list of the new release is installed, not just checked)."""
    return mode() in (VERSIONED, INPLACE)


@dataclass
class DepsPlan:
    mode: str = NONE
    changes: list = field(default_factory=list)  # [(name, installed, wanted)]
    python: Path | None = None  # the interpreter that checks and migrates the new code (the new environment when one was built)
    new_dir: Path | None = None  # VERSIONED: the freshly built environment
    old_target: str = ""  # VERSIONED: what the link pointed to before
    wheelhouse: Path | None = None  # INPLACE: the wheels inside the package
    hash_lock: Path | None = None
    switched: bool = False
    installed_inplace: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.changes)

    def describe(self) -> str:
        return ", ".join(f"{n} {a or '-'}→{b}" for n, a, b in self.changes[:8]) + (" …" if len(self.changes) > 8 else "")

    def as_dict(self) -> dict:
        """What is kept in the run's backup record so that a rollback or ``recover`` can undo the switch even from another process."""
        return {"mode": self.mode, "old_target": self.old_target, "new_dir": self.new_dir.name if self.new_dir else "", "link": str(link_path() or "")}


def installed_of(python: Path | str | None = None) -> dict[str, str]:
    """{normalized name: version} of the environment ``python`` belongs to (the running one by default)."""
    if python is None or str(python) == sys.executable:
        return deps.installed_versions()
    code = "import importlib.metadata as m, json; print(json.dumps({d.metadata['Name']: d.version for d in m.distributions() if d.metadata['Name']}))"
    r = subprocess.run([str(python), "-I", "-c", code], capture_output=True, text=True, timeout=120)  # noqa: S603  # nosec B603
    if r.returncode != 0:
        return {}
    return {deps.norm(k): v for k, v in json.loads(r.stdout).items()}


def _run_pip(python: Path, args: list[str], *, cwd: Path | None = None) -> tuple[bool, str]:
    env = {**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INPUT": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    for key in ("PIP_REQUIRE_VIRTUALENV", "PIP_TARGET", "PIP_USER"):  # the owner's mirror settings (PIP_INDEX_URL…) are kept: some servers can only reach a local mirror
        env.pop(key, None)
    try:
        r = subprocess.run([str(python), "-m", "pip", *args], capture_output=True, text=True, timeout=PIP_TIMEOUT, env=env, cwd=cwd)  # noqa: S603  # nosec B603
    except subprocess.TimeoutExpired:
        return False, gettext("pip did not finish in time.")
    return r.returncode == 0, sanitize((r.stdout or "") + (r.stderr or ""), 1500)


def _free(path: Path) -> int:
    return shutil.disk_usage(path).free


def prepare(tree: Path, to_version: str, *, build: bool = True) -> DepsPlan:
    """Decide what the new release needs and, when something must be built, build it *now* — before any file of the site is changed.
    Raises UpdateBlocked when the packages cannot be provided (nothing has been changed then)."""
    plan = DepsPlan(mode=mode(), python=Path(sys.executable))
    if plan.mode == NONE:
        return plan
    lock_file, hash_file = Path(tree, "requirements.lock"), Path(tree, "requirements.hashes.lock")
    if not lock_file.is_file() or not hash_file.is_file():
        return plan  # a release from before the hash locks: nothing to install from
    try:
        plan.changes = deps.changes(lock_file.read_text(encoding="utf-8"), installed_of())
    except ValueError as exc:
        raise UpdateBlocked(gettext("A line in the dependency lock files is not valid: %(line)s") % {"line": str(exc)[:80]}) from None
    plan.hash_lock = hash_file
    if not plan.changes:
        return plan
    if plan.mode == LEGACY:
        raise UpdateBlocked(gettext("This release needs other Python packages, and this server is not yet in the layout that can switch them safely. Run “sudo ./install.sh” from the new release once (it keeps your data) and try again."))
    if plan.mode == INPLACE:
        return _prepare_inplace(plan, tree)
    if not build:
        return plan  # a dry run only reports what would change
    return _prepare_versioned(plan, hash_file, to_version)


def _prepare_versioned(plan: DepsPlan, hash_file: Path, to_version: str) -> DepsPlan:
    link = link_path()
    assert link is not None  # noqa: S101 - mode() is VERSIONED only with a link
    target = link.parent / f"venv-{to_version}"
    if not VENV_NAME.match(target.name):
        raise UpdateBlocked(gettext("The version %(version)s cannot be used as a folder name.") % {"version": to_version})
    plan.old_target = os.readlink(link) if link.is_symlink() else ""
    if _free(link.parent) < MIN_FREE_BYTES:
        raise UpdateBlocked(gettext("Not enough disk space to build the new Python environment (about 600 MB are needed)."))
    if target.exists():
        if target.resolve() == link.resolve():
            raise UpdateBlocked(gettext("The environment for this version is already the active one."))
        shutil.rmtree(target)  # a leftover of an earlier attempt that never became active
    r = subprocess.run([sys.executable, "-m", "venv", str(target)], capture_output=True, text=True, timeout=300)  # noqa: S603  # nosec B603
    if r.returncode != 0:
        shutil.rmtree(target, ignore_errors=True)
        raise UpdateBlocked(gettext("The new Python environment could not be created: %(out)s") % {"out": sanitize(r.stdout + r.stderr, 400)})
    py = target / "bin" / "python"
    ok, out = _run_pip(py, ["install", "--require-hashes", "--only-binary=:all:", "-r", str(hash_file)])
    if ok:
        ok, out = _run_pip(py, ["check"])
    if not ok:
        shutil.rmtree(target, ignore_errors=True)
        raise UpdateBlocked(gettext("The new packages could not be installed (nothing was changed on your site): %(out)s") % {"out": out})
    plan.new_dir, plan.python = target, py
    return plan


def _prepare_inplace(plan: DepsPlan, tree: Path) -> DepsPlan:
    wheels = Path(tree, "wheelhouse")
    msg = gettext("This release needs newer Python packages that this hosting update does not carry. Nothing was changed. Install the full cPanel ZIP of the new version instead; your data stay as they are.")
    if not wheels.is_dir() or not any(wheels.glob("*.whl")):
        raise UpdateBlocked(msg)
    ok, out = _run_pip(Path(sys.executable), ["install", "--dry-run", "--no-index", "--find-links", str(wheels), "--require-hashes", "--only-binary=:all:", "-r", str(plan.hash_lock)])
    if not ok:
        raise UpdateBlocked(msg + " (" + sanitize(out, 300) + ")")
    plan.wheelhouse = wheels
    return plan


def install_wheelhouse(plan: DepsPlan) -> None:
    """Shared hosting: the real install, after the dry run succeeded. Raises UpdateBlocked-free ``RuntimeError`` text through UpdateError at the call site."""
    from .errors import UpdateError

    assert plan.wheelhouse is not None and plan.hash_lock is not None  # noqa: S101
    ok, out = _run_pip(Path(sys.executable), ["install", "--no-index", "--find-links", str(plan.wheelhouse), "--require-hashes", "--only-binary=:all:", "-r", str(plan.hash_lock)])
    if ok:
        ok, out = _run_pip(Path(sys.executable), ["check"])
    if not ok:
        raise UpdateError(gettext("The new packages could not be installed: %(out)s") % {"out": out})
    plan.installed_inplace = True


# ------------------------------------------------------------------ the symlink
def switch_link(link: Path, target_name: str) -> None:
    """Point ``link`` at the sibling folder ``target_name`` in one atomic rename (a relative target, so the whole folder can be moved)."""
    tmp = link.with_name(link.name + ".switching")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(target_name, tmp)
    os.replace(tmp, link)


def switch(plan: DepsPlan) -> None:
    link = link_path()
    assert link is not None and plan.new_dir is not None  # noqa: S101
    switch_link(link, plan.new_dir.name)
    plan.switched = True


def switch_back(info: dict) -> bool:
    """Undo a switch from the record ``DepsPlan.as_dict`` left in the run. True when the link points at the old environment afterwards."""
    link, old = Path(info.get("link") or ""), info.get("old_target") or ""
    if not info.get("new_dir") or not old or not str(link):
        return True
    if not (link.parent / old).is_dir():
        return False
    if link.is_symlink() and os.readlink(link) == old:
        return True
    switch_link(link, old)
    return True


def discard_new(info: dict) -> None:
    """Remove the environment a failed update built (never the one the link points at)."""
    link, name = Path(info.get("link") or ""), info.get("new_dir") or ""
    if not name or not VENV_NAME.match(name) or not str(link):
        return
    d = link.parent / name
    if d.is_dir() and not (link.is_symlink() and os.readlink(link) == name):
        shutil.rmtree(d, ignore_errors=True)


def prune(keep: int = 2) -> list[str]:
    """After a successful update keep the active environment and the previous one(s); delete the rest. Returns the deleted names."""
    link = link_path()
    if link is None or not link.is_symlink():
        return []
    active = os.readlink(link)
    others = sorted((p for p in link.parent.iterdir() if p.is_dir() and not p.is_symlink() and VENV_NAME.match(p.name) and p.name != active),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    gone = []
    for old in others[max(keep - 1, 0):]:
        shutil.rmtree(old, ignore_errors=True)
        gone.append(old.name)
    return gone
