"""
بستهٔ هسته و قالب: استخراج امن و اعتبارسنجی **بدون اجرای هیچ کدی** پیش از جایگزینی فایل‌ها.

محافظت‌ها: مسیر مطلق/``..``/بک‌اسلش/نام درایو، symlink و فایل غیرعادی، رمزگذاری، سقف تعداد/حجم/نسبت فشرده‌سازی (zip-bomb)،
تنها یک پوشهٔ ریشه، فقط مسیرهای مجاز (فهرست سفید)، رد فایل‌های محرمانه (.env، دیتابیس، کلید خصوصی PEM)، و بررسی نحوی (compile) همهٔ .py.
"""
import io
import json
import os
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from apps.core.version import UPDATE_SCHEMA_VERSION

from . import versions
from .errors import UpdateError

MAX_FILES = 20000
MAX_TOTAL = 400 * 1024 * 1024
MAX_FILE = 60 * 1024 * 1024
MAX_RATIO = 200
RELEASE_FILE = "release.json"

# فقط این مسیرها را بستهٔ هسته می‌تواند جایگزین کند. هرگز: .env، .venv، media، دیتابیس، plugins/، plugins_installed، قالب‌های سفارشی، پشتیبان‌ها.
CORE_ENTRIES = ("apps", "config", "static", "templates", "themes/default", "deploy", "docs", "scripts", "manage.py", "requirements.txt",
                "requirements-optional.txt", "README.md", ".env.example")
REQUIRED_CORE = ("manage.py", "apps/core/version.py", "config", "requirements.txt")
THEME_EXTENSIONS = {"html", "css", "js", "json", "png", "jpg", "jpeg", "gif", "webp", "svg", "woff", "woff2", "ttf", "ico", "txt", "md", "map"}
FORBIDDEN_NAME = re.compile(r"(^|/)(\.env(\..+)?|.*\.sqlite3?|.*\.pem|.*\.key|id_rsa.*|\.git|\.venv|__pycache__|.*\.pyc|.*\.pyo)$")
# نشانگرها عمداً تکه‌تکه نوشته شده‌اند تا خود این فایل (که داخل بستهٔ هسته است) با اسکنر خودش رد نشود.
_B = "-----BEGIN "
PRIVATE_KEY_MARK = tuple((_B + kind + "PRIVATE KEY-----").encode() for kind in ("", "OPENSSH ", "RSA ", "EC ", "ENCRYPTED ", "DSA "))


@dataclass
class Extracted:
    root: Path  # پوشهٔ ریشهٔ استخراج‌شده (حاوی محتوا)
    release: dict
    files: int


def _safe_names(zf: zipfile.ZipFile):
    infos = zf.infolist()
    if not infos:
        raise UpdateError("بسته خالی است.")
    if len(infos) > MAX_FILES:
        raise UpdateError("تعداد فایل‌های بسته از سقف مجاز بیشتر است.")
    total, root = 0, None
    for info in infos:
        name = info.filename
        if "\\" in name or name.startswith("/") or "\x00" in name:
            raise UpdateError(f"نام فایل نامعتبر در بسته: {name!r}")
        parts = PurePosixPath(name).parts
        if not parts or any(p in ("..", "") for p in parts) or (len(parts[0]) == 2 and parts[0][1] == ":"):
            raise UpdateError(f"مسیر ناامن در بسته: {name!r}")
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise UpdateError(f"پیوند نمادین (symlink) مجاز نیست: {name}")
        if stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise UpdateError(f"نوع فایل غیرعادی: {name}")
        if info.flag_bits & 0x1:
            raise UpdateError("بستهٔ رمزگذاری‌شده مجاز نیست.")
        if root is None:
            root = parts[0]
        elif parts[0] != root:
            raise UpdateError("همهٔ فایل‌ها باید زیر یک پوشهٔ ریشه باشند.")
        if info.file_size > MAX_FILE:
            raise UpdateError(f"فایل «{name}» بیش از حد بزرگ است.")
        if info.compress_size and info.file_size / max(info.compress_size, 1) > MAX_RATIO and info.file_size > 1024 * 1024:
            raise UpdateError("نسبت فشرده‌سازی مشکوک است (احتمال zip-bomb).")
        total += info.file_size
        if total > MAX_TOTAL:
            raise UpdateError("حجم باز‌شدهٔ بسته از سقف مجاز بیشتر است.")
    return infos, root


def extract(data: bytes, dest: Path) -> Path:
    """بسته را در ``dest`` (پوشهٔ خالی) استخراج می‌کند و مسیر ریشهٔ استخراج‌شده را برمی‌گرداند. هر تخلف ← UpdateError."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UpdateError("فایل بسته zip معتبر نیست.")
    with zf:
        infos, root = _safe_names(zf)
        if zf.testzip() is not None:
            raise UpdateError("بسته خراب است (CRC).")
        dest.mkdir(parents=True, exist_ok=True)
        base = dest.resolve()
        for info in infos:
            target = (base / info.filename).resolve()
            if base != target and base not in target.parents:
                raise UpdateError(f"مسیر خارج از پوشهٔ استخراج: {info.filename!r}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                out.write(src.read(MAX_FILE + 1))
            os.chmod(target, 0o755 if (info.external_attr >> 16) & 0o111 and target.suffix in ("", ".sh") else 0o644)  # بدون setuid/setgid
    return base / root


def _walk_files(root: Path):
    for p in sorted(root.rglob("*")):
        if p.is_file():
            yield p


def _scan_secrets_and_syntax(root: Path, *, python_ok: bool):
    count = 0
    for p in _walk_files(root):
        rel = p.relative_to(root).as_posix()
        count += 1
        if FORBIDDEN_NAME.search(rel) and rel != ".env.example":
            raise UpdateError(f"فایل غیرمجاز در بسته: {rel}")
        suffix = p.suffix.lower()
        if suffix == ".py" or suffix in (".json", ".txt", ".md", ".example", ".sh", ".conf", ".service"):
            raw = p.read_bytes()
            if len(raw) < 1024 * 1024 and any(m in raw for m in PRIVATE_KEY_MARK):
                raise UpdateError(f"کلید خصوصی داخل بسته پیدا شد ({rel}); بسته رد شد.")
            if suffix == ".py":
                if not python_ok:
                    raise UpdateError(f"فایل پایتون در این نوع بسته مجاز نیست: {rel}")
                try:
                    compile(raw, rel, "exec", dont_inherit=True)  # فقط بررسی نحوی؛ اجرا نمی‌شود
                except (SyntaxError, ValueError) as exc:
                    raise UpdateError(f"خطای نحوی در {rel}: {exc}")
        elif python_ok is False and suffix.lstrip(".") not in THEME_EXTENSIONS and p.name not in ("LICENSE", "README"):
            raise UpdateError(f"نوع فایل مجاز نیست: {rel}")
    return count


def load_release(root: Path) -> dict:
    path = root / RELEASE_FILE
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise UpdateError("release.json در بسته نیست یا معتبر نیست.")
    if not isinstance(doc, dict) or doc.get("schema") != UPDATE_SCHEMA_VERSION:
        raise UpdateError("قالب release.json پشتیبانی نمی‌شود.")
    return doc


def validate_core(root: Path, expected_version: str) -> Extracted:
    rel = load_release(root)
    if rel.get("component") != "core":
        raise UpdateError("این بسته بستهٔ هسته نیست.")
    if rel.get("version") != expected_version:
        raise UpdateError(f"نسخهٔ داخل بسته ({rel.get('version')}) با نسخهٔ درخواستی ({expected_version}) یکی نیست.")
    entries = sorted(p.name for p in root.iterdir() if p.name != RELEASE_FILE)
    allowed_top = {e.split("/")[0] for e in CORE_ENTRIES}
    for name in entries:
        if name not in allowed_top:
            raise UpdateError(f"مسیر غیرمجاز در بستهٔ هسته: {name}")
    for req in REQUIRED_CORE:
        if not (root / req).exists():
            raise UpdateError(f"فایل/پوشهٔ الزامی در بسته نیست: {req}")
    if (root / "themes").exists():
        for child in (root / "themes").iterdir():
            if child.name != "default":
                raise UpdateError("بستهٔ هسته فقط می‌تواند قالب «default» را شامل شود.")
    inner = versions.version_in_file(root / "apps/core/version.py")
    if inner != expected_version:
        raise UpdateError(f"RAVIX_VERSION داخل بسته ({inner}) با نسخهٔ اعلام‌شده ({expected_version}) یکی نیست.")
    files = _scan_secrets_and_syntax(root, python_ok=True)
    return Extracted(root, rel, files)


def validate_theme(root: Path, slug: str, expected_version: str) -> Extracted:
    from apps.themes.manifest import inspect_theme_dir

    if root.name != slug:
        raise UpdateError(f"پوشهٔ ریشهٔ بسته («{root.name}») با نامک قالب («{slug}») یکی نیست.")
    files = _scan_secrets_and_syntax(root, python_ok=False)
    info = inspect_theme_dir(root)
    if not info.valid:
        raise UpdateError("قالب نامعتبر است: " + "؛ ".join(info.errors[:5]))
    if info.version != expected_version:
        raise UpdateError(f"نسخهٔ داخل theme.json ({info.version}) با نسخهٔ درخواستی ({expected_version}) یکی نیست.")
    return Extracted(root, {"component": "theme", "version": info.version, "requires": {"ravix": getattr(info, "ravix", ""), "ravin": getattr(info, "ravix", "")}}, files)
