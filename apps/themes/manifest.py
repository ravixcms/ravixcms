"""
بررسی و اعتبارسنجی پوشه یک قالب بر اساس «قرارداد قالب».

``inspect_theme_dir`` هرگز استثنا نمی‌اندازد و همیشه یک ``ThemeInfo`` برمی‌گرداند؛ قالب نامعتبر در فهرست قالب‌ها همراه
با فهرست خطاهایش نمایش داده می‌شود اما قابل فعال‌سازی نیست.
"""
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from django.template import TemplateSyntaxError

from .constants import (
    ALLOWED_EXTENSIONS,
    ALLOWED_LIBRARIES,
    ALLOWED_TOP_LEVEL,
    API_VERSION,
    HOME_SECTION_TYPES,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_MANIFEST_BYTES,
    REQUIRED_BASE_TAGS,
    REQUIRED_TEMPLATES,
    SCREENSHOT_EXTENSIONS,
    THEME_MANIFEST,
)
from .engine import build_engine

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,48}$")
VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}(?:[-+][0-9A-Za-z.\-]{1,30})?$")
LOAD_RE = re.compile(r"\{%-?\s*load\s+([^%]+?)\s*-?%\}")
DEBUG_TAG_RE = re.compile(r"\{%-?\s*debug\s*-?%\}")


@dataclass(frozen=True)
class ThemeInfo:
    slug: str
    path: Path
    name: str = ""
    version: str = ""
    author: str = ""
    description: str = ""
    screenshot: str = ""
    home_sections: tuple[str, ...] = tuple(HOME_SECTION_TYPES)
    ravin: str = ""  # بازهٔ سازگاری با هسته (اختیاری)، مثل ">=0.6,<1.0"
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def templates_dir(self) -> Path:
        return self.path / "templates"

    @property
    def display_name(self) -> str:
        return self.name or self.slug

    @property
    def screenshot_static_path(self) -> str:
        return f"themes/{self.slug}/{self.screenshot}" if self.screenshot else ""


def _str_field(data: dict, key: str, errors: list, *, max_len: int, required: bool = True) -> str:
    value = data.get(key, "")
    if value in ("", None):
        if required:
            errors.append(f"فیلد «{key}» در theme.json الزامی است.")
        return ""
    if not isinstance(value, str) or len(value) > max_len:
        errors.append(f"فیلد «{key}» باید متنی حداکثر {max_len} نویسه‌ای باشد.")
        return ""
    return value.strip()


def _scan_tree(root: Path, errors: list) -> None:
    total = 0
    for entry in sorted(root.iterdir()):
        if entry.name not in ALLOWED_TOP_LEVEL:
            errors.append(f"فایل یا پوشه غیرمجاز در ریشه قالب: «{entry.name}».")
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        for name in list(dirnames):
            if (base / name).is_symlink():
                errors.append(f"پیوند نمادین (symlink) مجاز نیست: «{(base / name).relative_to(root)}».")
                dirnames.remove(name)
        for name in filenames:
            path = base / name
            rel = path.relative_to(root)
            total += 1
            if path.is_symlink():
                errors.append(f"پیوند نمادین (symlink) مجاز نیست: «{rel}».")
                continue
            if len(rel.parts) == 1:
                continue  # فایل‌های ریشه با ALLOWED_TOP_LEVEL بررسی شده‌اند
            ext = path.suffix.lstrip(".").lower()
            top = rel.parts[0]
            if ext not in ALLOWED_EXTENSIONS:
                errors.append(f"نوع فایل «{rel}» در قالب مجاز نیست (فقط فایل‌های غیرقابل‌اجرا روی سرور).")
            elif top == "templates" and ext != "html":
                errors.append(f"پوشه templates فقط فایل‌های html می‌پذیرد: «{rel}».")
            elif top == "static" and ext == "html":
                # فایل‌های static مستقیم از وب سرو می‌شوند؛ html در آن‌جا یک صفحه هم‌مبدأ بدون کنترل سایت می‌شد.
                errors.append(f"فایل html در پوشه static مجاز نیست: «{rel}» (قالب‌های html باید در templates باشند).")
            elif path.stat().st_size > MAX_FILE_BYTES:
                errors.append(f"حجم فایل «{rel}» بیش از حد مجاز است.")
    if total > MAX_FILES:
        errors.append(f"تعداد فایل‌های قالب بیش از {MAX_FILES} است.")


def _scan_templates(theme_dir: Path, errors: list) -> None:
    templates_dir = theme_dir / "templates"
    for name in REQUIRED_TEMPLATES:
        if not (templates_dir / name).is_file():
            errors.append(f"قالب الزامی «{name}» وجود ندارد.")
    engine = build_engine(templates_dir)
    for path in sorted(templates_dir.rglob("*.html")):
        rel = path.relative_to(templates_dir)
        if path.is_symlink():
            continue  # قبلاً گزارش شده است
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            errors.append(f"فایل «{rel}» باید متن UTF-8 معتبر باشد.")
            continue
        for match in LOAD_RE.finditer(source):
            parts = match.group(1).split()
            libs = parts[parts.index("from") + 1 :] if "from" in parts else parts
            for lib in libs:
                if lib not in ALLOWED_LIBRARIES:
                    errors.append(f"بارگذاری کتابخانه «{lib}» در «{rel}» مجاز نیست (فقط ravin_theme).")
        if DEBUG_TAG_RE.search(source):
            errors.append(f"تگ {{% debug %}} در «{rel}» مجاز نیست.")
        try:
            engine.from_string(source)
        except TemplateSyntaxError as exc:
            errors.append(f"خطای نحوی در «{rel}»: {exc}")
        if rel.as_posix() == "base.html":
            for tag in REQUIRED_BASE_TAGS:
                if not re.search(r"\{%-?\s*" + tag + r"\b", source):
                    errors.append(f"base.html باید از تگ {{% {tag} %}} استفاده کند.")


def inspect_theme_dir(theme_dir: Path) -> ThemeInfo:
    theme_dir = Path(theme_dir)
    slug = theme_dir.name
    errors: list[str] = []
    data: dict = {}

    if not SLUG_RE.match(slug):
        errors.append("نام پوشه قالب فقط می‌تواند شامل حروف کوچک انگلیسی، عدد، «-» و «_» باشد.")
    if theme_dir.is_symlink():
        errors.append("پوشه قالب نمی‌تواند پیوند نمادین (symlink) باشد.")
        return ThemeInfo(slug=slug, path=theme_dir, errors=tuple(errors))

    manifest = theme_dir / THEME_MANIFEST
    if not manifest.is_file():
        errors.append("فایل theme.json پیدا نشد.")
    elif manifest.is_symlink():
        errors.append("theme.json نمی‌تواند پیوند نمادین باشد.")
    elif manifest.stat().st_size > MAX_MANIFEST_BYTES:
        errors.append("حجم theme.json بیش از حد مجاز است.")
    else:
        try:
            loaded = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
            else:
                errors.append("theme.json باید یک شیء JSON باشد.")
        except (ValueError, UnicodeDecodeError) as exc:
            errors.append(f"theme.json معتبر نیست: {exc}")

    name = version = author = description = screenshot = ravin = ""
    home_sections = tuple(HOME_SECTION_TYPES)
    if data:
        if data.get("api_version") != API_VERSION:
            errors.append(f"api_version باید {API_VERSION} باشد (قالب با نسخه دیگری از قرارداد ساخته شده است).")
        if data.get("slug") != slug:
            errors.append("مقدار «slug» در theme.json باید دقیقاً با نام پوشه قالب یکی باشد.")
        name = _str_field(data, "name", errors, max_len=60)
        version = _str_field(data, "version", errors, max_len=40)
        if version and not VERSION_RE.match(version):
            errors.append("«version» باید به شکل نسخه‌دهی معنایی باشد (مثلاً 1.0.0).")
        author = _str_field(data, "author", errors, max_len=80)
        ravin = _str_field(data, "ravin", errors, max_len=50, required=False)
        if ravin:
            from apps.plugins.versions import is_valid_spec

            if not is_valid_spec(ravin):
                errors.append("«ravin» باید بازهٔ نسخه باشد (مثلاً >=0.6,<1.0).")
                ravin = ""
        description = _str_field(data, "description", errors, max_len=300, required=False)
        screenshot = _str_field(data, "screenshot", errors, max_len=100, required=False)
        if screenshot:
            if ".." in screenshot or screenshot.startswith("/") or screenshot.lower().rsplit(".", 1)[-1] not in SCREENSHOT_EXTENSIONS:
                errors.append("مسیر «screenshot» نامعتبر است (باید فایل png/jpg/webp داخل static باشد).")
                screenshot = ""
            elif not (theme_dir / "static" / screenshot).is_file():
                screenshot = ""  # تصویر معرفی اختیاری است؛ نبودنش خطا نیست
        supports = data.get("supports")
        if isinstance(supports, dict) and isinstance(supports.get("home_sections"), list):
            declared = tuple(s for s in supports["home_sections"] if s in HOME_SECTION_TYPES)
            if declared:
                home_sections = declared

    if theme_dir.is_dir():
        if not (theme_dir / "templates").is_dir():
            errors.append("پوشه templates وجود ندارد.")
        else:
            _scan_tree(theme_dir, errors)
            _scan_templates(theme_dir, errors)
    else:
        errors.append("پوشه قالب وجود ندارد.")

    return ThemeInfo(
        slug=slug,
        path=theme_dir,
        name=name,
        version=version,
        author=author,
        description=description,
        screenshot=screenshot,
        home_sections=home_sections,
        ravin=ravin,
        errors=tuple(dict.fromkeys(errors)),
    )
