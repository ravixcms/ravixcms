"""``manifest.json`` of a core release: the SHA-256 of every file the package carries. It is checked when the package is validated (so a file
cannot be swapped inside a signed zip without anyone noticing a mismatch with the manifest) and it stays on disk next to ``manage.py`` as the
list of files that belong to the installed version — which is how an update can tell the owner's own files from the release's files."""
import hashlib
import json
from pathlib import Path

from django.utils.translation import gettext

from .errors import UpdateError

MANIFEST_FILE = "manifest.json"
SCHEMA = 1
SKIP_PARTS = {"__pycache__"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
NOT_LISTED = {MANIFEST_FILE, "release.json"}  # the manifest cannot contain itself; release.json is the package's own descriptor


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def scan(root: Path, entries) -> dict[str, str]:
    """{relative posix path: sha256} of every regular file under ``entries`` (names relative to ``root``; a name can be a file or a folder)."""
    out = {}
    for entry in entries:
        base = Path(root, entry)
        files = [base] if base.is_file() else sorted(base.rglob("*")) if base.is_dir() else []
        for p in files:
            if not p.is_file() or p.is_symlink() or SKIP_PARTS & set(p.parts) or p.suffix in SKIP_SUFFIXES:
                continue
            rel = p.relative_to(root).as_posix()
            if rel not in NOT_LISTED:
                out[rel] = _sha256(p)
    return dict(sorted(out.items()))


def dumps(files: dict[str, str]) -> bytes:
    return json.dumps({"schema": SCHEMA, "files": files}, indent=1, sort_keys=True).encode()


def read(root: Path) -> dict[str, str] | None:
    """The file list of ``root/manifest.json``; None when it does not exist or is not a manifest."""
    try:
        doc = json.loads(Path(root, MANIFEST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    files = doc.get("files") if isinstance(doc, dict) and doc.get("schema") == SCHEMA else None
    if not isinstance(files, dict) or not all(isinstance(k, str) and isinstance(v, str) and len(v) == 64 for k, v in files.items()):
        return None
    return files


def verify_extracted(root: Path, entries) -> int:
    """The extracted package must match its manifest exactly: no file missing, none extra, every hash equal. Returns the file count."""
    listed = read(root)
    if listed is None:
        raise UpdateError(gettext("manifest.json is missing from the package or invalid."))
    actual = scan(root, entries)
    for rel in sorted(set(listed) - set(actual)):
        raise UpdateError(gettext("The manifest lists a file that is not in the package: %(rel)s") % {"rel": rel})
    for rel in sorted(set(actual) - set(listed)):
        raise UpdateError(gettext("The package has a file the manifest does not list: %(rel)s") % {"rel": rel})
    for rel, sha in listed.items():
        if actual[rel] != sha:
            raise UpdateError(gettext("The file %(rel)s does not match the manifest.") % {"rel": rel})
    return len(listed)


def compare(app_root: Path, entries, new: dict[str, str], old: dict[str, str] | None) -> dict[str, list[str]]:
    """What an update would overwrite or delete that belongs to the owner, not to the release.

    ``modified``: a file of the installed release that was edited (needs the installed manifest ``old``).
    ``added``: a file in a managed folder that no release has (with ``old``) / that the new release does not have (without it: ``extra``).
    Files of the old release the new one dropped are normal and not reported."""
    actual = scan(app_root, entries)
    modified, added = [], []
    for rel, sha in actual.items():
        if old is None:
            if rel not in new:
                added.append(rel)
        elif rel in old:
            if old[rel] != sha:
                modified.append(rel)
        elif rel not in new:
            added.append(rel)
    return {"modified": modified, "added": added, "known_baseline": old is not None}


def local_report(app_root: Path, entries) -> dict[str, list[str]] | None:
    """Files in the managed folders that an update would overwrite or delete because they are the owner's: edited release files and extra files.
    Needs the installed ``manifest.json`` (None for an install from before 0.10.0, which has none)."""
    old = read(app_root)
    if old is None:
        return None
    result = compare(app_root, entries, {}, old)
    return {"modified": result["modified"], "added": result["added"]}
