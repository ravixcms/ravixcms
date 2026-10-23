"""
قالب و اعتبارسنجی بستهٔ افزونه (مشترک بین نصب‌کننده و مخزن).

بستهٔ افزونه = فایل zip با یک پوشهٔ ریشه ``<slug>/`` که ``plugin.json`` دارد. اعتبارسنجی **بدون اجرای هیچ کدی** انجام می‌شود.

محافظت‌ها: مسیر مطلق/``..``/بک‌اسلش، symlink، نام تکراری، سقف تعداد/حجم (ضد zip-bomb)، فایل‌های اجرایی عجیب، manifest نامعتبر.
"""
from django.utils.translation import gettext
import hashlib
import io
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from django.conf import settings

from apps.plugins.integrity import MAX_FILES, MAX_TOTAL_BYTES, IntegrityError, fingerprint
from apps.plugins.manifest import MANIFEST_FILE, MAX_MANIFEST_BYTES, Manifest, ManifestError, parse_manifest


class PackageError(Exception):
    pass


@dataclass
class Inspected:
    manifest: Manifest
    raw_manifest: dict
    sha256: str
    fingerprint: str
    size: int
    data: bytes


def _safe_members(zf: zipfile.ZipFile):
    infos = zf.infolist()
    if not infos:
        raise PackageError(gettext("The package is empty."))
    if len(infos) > MAX_FILES * 2:
        raise PackageError(gettext("The number of files in the package exceeds the allowed limit."))
    seen, files, total, root = set(), [], 0, None
    for info in infos:
        name = info.filename
        if "\\" in name or name.startswith("/") or "\x00" in name:
            raise PackageError((gettext("Invalid file name: %(name)s") % {"name": repr(name)}))
        parts = PurePosixPath(name).parts
        if not parts or any(p in ("..", "") for p in parts) or (len(parts[0]) == 2 and parts[0][1] == ":"):
            raise PackageError((gettext("Unsafe path in package: %(name)s") % {"name": repr(name)}))
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise PackageError((gettext("Symbolic links are not allowed: %(name)s") % {"name": name}))
        if stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise PackageError((gettext("Unusual file type: %(name)s") % {"name": name}))
        if info.flag_bits & 0x1:
            raise PackageError(gettext("Encrypted packages are not allowed."))
        if root is None:
            root = parts[0]
        elif parts[0] != root:
            raise PackageError(gettext("All files must be under a single root folder (the plugin name)."))
        key = name.rstrip("/").lower()
        if key in seen:
            raise PackageError((gettext("Duplicate name in the package: %(name)s") % {"name": name}))
        seen.add(key)
        if info.is_dir():
            continue
        if len(parts) < 2:
            raise PackageError(gettext("The file is outside the plugin folder."))
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise PackageError(gettext("The extracted size of the package exceeds the allowed limit."))
        files.append(info)
    if len(files) > MAX_FILES:
        raise PackageError(gettext("The number of files in the package exceeds the allowed limit."))
    return root, files


def extract_to(data: bytes, dest: Path) -> str:
    """بسته را به‌صورت امن در dest (پوشهٔ موجود) باز می‌کند و نام ریشه را برمی‌گرداند. حجم واقعی هنگام خواندن هم شمرده می‌شود."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise PackageError(gettext("The zip file is not valid."))
    with zf:
        root, files = _safe_members(zf)
        written = 0
        for info in files:
            target = (dest / PurePosixPath(info.filename)).resolve()
            if dest.resolve() not in target.parents:
                raise PackageError(gettext("The path is outside the destination folder."))
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                while True:
                    chunk = src.read(65536)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_TOTAL_BYTES:
                        raise PackageError(gettext("The extracted size of the package exceeds the allowed limit."))
                    out.write(chunk)
    return root


def inspect(data: bytes) -> Inspected:
    max_bytes = getattr(settings, "RAVIX_REGISTRY_MAX_PACKAGE_BYTES", 5 * 1024 * 1024)
    if not data:
        raise PackageError(gettext("No file was submitted."))
    if len(data) > max_bytes:
        raise PackageError((gettext("The package is larger than %(value)s KB.") % {"value": (max_bytes // 1024)}))
    import json

    with tempfile.TemporaryDirectory(prefix="ravix-pkg-") as tmp:
        dest = Path(tmp)
        root = extract_to(data, dest)
        pdir = dest / root
        mpath = pdir / MANIFEST_FILE
        if not mpath.is_file():
            raise PackageError(gettext("plugin.json is not at the package root."))
        if mpath.stat().st_size > MAX_MANIFEST_BYTES:
            raise PackageError(gettext("plugin.json is larger than the allowed limit."))
        try:
            raw = json.loads(mpath.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PackageError((gettext("plugin.json is not valid JSON: %(exc)s") % {"exc": exc}))
        try:
            manifest = parse_manifest(raw, expected_slug=root)
        except ManifestError as exc:
            raise PackageError((gettext("Invalid plugin.json: %(errors)s") % {"errors": gettext("; ").join(exc.errors)}))
        try:
            fp = fingerprint(pdir)
        except IntegrityError as exc:
            raise PackageError(str(exc))
    return Inspected(manifest=manifest, raw_manifest=manifest.to_json(), sha256=hashlib.sha256(data).hexdigest(), fingerprint=fp, size=len(data), data=data)
