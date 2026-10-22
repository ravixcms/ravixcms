"""
قالب و اعتبارسنجی بستهٔ افزونه (مشترک بین نصب‌کننده و مخزن).

بستهٔ افزونه = فایل zip با یک پوشهٔ ریشه ``<slug>/`` که ``plugin.json`` دارد. اعتبارسنجی **بدون اجرای هیچ کدی** انجام می‌شود.

محافظت‌ها: مسیر مطلق/``..``/بک‌اسلش، symlink، نام تکراری، سقف تعداد/حجم (ضد zip-bomb)، فایل‌های اجرایی عجیب، manifest نامعتبر.
"""
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
        raise PackageError("بسته خالی است.")
    if len(infos) > MAX_FILES * 2:
        raise PackageError("تعداد فایل‌های بسته از سقف مجاز بیشتر است.")
    seen, files, total, root = set(), [], 0, None
    for info in infos:
        name = info.filename
        if "\\" in name or name.startswith("/") or "\x00" in name:
            raise PackageError(f"نام فایل نامعتبر: {name!r}")
        parts = PurePosixPath(name).parts
        if not parts or any(p in ("..", "") for p in parts) or (len(parts[0]) == 2 and parts[0][1] == ":"):
            raise PackageError(f"مسیر ناامن در بسته: {name!r}")
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise PackageError(f"پیوند نمادین (symlink) مجاز نیست: {name}")
        if stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise PackageError(f"نوع فایل غیرعادی: {name}")
        if info.flag_bits & 0x1:
            raise PackageError("بستهٔ رمزگذاری‌شده مجاز نیست.")
        if root is None:
            root = parts[0]
        elif parts[0] != root:
            raise PackageError("همهٔ فایل‌ها باید زیر یک پوشهٔ ریشه (نام افزونه) باشند.")
        key = name.rstrip("/").lower()
        if key in seen:
            raise PackageError(f"نام تکراری در بسته: {name}")
        seen.add(key)
        if info.is_dir():
            continue
        if len(parts) < 2:
            raise PackageError("فایل بیرون از پوشهٔ افزونه است.")
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise PackageError("حجم بازشدهٔ بسته از سقف مجاز بیشتر است.")
        files.append(info)
    if len(files) > MAX_FILES:
        raise PackageError("تعداد فایل‌های بسته از سقف مجاز بیشتر است.")
    return root, files


def extract_to(data: bytes, dest: Path) -> str:
    """بسته را به‌صورت امن در dest (پوشهٔ موجود) باز می‌کند و نام ریشه را برمی‌گرداند. حجم واقعی هنگام خواندن هم شمرده می‌شود."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise PackageError("فایل zip معتبر نیست.")
    with zf:
        root, files = _safe_members(zf)
        written = 0
        for info in files:
            target = (dest / PurePosixPath(info.filename)).resolve()
            if dest.resolve() not in target.parents:
                raise PackageError("مسیر خارج از پوشهٔ مقصد.")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                while True:
                    chunk = src.read(65536)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_TOTAL_BYTES:
                        raise PackageError("حجم بازشدهٔ بسته از سقف مجاز بیشتر است.")
                    out.write(chunk)
    return root


def inspect(data: bytes) -> Inspected:
    max_bytes = getattr(settings, "RAVIX_REGISTRY_MAX_PACKAGE_BYTES", 5 * 1024 * 1024)
    if not data:
        raise PackageError("فایلی ارسال نشد.")
    if len(data) > max_bytes:
        raise PackageError(f"حجم بسته بیشتر از {max_bytes // 1024} کیلوبایت است.")
    import json

    with tempfile.TemporaryDirectory(prefix="ravix-pkg-") as tmp:
        dest = Path(tmp)
        root = extract_to(data, dest)
        pdir = dest / root
        mpath = pdir / MANIFEST_FILE
        if not mpath.is_file():
            raise PackageError("plugin.json در ریشهٔ بسته نیست.")
        if mpath.stat().st_size > MAX_MANIFEST_BYTES:
            raise PackageError("plugin.json بزرگ‌تر از حد مجاز است.")
        try:
            raw = json.loads(mpath.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PackageError(f"plugin.json JSON معتبر نیست: {exc}")
        try:
            manifest = parse_manifest(raw, expected_slug=root)
        except ManifestError as exc:
            raise PackageError("plugin.json نامعتبر: " + "؛ ".join(exc.errors))
        try:
            fp = fingerprint(pdir)
        except IntegrityError as exc:
            raise PackageError(str(exc))
    return Inspected(manifest=manifest, raw_manifest=manifest.to_json(), sha256=hashlib.sha256(data).hexdigest(), fingerprint=fp, size=len(data), data=data)
