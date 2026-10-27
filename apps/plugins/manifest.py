"""
قرارداد افزونه: فایل ``plugin.json`` در ریشهٔ پوشهٔ افزونه. این ماژول فقط دادهٔ JSON را می‌خواند و اعتبارسنجی می‌کند؛
**هیچ کد افزونه‌ای اینجا import یا اجرا نمی‌شود.**
"""
from django.utils.translation import gettext
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from apps.accounts.permissions import CAPABILITIES
from apps.core.deprecation import upgrade_legacy_key
from apps.core.navigation import SECTIONS
from apps.core.templatetags.ravix_ui import ICONS
from apps.core.version import PLUGIN_API_VERSION

from . import versions
from .hooks_registry import HOOKS
from .settings_schema import parse_field

MANIFEST_FILE = "plugin.json"
MAX_MANIFEST_BYTES = 64 * 1024
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{2,30}$")
RESERVED_SLUGS = frozenset({"log", "x", "events", "core", "ravix", "plugins", "admin", "static"})
_MODULE_REF = re.compile(r"^([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*):([A-Za-z_][A-Za-z0-9_]*)$")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,29}$")
_ROUTE_RE = re.compile(r"^(?:[a-z0-9][-a-z0-9_]*(?:/[a-z0-9][-a-z0-9_]*)*/?)?$")
# First path segments a site route may never take: they belong to the panel, the system or the site itself.
SITE_ROUTE_RESERVED = frozenset({"panel", "accounts", "api", "django-admin", "static", "theme-assets", "media", "ext", "registry", "install",
                                 "healthz", "jsi18n", "i18n", "sitemap", "robots", "blog", "category", "tag", "search", "feed", "post", "shop", "cart", "checkout"})
TOP_KEYS = {"slug", "name", "version", "description", "author", "homepage", "api_version", "ravix", "requires", "capabilities",
            "settings", "admin_pages", "public_routes", "site_routes", "hooks", "models", "category", "license"}


class ManifestError(Exception):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__(gettext("; ").join(self.errors))


@dataclass(frozen=True)
class Manifest:
    slug: str
    name: str
    version: str
    description: str
    author: str
    homepage: str
    api_version: int
    ravix: str
    requires: dict
    capabilities: dict
    settings: tuple
    admin_pages: tuple
    public_routes: tuple
    hooks: tuple
    models: bool
    site_routes: tuple = ()
    names: dict = field(default_factory=dict)  # multilingual «name»: {language code: text} (empty for a plain string)
    descriptions: dict = field(default_factory=dict)
    category: str = ""  # نامک دستهٔ مخزن (اختیاری)
    license: str = ""

    @property
    def label(self) -> str:
        """برچسب اپ جنگو و پیشوند جدول‌ها: plugin_<slug>."""
        return f"plugin_{self.slug}"

    @property
    def display_name(self) -> str:
        """The name in the active language (falls back to the default text)."""
        from apps.core.regional import pick_localized

        return pick_localized(self.name, self.names)

    @property
    def display_description(self) -> str:
        from apps.core.regional import pick_localized

        return pick_localized(self.description, self.descriptions)

    def to_json(self) -> dict:
        return {
            "slug": self.slug, "name": dict(self.names) or self.name, "version": self.version,
            "description": dict(self.descriptions) or self.description, "author": self.author,
            "homepage": self.homepage, "api_version": self.api_version, "ravix": self.ravix, "requires": dict(self.requires),
            "capabilities": dict(self.capabilities), "settings": [f.to_json() for f in self.settings],
            "admin_pages": [dict(p) for p in self.admin_pages], "public_routes": [dict(r) for r in self.public_routes],
            "site_routes": [dict(r) for r in self.site_routes],
            "hooks": [dict(h) for h in self.hooks], "models": self.models,
            "category": self.category, "license": self.license,
        }

    def page(self, key):
        return next((p for p in self.admin_pages if p["key"] == key), None)

    def route(self, path):
        return next((r for r in self.public_routes if r["path"] == path), None)

    def site_route(self, path):
        return next((r for r in self.site_routes if r["path"] == path), None)

    def module_refs(self):
        """همهٔ ارجاع‌های «ماژول:نام» (برای import یکجا هنگام بارگذاری تأییدشده)."""
        refs = [p["view"] for p in self.admin_pages] + [r["view"] for r in self.public_routes] + [r["view"] for r in self.site_routes] + [h["handler"] for h in self.hooks]
        return refs


def _str(raw, key, errors, *, required=False, max_len=200, default=""):
    value = raw.get(key, default)
    if not isinstance(value, str):
        errors.append((gettext("%(key)s: must be text.") % {"key": key}))
        return default
    value = value.strip()
    if required and not value:
        errors.append((gettext("%(key)s: required.") % {"key": key}))
    if len(value) > max_len:
        errors.append((gettext("%(key)s: at most %(max_len)s characters.") % {"key": key, "max_len": max_len}))
    return value


def _own_cap(slug, cap):
    return cap.startswith(slug + ".") and re.match(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]{0,29}$", cap) is not None


def parse_manifest(raw, *, expected_slug: str | None = None) -> Manifest:
    errors: list[str] = []
    if not isinstance(raw, dict):
        raise ManifestError([gettext("plugin.json must be a JSON object.")])
    raw = upgrade_legacy_key(raw, f"plugin.json ({raw.get('slug', '?')})")  # «ravin» ← نام قدیمی کلید
    for k in raw:
        if k not in TOP_KEYS:
            errors.append((gettext("%(k)s: unknown key.") % {"k": k}))
    slug = raw.get("slug")
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        errors.append(gettext("slug: lowercase English letters, digits and _ (3 to 31 characters, starting with a letter)."))
        slug = ""
    elif slug in RESERVED_SLUGS:
        errors.append((gettext("slug: “%(slug)s” is reserved.") % {"slug": slug}))
    if expected_slug is not None and slug and slug != expected_slug:
        errors.append((gettext("slug: must match the folder name (%(expected_slug)s).") % {"expected_slug": expected_slug}))
    from apps.core.regional import parse_localized_text

    name, names = parse_localized_text(raw.get("name"), "name", errors, required=True, max_len=80)
    version = _str(raw, "version", errors, required=True, max_len=20)
    if version and not versions.is_valid_version(version):
        errors.append(gettext("version: must look like 1.2.3."))
    description, descriptions = parse_localized_text(raw.get("description"), "description", errors, max_len=500)
    author = _str(raw, "author", errors, max_len=100)
    homepage = _str(raw, "homepage", errors, max_len=200)
    if homepage and not re.match(r"^https?://[^\s<>\"']+$", homepage):
        errors.append(gettext("homepage: not a valid http(s) address."))
    api = raw.get("api_version")
    if isinstance(api, bool) or not isinstance(api, int):
        errors.append(gettext("api_version: an integer is required."))
        api = 0
    elif api != PLUGIN_API_VERSION:
        errors.append((gettext("api_version: this CMS supports contract version %(PLUGIN_API_VERSION)s (plugin: %(api)s).") % {"PLUGIN_API_VERSION": PLUGIN_API_VERSION, "api": api}))
    ravix = _str(raw, "ravix", errors, required=True, max_len=50)
    if ravix and not versions.is_valid_spec(ravix):
        errors.append(gettext("ravix: invalid version range (e.g. >=0.5.0,<1.0.0)."))

    requires = raw.get("requires", {})
    if not isinstance(requires, dict):
        errors.append(gettext("requires: must be an object {slug: range}."))
        requires = {}
    for dep, spec in requires.items():
        if not SLUG_RE.match(str(dep)) or not isinstance(spec, str) or not versions.is_valid_spec(spec):
            errors.append((gettext("requires.%(dep)s: invalid.") % {"dep": dep}))
        if dep == slug:
            errors.append(gettext("requires: dependency on the plugin itself."))

    caps = raw.get("capabilities", {})
    if not isinstance(caps, dict):
        errors.append(gettext("capabilities: must be an object {name: label}."))
        caps = {}
    for cap, label in caps.items():
        if not _own_cap(slug, str(cap)):
            errors.append((gettext("capabilities.%(cap)s: must start with “%(slug)s.” (the plugin namespace); core permissions cannot be redefined.") % {"cap": cap, "slug": slug}))
        elif cap in CAPABILITIES:
            errors.append((gettext("capabilities.%(cap)s: conflicts with a core permission.") % {"cap": cap}))
        if not isinstance(label, str) or not label.strip() or len(label) > 100:
            errors.append((gettext("capabilities.%(cap)s: a label is required.") % {"cap": cap}))

    def cap_ok(cap):
        return cap in CAPABILITIES or cap in caps or cap == "plugins.manage"

    schema = []
    raw_settings = raw.get("settings", [])
    if not isinstance(raw_settings, list) or len(raw_settings) > 40:
        errors.append(gettext("settings: a list of at most 40 items is required."))
        raw_settings = []
    seen = set()
    for i, item in enumerate(raw_settings):
        f = parse_field(item, i, errors)
        if f is not None:
            if f.key in seen:
                errors.append((gettext("settings[%(i)s].key: duplicate.") % {"i": i}))
            seen.add(f.key)
            schema.append(f)

    pages, seen = [], set()
    raw_pages = raw.get("admin_pages", [])
    if not isinstance(raw_pages, list) or len(raw_pages) > 20:
        errors.append(gettext("admin_pages: a list of at most 20 items is required."))
        raw_pages = []
    for i, p in enumerate(raw_pages):
        where = f"admin_pages[{i}]"
        if not isinstance(p, dict):
            errors.append((gettext("%(where)s: must be an object.") % {"where": where}))
            continue
        for k in p:
            if k not in {"key", "title", "view", "capability", "menu", "icon", "section", "order"}:
                errors.append((gettext("%(where)s.%(k)s: unknown key.") % {"where": where, "k": k}))
        key, title, view = p.get("key"), p.get("title"), p.get("view")
        if not isinstance(key, str) or not _KEY_RE.match(key) or key in seen:
            errors.append((gettext("%(where)s.key: invalid or duplicate.") % {"where": where}))
            continue
        seen.add(key)
        if not isinstance(title, str) or not title.strip() or len(title) > 60:
            errors.append((gettext("%(where)s.title: required (at most 60).") % {"where": where}))
            continue
        if not isinstance(view, str) or not _MODULE_REF.match(view):
            errors.append((gettext("%(where)s.view: must look like “module:function”.") % {"where": where}))
            continue
        cap = p.get("capability", "plugins.manage")
        if not isinstance(cap, str) or not cap_ok(cap):
            errors.append((gettext("%(where)s.capability: must be a core permission or a permission declared by this plugin.") % {"where": where}))
            continue
        section = p.get("section", "content")
        if section not in SECTIONS:
            errors.append((gettext("%(where)s.section: one of %(SECTIONS)s.") % {"where": where, "SECTIONS": ', '.join(SECTIONS)}))
            continue
        order = p.get("order", 100)
        if isinstance(order, bool) or not isinstance(order, int) or not 0 <= order <= 999:
            errors.append((gettext("%(where)s.order: a number from 0 to 999.") % {"where": where}))
            continue
        icon = p.get("icon", "circle")
        pages.append({"key": key, "title": title.strip(), "view": view, "capability": cap, "menu": bool(p.get("menu", True)),
                      "icon": icon if icon in ICONS else "circle", "section": section, "order": order})

    routes, seen = [], set()
    raw_routes = raw.get("public_routes", [])
    if not isinstance(raw_routes, list) or len(raw_routes) > 20:
        errors.append(gettext("public_routes: a list of at most 20 items is required."))
        raw_routes = []
    for i, r in enumerate(raw_routes):
        where = f"public_routes[{i}]"
        if not isinstance(r, dict):
            errors.append((gettext("%(where)s: must be an object.") % {"where": where}))
            continue
        for k in r:
            if k not in {"path", "view", "methods"}:
                errors.append((gettext("%(where)s.%(k)s: unknown key.") % {"where": where, "k": k}))
        path, view, methods = r.get("path", ""), r.get("view"), r.get("methods", ["GET"])
        if not isinstance(path, str) or not _ROUTE_RE.match(path) or path in seen:
            errors.append((gettext("%(where)s.path: invalid or duplicate (lowercase letters, digits, - _ and /).") % {"where": where}))
            continue
        seen.add(path)
        if not isinstance(view, str) or not _MODULE_REF.match(view):
            errors.append((gettext("%(where)s.view: must look like “module:function”.") % {"where": where}))
            continue
        if not isinstance(methods, list) or not methods or not all(m in ("GET", "POST") for m in methods):
            errors.append((gettext("%(where)s.methods: a subset of [GET, POST].") % {"where": where}))
            continue
        routes.append({"path": path, "view": view, "methods": sorted(set(methods))})

    site_routes, seen_site = [], set()
    raw_site = raw.get("site_routes", [])
    if not isinstance(raw_site, list) or len(raw_site) > 10:
        errors.append(gettext("site_routes: a list of at most 10 items is required."))
        raw_site = []
    for i, r in enumerate(raw_site):
        where = f"site_routes[{i}]"
        if not isinstance(r, dict):
            errors.append((gettext("%(where)s: must be an object.") % {"where": where}))
            continue
        for k in r:
            if k not in {"path", "view", "methods", "language_prefix"}:
                errors.append((gettext("%(where)s.%(k)s: unknown key.") % {"where": where, "k": k}))
        path, view, methods, prefixed = r.get("path", ""), r.get("view"), r.get("methods", ["GET"]), r.get("language_prefix", True)
        if not isinstance(path, str) or not path or not _ROUTE_RE.match(path) or path.split("/")[0] in SITE_ROUTE_RESERVED or (path, bool(prefixed)) in seen_site:
            errors.append((gettext("%(where)s.path: invalid, reserved or duplicate (lowercase letters, digits, - _ and /).") % {"where": where}))
            continue
        if not isinstance(prefixed, bool):
            errors.append((gettext("%(where)s.language_prefix: must be true/false.") % {"where": where}))
            continue
        seen_site.add((path, prefixed))
        if not isinstance(view, str) or not _MODULE_REF.match(view):
            errors.append((gettext("%(where)s.view: must look like “module:function”.") % {"where": where}))
            continue
        if not isinstance(methods, list) or not methods or not all(m in ("GET", "POST") for m in methods):
            errors.append((gettext("%(where)s.methods: a subset of [GET, POST].") % {"where": where}))
            continue
        site_routes.append({"path": path, "view": view, "methods": sorted(set(methods)), "language_prefix": prefixed})

    hooks = []
    raw_hooks = raw.get("hooks", [])
    if not isinstance(raw_hooks, list) or len(raw_hooks) > 20:
        errors.append(gettext("hooks: a list of at most 20 items is required."))
        raw_hooks = []
    for i, h in enumerate(raw_hooks):
        where = f"hooks[{i}]"
        if not isinstance(h, dict) or set(h) - {"name", "handler", "priority"}:
            errors.append((gettext("%(where)s: an object with the keys name/handler/priority is required.") % {"where": where}))
            continue
        if h.get("name") not in HOOKS:
            errors.append((gettext("%(where)s.name: unknown hook (allowed: %(HOOKS)s).") % {"where": where, "HOOKS": ', '.join(HOOKS)}))
            continue
        if not isinstance(h.get("handler"), str) or not _MODULE_REF.match(h["handler"]):
            errors.append((gettext("%(where)s.handler: must look like “module:function”.") % {"where": where}))
            continue
        prio = h.get("priority", 50)
        if isinstance(prio, bool) or not isinstance(prio, int) or not 0 <= prio <= 100:
            errors.append((gettext("%(where)s.priority: a number from 0 to 100.") % {"where": where}))
            continue
        hooks.append({"name": h["name"], "handler": h["handler"], "priority": prio})

    models = raw.get("models", False)
    if not isinstance(models, bool):
        errors.append(gettext("models: must be true/false."))
        models = False
    category = _str(raw, "category", errors, max_len=30)
    if category and not re.match(r"^[a-z][a-z0-9-]{1,29}$", category):
        errors.append(gettext("category: must be an English slug (lowercase letters, digits and -)."))
    license_ = _str(raw, "license", errors, max_len=40)
    if errors:
        raise ManifestError(errors)
    return Manifest(slug=slug, name=name, version=version, description=description, author=author, homepage=homepage, api_version=api,
                    ravix=ravix, requires=dict(requires), capabilities={k: v.strip() for k, v in caps.items()}, settings=tuple(schema),
                    admin_pages=tuple(pages), public_routes=tuple(routes), site_routes=tuple(site_routes), hooks=tuple(hooks), models=models, names=names, descriptions=descriptions, category=category, license=license_)


def read_manifest(directory: Path, *, expected_slug: str | None = None) -> Manifest:
    path = Path(directory) / MANIFEST_FILE
    if path.is_symlink() or not path.is_file():
        raise ManifestError([(gettext("%(MANIFEST_FILE)s was not found.") % {"MANIFEST_FILE": MANIFEST_FILE})])
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ManifestError([(gettext("%(MANIFEST_FILE)s is larger than the allowed limit.") % {"MANIFEST_FILE": MANIFEST_FILE})])
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError([(gettext("%(MANIFEST_FILE)s is not valid JSON: %(exc)s") % {"MANIFEST_FILE": MANIFEST_FILE, "exc": exc})])
    return parse_manifest(raw, expected_slug=expected_slug)
