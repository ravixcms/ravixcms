"""
شناسایی افزونه‌های معتبر. فقط پوشه‌های فهرست‌شده در ``RAVIX_PLUGIN_DIRS`` (تنظیم سرور، نه ورودی کاربر) بررسی می‌شوند و فقط
``plugin.json`` خوانده می‌شود؛ هیچ کدی import نمی‌شود. افزونهٔ نامعتبر در فهرست با دلیل خطا نمایش داده می‌شود.
"""
from django.utils.translation import gettext
from dataclasses import dataclass, field
from pathlib import Path

import logging

from django.conf import settings

from .integrity import IntegrityError, fingerprint
from .manifest import MANIFEST_FILE, ManifestError, read_manifest

logger = logging.getLogger(__name__)


@dataclass
class Discovered:
    slug: str
    path: Path
    manifest: object = None
    errors: list = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.manifest is not None and not self.errors

    def fingerprint(self):
        return fingerprint(self.path)


def plugin_dirs() -> list[Path]:
    return [Path(p) for p in getattr(settings, "RAVIX_PLUGIN_DIRS", [])]


def _inspect(path: Path) -> Discovered:
    d = Discovered(slug=path.name, path=path)
    if path.is_symlink():
        d.errors.append(gettext("The plugin folder must not be a symlink."))
        return d
    if not (path / "__init__.py").is_file():
        d.errors.append(gettext("The __init__.py file is missing."))
    try:
        d.manifest = read_manifest(path, expected_slug=path.name)
    except ManifestError as exc:
        d.errors.extend(exc.errors)
    try:
        fingerprint(path)  # فقط برای کشف symlink/حجم غیرمجاز؛ مقدار ذخیره نمی‌شود
    except IntegrityError as exc:
        d.errors.append(str(exc))
    if d.errors:
        d.manifest = None if d.manifest is None else d.manifest
    return d


def scan() -> tuple[list[Discovered], list[str]]:
    """Every plugin found, plus warnings for plugin folders this process cannot read (a permission problem is shown, never a 500)."""
    found: dict[str, Discovered] = {}
    warnings: list[str] = []
    for base in plugin_dirs():
        try:
            if not base.is_dir():
                continue
            children = sorted(base.iterdir())
        except OSError:
            from apps.core import fsaccess

            logger.warning("%s", fsaccess.unreadable_detail(base))
            warnings.append(gettext("The plugin folder %(path)s cannot be read by the web service, so the plugins in it are not listed. "
                                    "Give the service user read access (SupplementaryGroups=ravix in ravix.service, then restart) and run ravix_preflight.") % {"path": base})
            continue
        for child in children:
            try:
                if not child.is_dir() or child.name.startswith((".", "_")):
                    continue
            except OSError:
                continue
            item = _inspect(child)
            if child.name in found:
                item.errors.append((gettext("Duplicate slug; the version in folder %(path)s takes priority.") % {"path": found[child.name].path}))
                item.slug = f"{child.name}@dup"
                found[item.slug] = item
                continue
            found[child.name] = item
    return list(found.values()), warnings


def discover() -> list[Discovered]:
    return scan()[0]


def find(slug: str) -> Discovered | None:
    for item in discover():
        if item.slug == slug:
            return item
    return None


__all__ = ["Discovered", "discover", "scan", "find", "plugin_dirs", "MANIFEST_FILE"]
