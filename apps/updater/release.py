"""
سمت ناشر: ساخت بستهٔ هسته/قالب، امضا (Ed25519) و تولید ``index.json``. این ماژول روی سرور سایت لازم نیست؛ روی ماشین انتشار اجرا می‌شود.
کلید خصوصی فقط از فایل (مجوز ۶۰۰) یا متغیر محیطی خوانده می‌شود و هرگز چاپ/ذخیره نمی‌شود.
"""
from django.utils.translation import gettext
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from apps.core.version import UPDATE_SCHEMA_VERSION

from . import package, signing, versions
from .errors import UpdateError


def _add(zf: zipfile.ZipFile, src: Path, arc: str):
    if src.is_dir():
        for p in sorted(src.rglob("*")):
            if "__pycache__" in p.parts or p.suffix in (".pyc", ".pyo") or p.is_symlink():
                continue
            rel = f"{arc}/{p.relative_to(src).as_posix()}"
            if p.is_dir():
                continue
            _write(zf, p, rel)
    else:
        _write(zf, src, arc)


def _write(zf, p: Path, arc: str):
    info = zipfile.ZipInfo(arc, date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o755 if p.suffix in (".sh",) or p.name == "manage.py" else 0o644) << 16
    zf.writestr(info, p.read_bytes())


def _with_legacy(req: dict) -> dict:
    """کلاینت‌های قدیمی (<0.7) کلید «ravin» را می‌خوانند؛ تا 2.0 هر دو کلید نوشته می‌شود."""
    req = dict(req or {})
    if req.get("ravix") and "ravin" not in req:
        req["ravin"] = req["ravix"]
    return req


def _entry(data: bytes, version: str, filename: str, seed: str | None, kind: str, slug: str, requires: dict, notes: str, migrations: bool):
    sha = hashlib.sha256(data).hexdigest()
    e = {"version": version, "sha256": sha, "size": len(data), "download_url": filename, "released": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
         "notes": notes, "migrations": migrations, "requires": _with_legacy(requires)}
    if seed:
        e["signature"], e["signature_key_id"] = signing.sign(seed, kind, slug, version, sha)
    return e


def build_core(source_root: Path, out_dir: Path, *, seed: str | None, requires: dict | None = None, notes: str = "", migrations: bool = True) -> dict:
    source_root, out_dir = Path(source_root), Path(out_dir)
    version = versions.disk_version(source_root)
    if not version:
        raise UpdateError(gettext("RAVIX_VERSION was not found in apps/core/version.py."))
    req = _with_legacy(requires)
    release = {"schema": UPDATE_SCHEMA_VERSION, "component": "core", "version": version, "requires": req, "migrations": migrations}
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"ravix-core-{version}.zip"
    root_name = f"ravix-{version}"
    with zipfile.ZipFile(out_dir / fname, "w") as zf:
        for e in package.CORE_ENTRIES:
            if (source_root / e).exists():
                _add(zf, source_root / e, f"{root_name}/{e}")
        info = zipfile.ZipInfo(f"{root_name}/{package.RELEASE_FILE}", date_time=(2020, 1, 1, 0, 0, 0))
        info.external_attr = 0o644 << 16
        zf.writestr(info, json.dumps(release, ensure_ascii=False, indent=1))
    data = (out_dir / fname).read_bytes()
    return _entry(data, version, fname, seed, "core", "", req, notes, migrations)


def build_theme(theme_dir: Path, out_dir: Path, *, seed: str | None, requires: dict | None = None, notes: str = "") -> tuple[str, dict]:
    from apps.themes.manifest import inspect_theme_dir

    theme_dir, out_dir = Path(theme_dir), Path(out_dir)
    info = inspect_theme_dir(theme_dir)
    if not info.valid:
        raise UpdateError((gettext("Invalid format: %(items)s") % {"items": gettext("; ").join(info.errors[:5])}))
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"ravix-theme-{info.slug}-{info.version}.zip"
    with zipfile.ZipFile(out_dir / fname, "w") as zf:
        _add(zf, theme_dir, info.slug)
    data = (out_dir / fname).read_bytes()
    return info.slug, _entry(data, info.version, fname, seed, "theme", info.slug, dict(requires or {}), notes, False)


def write_index(out_dir: Path, *, core: list | None = None, themes: dict | None = None, seed: str | None = None, channel: str = "stable") -> Path:
    """index.json را (با ادغام ورودی‌های قبلی) می‌نویسد؛ با ``seed`` فایل index.json.sig هم ساخته می‌شود."""
    out_dir = Path(out_dir)
    path = out_dir / "index.json"
    doc = {"schema": UPDATE_SCHEMA_VERSION, "channel": channel, "core": [], "themes": {}}
    if path.is_file():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            doc["core"], doc["themes"] = list(old.get("core", [])), dict(old.get("themes", {}))
        except ValueError:
            pass
    for e in core or []:
        doc["core"] = [x for x in doc["core"] if x["version"] != e["version"]] + [e]
    for slug, es in (themes or {}).items():
        lst = [x for x in doc["themes"].get(slug, []) if x["version"] not in {y["version"] for y in es}]
        doc["themes"][slug] = lst + es
    doc["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data = json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True).encode()
    path.write_bytes(data)
    sig_path = out_dir / "index.json.sig"
    if seed:
        sig, kid = signing.sign_index(seed, data)
        sig_path.write_text(json.dumps({"signature": sig, "key_id": kid}), encoding="utf-8")
    else:
        sig_path.unlink(missing_ok=True)
    return path
