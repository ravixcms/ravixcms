"""Extra translation catalogs contributed by plugins and themes.

Django merges the catalogs found in ``settings.LOCALE_PATHS``; the **first** entry wins on conflicts. Ravix keeps its own
``locale/`` first, so a plugin or theme can add strings but can never override (or hijack) a core string.

* A **plugin** ships a normal gettext tree: ``<plugin>/locale/<lang>/LC_MESSAGES/django.mo`` (+ ``djangojs.mo`` for its JS).
  It is registered when the plugin is loaded and removed when it is unloaded.
* A **theme** ships plain JSON files ``<theme>/locale/<lang>.json`` (``{"English text": "translation"}``). They are compiled
  here, by Ravix, into a ``.mo`` file; a theme package therefore never carries a binary file that has to be parsed.

Registering or removing a catalog resets Django's translation cache, so the change is visible immediately in this process.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import struct
import tempfile
import threading
from pathlib import Path

from django.conf import settings
from django.utils.translation import to_locale

logger = logging.getLogger(__name__)

MAX_CATALOG_BYTES = 128 * 1024
MAX_CATALOG_ENTRIES = 3000
MAX_MSGID_LENGTH = 2000
MAX_MSGSTR_LENGTH = 4000

_lock = threading.Lock()
_base: list[str] | None = None  # the project's own LOCALE_PATHS (core first)
_extras: dict[str, Path] = {}


# ---------------------------------------------------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------------------------------------------------
def _apply() -> None:
    global _base
    if _base is None:
        _base = [str(p) for p in settings.LOCALE_PATHS]
    wanted = list(_base) + [str(path) for _owner, path in sorted(_extras.items())]
    if [str(p) for p in settings.LOCALE_PATHS] == wanted:
        return
    settings.LOCALE_PATHS = wanted
    reset_translations()


def reset_translations() -> None:
    """Forget Django's compiled translations (they are rebuilt lazily) and re-activate this thread's language."""
    from django.utils.translation import trans_real

    current = trans_real.get_language()
    trans_real._translations = {}
    trans_real._default = None
    trans_real.translation_catalog_exists.cache_clear()
    if current:
        trans_real.activate(current)


def register(owner: str, path) -> None:
    """Add (or replace) the catalog directory of ``owner`` (``plugin:<slug>``). ``path`` is a gettext ``locale/`` directory."""
    with _lock:
        _extras[owner] = Path(path)
        _apply()


def unregister(owner: str) -> None:
    with _lock:
        if _extras.pop(owner, None) is not None:
            _apply()


def sync(prefix: str, mapping: dict[str, Path]) -> None:
    """Make the registered owners starting with ``prefix`` exactly ``mapping`` ({owner: locale dir}); one cache reset."""
    with _lock:
        before = dict(_extras)
        for owner in [o for o in _extras if o.startswith(prefix)]:
            del _extras[owner]
        _extras.update({owner: Path(path) for owner, path in mapping.items()})
        if _extras != before:
            _apply()


def registered() -> dict[str, Path]:
    with _lock:
        return dict(_extras)


def reset_for_tests() -> None:
    with _lock:
        _extras.clear()
        _apply()


# ---------------------------------------------------------------------------------------------------------------------
# .mo writer (so that no binary file has to be shipped or parsed for themes)
# ---------------------------------------------------------------------------------------------------------------------
def write_mo(path: Path, messages: dict[str, str], language: str = "") -> None:
    """Write a GNU ``.mo`` catalog (little-endian, no hash table) for ``messages``."""
    header = "Content-Type: text/plain; charset=UTF-8\nContent-Transfer-Encoding: 8bit\n"
    if language:
        header += f"Language: {language}\n"
    items = {"": header, **messages}
    keys = sorted(items)
    ids = b""
    strs = b""
    offsets = []
    for key in keys:
        kb, vb = key.encode("utf-8"), items[key].encode("utf-8")
        offsets.append((len(ids), len(kb), len(strs), len(vb)))
        ids += kb + b"\0"
        strs += vb + b"\0"
    count = len(keys)
    keystart = 7 * 4 + 16 * count
    valuestart = keystart + len(ids)
    table = []
    for o1, l1, o2, l2 in offsets:
        table += [l1, o1 + keystart]
    for o1, l1, o2, l2 in offsets:
        table += [l2, o2 + valuestart]
    data = struct.pack("<Iiiiiii", 0x950412DE, 0, count, 7 * 4, 7 * 4 + count * 8, 0, 0)
    data += struct.pack(f"<{len(table)}i", *table) + ids + strs
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# ---------------------------------------------------------------------------------------------------------------------
# Theme JSON catalogs
# ---------------------------------------------------------------------------------------------------------------------
def theme_catalog_language(filename: str) -> str:
    """"pt-br.json" / "pt_BR.json" → "pt-br"; an unsupported language (or a non-.json name) → ""."""
    from .regional import normalize_language

    name = Path(filename).name
    if not name.lower().endswith(".json"):
        return ""
    return normalize_language(name[:-5])


def read_theme_catalog(path: Path) -> tuple[dict[str, str] | None, bool]:
    """(messages, ok). ``ok`` is False for anything that is not a flat {English text: translation} object within the limits."""
    try:
        if path.is_symlink() or path.stat().st_size > MAX_CATALOG_BYTES:
            return None, False
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, False
    if not isinstance(data, dict) or len(data) > MAX_CATALOG_ENTRIES:
        return None, False
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, str) or not key or not value:
            return None, False
        if len(key) > MAX_MSGID_LENGTH or len(value) > MAX_MSGSTR_LENGTH:
            return None, False
    return data, True


def _cache_root() -> Path:
    base = getattr(settings, "RAVIX_THEME_INSTALL_DIR", None)
    candidates = ([Path(base) / ".i18n"] if base else []) + [Path(tempfile.gettempdir()) / "ravix-i18n"]
    for root in candidates:
        try:
            root.mkdir(parents=True, exist_ok=True)
            if os.access(root, os.W_OK):
                return root
        except OSError:
            continue
    raise OSError("no writable directory for compiled theme catalogs")


def compile_theme_catalogs(slug: str, theme_dir: Path) -> Path | None:
    """Compile ``<theme>/locale/*.json`` into a gettext tree and return it (None when the theme has no valid catalog)."""
    locale_dir = Path(theme_dir) / "locale"
    if not locale_dir.is_dir() or locale_dir.is_symlink():
        return None
    catalogs: dict[str, dict[str, str]] = {}
    digest = hashlib.sha256()
    for file in sorted(locale_dir.glob("*.json")):
        language = theme_catalog_language(file.name)
        messages, ok = read_theme_catalog(file)
        if not language or not ok:
            continue
        catalogs[language] = messages
        digest.update(language.encode() + b"\0" + json.dumps(messages, sort_keys=True, ensure_ascii=False).encode())
    if not catalogs:
        return None
    root = _cache_root()
    target = root / f"{slug}-{digest.hexdigest()[:16]}"
    if not target.is_dir():
        tmp = Path(tempfile.mkdtemp(prefix=f".{slug}-", dir=root))
        try:
            for language, messages in catalogs.items():
                locale = to_locale(language)
                write_mo(tmp / locale / "LC_MESSAGES" / "django.mo", messages, language=locale)
            try:
                os.replace(tmp, target)
            except OSError:  # another worker built the same catalog first
                shutil.rmtree(tmp, ignore_errors=True)
        except Exception:  # noqa: BLE001
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    for stale in root.glob(f"{slug}-*"):  # older builds of this theme
        if stale != target and stale.is_dir():
            shutil.rmtree(stale, ignore_errors=True)
    return target


def sync_themes(themes) -> None:
    """Register the compiled catalogs of every valid installed theme (``themes``: iterable of ThemeInfo)."""
    mapping: dict[str, Path] = {}
    for info in themes:
        if not getattr(info, "valid", False):
            continue
        try:
            built = compile_theme_catalogs(info.slug, info.path)
        except Exception:  # noqa: BLE001 — a broken catalog must never break the site
            logger.exception("Could not compile the translations of theme %s", info.slug)
            continue
        if built is not None:
            mapping[f"theme:{info.slug}"] = built
    sync("theme:", mapping)
