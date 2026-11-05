"""Dependency locks of a release: ``requirements.lock`` (exact pins) and ``requirements.hashes.lock`` (the same pins with the SHA-256 of every
file PyPI publishes for them). A signed release carries both, so the chain is: signature → package → hashes → every installed wheel."""
import re
from importlib import metadata

from django.utils.translation import gettext

_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9.+!_-]+)$")
_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")


def norm(name: str) -> str:
    """PEP 503 name: ``Django`` and ``django``, ``jalali_core`` and ``jalali-core`` are the same package."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock(text: str) -> dict[str, str]:
    """{normalized name: version} of a plain ``name==version`` file. Anything that is not an exact pin raises ValueError."""
    out = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = _PIN.match(line)
        if not m:
            raise ValueError(line)
        out[norm(m.group(1))] = m.group(2)
    return out


def parse_hash_lock(text: str) -> dict[str, tuple[str, list[str]]]:
    """{normalized name: (version, [sha256, …])}. Continuation lines (``\\``) are joined first."""
    out = {}
    for entry in re.sub(r"\\\s*\n", " ", text).splitlines():
        entry = entry.split(" #", 1)[0].strip()
        if not entry or entry.startswith("#"):
            continue
        head = entry.split(" ", 1)[0]
        m = _PIN.match(head)
        if not m:
            raise ValueError(entry[:80])
        out[norm(m.group(1))] = (m.group(2), _HASH.findall(entry))
    return out


def problems(lock_text: str, hash_text: str) -> list[str]:
    """What is wrong with a release's two lock files (empty list = consistent): bad syntax, a pin without hashes, a version that differs, an extra package."""
    try:
        lock = parse_lock(lock_text)
        hashed = parse_hash_lock(hash_text)
    except ValueError as exc:
        return [gettext("A line in the dependency lock files is not valid: %(line)s") % {"line": str(exc)[:80]}]
    out = []
    for name, version in sorted(lock.items()):
        got = hashed.get(name)
        if got is None:
            out.append(gettext("The hash lock has no entry for %(name)s.") % {"name": name})
        elif got[0] != version:
            out.append(gettext("The hash lock pins %(name)s to %(got)s but requirements.lock says %(want)s.") % {"name": name, "got": got[0], "want": version})
        elif not got[1]:
            out.append(gettext("The hash lock has no hashes for %(name)s.") % {"name": name})
    for name in sorted(set(hashed) - set(lock)):
        out.append(gettext("The hash lock has %(name)s but requirements.lock does not.") % {"name": name})
    return out


def installed_versions() -> dict[str, str]:
    return {norm(d.metadata["Name"]): d.version for d in metadata.distributions() if d.metadata["Name"]}


def changes(lock_text: str, installed: dict[str, str] | None = None) -> list[tuple[str, str, str]]:
    """``[(name, installed_or_empty, wanted), …]`` for pins whose installed version differs (or is missing). Empty list: nothing to install."""
    installed = installed_versions() if installed is None else installed
    return [(name, installed.get(name, ""), version) for name, version in sorted(parse_lock(lock_text).items()) if installed.get(name) != version]
