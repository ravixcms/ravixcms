"""
بررسی و اعتبارسنجی پوشه یک قالب بر اساس «قرارداد قالب».

``inspect_theme_dir`` هرگز استثنا نمی‌اندازد و همیشه یک ``ThemeInfo`` برمی‌گرداند؛ قالب نامعتبر در فهرست قالب‌ها همراه
با فهرست خطاهایش نمایش داده می‌شود اما قابل فعال‌سازی نیست.
"""
from django.utils.translation import gettext
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from django.template import TemplateSyntaxError

from apps.core.deprecation import upgrade_legacy_key, warn_legacy

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
    homepage: str = ""
    description: str = ""
    names: dict = field(default_factory=dict)  # {"fa": "…", "en": "…"} from a multilingual «name» (empty for a plain string)
    descriptions: dict = field(default_factory=dict)
    screenshot: str = ""
    home_sections: tuple[str, ...] = tuple(HOME_SECTION_TYPES)
    ravix: str = ""  # بازهٔ سازگاری با هسته (اختیاری)، مثل ">=0.6,<1.0"
    studio_full_width: bool = False  # supports.studio_full_width: the theme has a full-width / no-sidebar layout for Ravix Studio pages
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def templates_dir(self) -> Path:
        return self.path / "templates"

    @property
    def display_name(self) -> str:
        """The name in the active language (falls back to the default text, then the slug)."""
        from apps.core.regional import pick_localized

        return pick_localized(self.name, self.names) or self.slug

    @property
    def display_description(self) -> str:
        from apps.core.regional import pick_localized

        return pick_localized(self.description, self.descriptions)

    @property
    def screenshot_url(self) -> str:
        """URL of the preview image: the static pipeline for bundled themes, /theme-assets/ for themes installed from the panel."""
        if not self.screenshot:
            return ""
        from .assets import theme_static_url

        return theme_static_url(self.slug, self.screenshot, self.version)

    @property
    def screenshot_static_path(self) -> str:
        return f"themes/{self.slug}/{self.screenshot}" if self.screenshot else ""


def _str_field(data: dict, key: str, errors: list, *, max_len: int, required: bool = True) -> str:
    value = data.get(key, "")
    if value in ("", None):
        if required:
            errors.append((gettext("The field “%(key)s” is required in theme.json.") % {"key": key}))
        return ""
    if not isinstance(value, str) or len(value) > max_len:
        errors.append((gettext("The field “%(key)s” must be a string of at most %(max_len)s characters.") % {"key": key, "max_len": max_len}))
        return ""
    return value.strip()


def _scan_tree(root: Path, errors: list) -> None:
    total = 0
    for entry in sorted(root.iterdir()):
        if entry.name not in ALLOWED_TOP_LEVEL:
            errors.append((gettext("File or folder not allowed in the theme root: “%(name)s”.") % {"name": entry.name}))
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        for name in list(dirnames):
            if (base / name).is_symlink():
                errors.append((gettext("Symbolic links are not allowed: “%(relative_to)s”.") % {"relative_to": (base / name).relative_to(root)}))
                dirnames.remove(name)
        for name in filenames:
            path = base / name
            rel = path.relative_to(root)
            total += 1
            if path.is_symlink():
                errors.append((gettext("Symbolic links are not allowed: “%(rel)s”.") % {"rel": rel}))
                continue
            if len(rel.parts) == 1:
                continue  # فایل‌های ریشه با ALLOWED_TOP_LEVEL بررسی شده‌اند
            ext = path.suffix.lstrip(".").lower()
            top = rel.parts[0]
            if ext not in ALLOWED_EXTENSIONS:
                errors.append((gettext("The file type of “%(rel)s” is not allowed in a theme (only files that cannot run on the server).") % {"rel": rel}))
            elif top == "templates" and ext != "html":
                errors.append((gettext("The templates folder accepts only html files: “%(rel)s”.") % {"rel": rel}))
            elif top == "locale":
                from apps.core import catalogs

                if len(rel.parts) != 2 or ext != "json" or not catalogs.theme_catalog_language(path.name):
                    errors.append((gettext("The locale folder accepts only <language>.json files for the supported languages (for example fr.json or pt-br.json): “%(rel)s”.") % {"rel": rel}))
                elif not catalogs.read_theme_catalog(path)[1]:
                    errors.append((gettext("The translation file “%(rel)s” must be a JSON object that maps English text to its translation (text only, within the size limits).") % {"rel": rel}))
            elif top == "static" and ext == "html":
                # فایل‌های static مستقیم از وب سرو می‌شوند؛ html در آن‌جا یک صفحه هم‌مبدأ بدون کنترل سایت می‌شد.
                errors.append((gettext("html files are not allowed in the static folder: “%(rel)s” (html templates must be in templates).") % {"rel": rel}))
            elif path.stat().st_size > MAX_FILE_BYTES:
                errors.append((gettext("The size of file “%(rel)s” exceeds the limit.") % {"rel": rel}))
    if total > MAX_FILES:
        errors.append((gettext("The number of theme files exceeds %(MAX_FILES)s.") % {"MAX_FILES": MAX_FILES}))


def _scan_templates(theme_dir: Path, errors: list) -> None:
    templates_dir = theme_dir / "templates"
    for name in REQUIRED_TEMPLATES:
        if not (templates_dir / name).is_file():
            errors.append((gettext("The required template “%(name)s” does not exist.") % {"name": name}))
    engine = build_engine(templates_dir)
    for path in sorted(templates_dir.rglob("*.html")):
        rel = path.relative_to(templates_dir)
        if path.is_symlink():
            continue  # قبلاً گزارش شده است
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            errors.append((gettext("The file “%(rel)s” must be valid UTF-8 text.") % {"rel": rel}))
            continue
        for match in LOAD_RE.finditer(source):
            parts = match.group(1).split()
            libs = parts[parts.index("from") + 1 :] if "from" in parts else parts
            for lib in libs:
                if lib == "ravin_theme":
                    warn_legacy("{% load ravin_theme %}", "{% load ravix_theme %}")
                if lib not in ALLOWED_LIBRARIES:
                    errors.append((gettext("Loading the library “%(lib)s” in “%(rel)s” is not allowed (only ravix_theme and i18n).") % {"lib": lib, "rel": rel}))
        if DEBUG_TAG_RE.search(source):
            errors.append((gettext("The {%% debug %%} tag is not allowed in “%(rel)s”.") % {"rel": rel}))
        try:
            engine.from_string(source)
        except TemplateSyntaxError as exc:
            errors.append((gettext("Syntax error in “%(rel)s”: %(exc)s") % {"rel": rel, "exc": exc}))
        if rel.as_posix() == "base.html":
            for tag in REQUIRED_BASE_TAGS:
                if not re.search(r"\{%-?\s*" + tag + r"\b", source):
                    errors.append((gettext("base.html must use the {%% %(tag)s %%} tag.") % {"tag": tag}))


def inspect_theme_dir(theme_dir: Path) -> ThemeInfo:
    theme_dir = Path(theme_dir)
    slug = theme_dir.name
    errors: list[str] = []
    data: dict = {}

    if not SLUG_RE.match(slug):
        errors.append(gettext("The theme folder name may only contain lowercase English letters, digits, “-” and “_”."))
    if theme_dir.is_symlink():
        errors.append(gettext("The theme folder cannot be a symbolic link."))
        return ThemeInfo(slug=slug, path=theme_dir, errors=tuple(errors))

    manifest = theme_dir / THEME_MANIFEST
    if not manifest.is_file():
        errors.append(gettext("The theme.json file was not found."))
    elif manifest.is_symlink():
        errors.append(gettext("theme.json cannot be a symbolic link."))
    elif manifest.stat().st_size > MAX_MANIFEST_BYTES:
        errors.append(gettext("The size of theme.json exceeds the limit."))
    else:
        try:
            loaded = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
            else:
                errors.append(gettext("theme.json must be a JSON object."))
        except (ValueError, UnicodeDecodeError) as exc:
            errors.append((gettext("theme.json is not valid: %(exc)s") % {"exc": exc}))

    name = version = author = homepage = description = screenshot = ravix = ""
    names: dict = {}
    descriptions: dict = {}
    home_sections = tuple(HOME_SECTION_TYPES)
    studio_full_width = False
    if data:
        if data.get("api_version") != API_VERSION:
            errors.append((gettext("api_version must be %(API_VERSION)s (the theme was built for another version of the contract).") % {"API_VERSION": API_VERSION}))
        if data.get("slug") != slug:
            errors.append(gettext("The “slug” value in theme.json must match the theme folder name exactly."))
        from apps.core.regional import parse_localized_text

        name, names = parse_localized_text(data.get("name"), "name", errors, max_len=60, required=True)
        version = _str_field(data, "version", errors, max_len=40)
        if version and not VERSION_RE.match(version):
            errors.append(gettext("“version” must be in semantic versioning form (e.g. 1.0.0)."))
        author = _str_field(data, "author", errors, max_len=80)
        homepage = _str_field(data, "homepage", errors, max_len=200, required=False)
        if homepage and not re.match(r"^https?://[^\s<>\"']+$", homepage):
            errors.append(gettext("homepage: not a valid http(s) address."))
            homepage = ""
        data = upgrade_legacy_key(data, f"theme.json ({slug})")
        ravix = _str_field(data, "ravix", errors, max_len=50, required=False)
        if ravix:
            from apps.plugins.versions import is_valid_spec

            if not is_valid_spec(ravix):
                errors.append(gettext("“ravix” must be a version range (e.g. >=0.6,<1.0)."))
                ravix = ""
        description, descriptions = parse_localized_text(data.get("description"), "description", errors, max_len=300)
        screenshot = _str_field(data, "screenshot", errors, max_len=100, required=False)
        if screenshot:
            if ".." in screenshot or screenshot.startswith("/") or screenshot.lower().rsplit(".", 1)[-1] not in SCREENSHOT_EXTENSIONS:
                errors.append(gettext("The “screenshot” path is invalid (it must be a png/jpg/webp file inside static)."))
                screenshot = ""
            elif not (theme_dir / "static" / screenshot).is_file():
                screenshot = ""  # تصویر معرفی اختیاری است؛ نبودنش خطا نیست
        supports = data.get("supports")
        if isinstance(supports, dict) and isinstance(supports.get("home_sections"), list):
            declared = tuple(s for s in supports["home_sections"] if s in HOME_SECTION_TYPES)
            if declared:
                home_sections = declared
        if isinstance(supports, dict):
            studio_full_width = supports.get("studio_full_width") is True

    if theme_dir.is_dir():
        if not (theme_dir / "templates").is_dir():
            errors.append(gettext("The templates folder does not exist."))
        else:
            _scan_tree(theme_dir, errors)
            _scan_templates(theme_dir, errors)
    else:
        errors.append(gettext("The theme folder does not exist."))

    return ThemeInfo(
        slug=slug,
        path=theme_dir,
        name=name,
        version=version,
        author=author,
        homepage=homepage,
        description=description,
        names=names,
        descriptions=descriptions,
        screenshot=screenshot,
        home_sections=home_sections,
        ravix=ravix,
        studio_full_width=studio_full_width,
        errors=tuple(dict.fromkeys(errors)),
    )
