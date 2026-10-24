"""
بارگذار افزونه‌ها — **تنها** جایی که کد افزونه import می‌شود.

تضمین‌ها:
- فقط افزونه‌ای که رکورد «فعال» در دیتابیس دارد و اثرانگشت پوشه‌اش با اثرانگشت تأییدشدهٔ مدیر برابر است بارگذاری می‌شود؛
- import از مسیر ``ravix_plugins.<slug>`` فقط با یابندهٔ اختصاصی ممکن است که هر بار اثرانگشت را بازبینی می‌کند و بایت‌کد
  (``.pyc``) را نادیده می‌گیرد (کد منبع تأییدشده اجرا می‌شود، نه یک کش دست‌کاری‌شده)؛
- تغییر کد پس از تأیید (دستکاری) ← افزونه فوراً غیرفعال و رویداد «tamper» ثبت می‌شود؛
- هر خطای بارگذاری یا اجرا در همین‌جا مهار و ثبت می‌شود و به پنل نمی‌رسد.
"""
from django.utils.translation import gettext
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import logging
import sys
import threading
import time
import types
from dataclasses import dataclass, field
from pathlib import Path

from django.apps import AppConfig, apps
from django.conf import settings

from . import events, state
from .integrity import IntegrityError, fingerprint

logger = logging.getLogger("ravix.plugins")

NAMESPACE = "ravix_plugins"
VERIFY_INTERVAL = 5.0  # ثانیه؛ فاصلهٔ بازبینی اثرانگشت برای افزونه‌های بارگذاری‌شده
_lock = threading.RLock()
_roots: dict[str, tuple[Path, str]] = {}  # slug -> (ریشه، اثرانگشت تأییدشده)؛ فقط این‌ها import می‌شوند
_loaded: dict[str, "Loaded"] = {}
_dev_slugs: set[str] = set()
_module_fp: dict[str, str] = {}  # slug -> اثرانگشتی که ماژول‌های فعلی در حافظه با آن import شده‌اند
_warned: set[str] = set()


@dataclass
class Loaded:
    slug: str
    info: state.ActiveInfo
    root: Path
    fingerprint: str
    api: object
    pages: dict = field(default_factory=dict)
    routes: dict = field(default_factory=dict)
    hooks: dict = field(default_factory=dict)
    verified_at: float = 0.0
    app_label: str = ""


class PluginLoadError(Exception):
    pass


# ------------------------------------------------------------------ یابنده و بارگذار منبع‌محض
class _SourceOnlyLoader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):  # هرگز از __pycache__ نمی‌خواند و چیزی در آن نمی‌نویسد
        path = self.get_filename(fullname)
        return self.source_to_code(self.get_data(path), path)


class _PluginFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(NAMESPACE + "."):
            return None
        parts = fullname.split(".")
        slug = parts[1]
        entry = _roots.get(slug)
        if entry is None:
            raise ImportError((gettext("Plugin “%(slug)s” has not been approved/loaded.") % {"slug": slug}))
        root, approved = entry
        if slug not in _dev_slugs:
            try:
                if fingerprint(root) != approved:
                    raise ImportError((gettext("The code of plugin “%(slug)s” differs from the approved version.") % {"slug": slug}))
            except IntegrityError as exc:
                raise ImportError(str(exc))
        rel = Path(*parts[2:]) if len(parts) > 2 else Path()
        base = (root / rel).resolve()
        try:
            base.relative_to(root.resolve())
        except ValueError:
            return None
        if (base / "__init__.py").is_file():
            return importlib.util.spec_from_file_location(fullname, base / "__init__.py", loader=_SourceOnlyLoader(fullname, str(base / "__init__.py")),
                                                          submodule_search_locations=[str(base)])
        mod = base.with_suffix(".py")
        if len(parts) > 2 and mod.is_file():
            return importlib.util.spec_from_file_location(fullname, mod, loader=_SourceOnlyLoader(fullname, str(mod)))
        return None


def _install_finder():
    if not any(isinstance(f, _PluginFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _PluginFinder())
    if NAMESPACE not in sys.modules:
        pkg = types.ModuleType(NAMESPACE)
        pkg.__path__ = []
        sys.modules[NAMESPACE] = pkg


def _purge_modules(slug):
    prefix = f"{NAMESPACE}.{slug}"
    for name in [n for n in sys.modules if n == prefix or n.startswith(prefix + ".")]:
        del sys.modules[name]


# ------------------------------------------------------------------ ثبت پویای اپ (مدل‌ها و مهاجرت‌ها)
def _register_app(slug, pkg):
    label = f"plugin_{slug}"
    existing = apps.app_configs.get(label)
    if existing is not None:
        return existing
    cfg = AppConfig(f"{NAMESPACE}.{slug}", pkg)
    cfg.label = label
    cfg.verbose_name = slug
    cfg.apps = apps
    cfg.default_auto_field = "django.db.models.BigAutoField"
    cfg.models = apps.all_models[label]
    apps.app_configs[label] = cfg
    apps.clear_cache()
    try:
        cfg.import_models()
        apps.clear_cache()
        _check_models(cfg)
    except Exception:
        _unregister_app(label)
        raise
    return cfg


def _unregister_app(label):
    apps.app_configs.pop(label, None)
    apps.all_models.pop(label, None)
    apps.clear_cache()


def _check_models(cfg):
    allowed_apps = {cfg.label, "accounts"}
    for model in cfg.get_models():
        if not model._meta.db_table.startswith(cfg.label + "_"):
            raise PluginLoadError((gettext("The table of model %(name)s must start with “%(label)s_” (db_table was tampered with).") % {"name": model.__name__, "label": cfg.label}))
        for f in model._meta.get_fields():
            rel = getattr(f, "related_model", None)
            if f.is_relation and rel is not None and rel._meta.app_label not in allowed_apps:
                raise PluginLoadError((gettext("Model %(name)s depends on a core model (%(label)s); only its own models and the user model are allowed.") % {"name": model.__name__, "label": rel._meta.label}))


# ------------------------------------------------------------------ بارگذاری
def locate(slug):
    from .discovery import plugin_dirs

    for base in plugin_dirs():
        path = base / slug
        try:
            if path.is_dir() and not path.is_symlink():
                return path
        except OSError:  # unreadable folder: treated as absent (discovery reports it)
            continue
    return None


def _resolve(pkg_name, ref):
    module_name, attr = ref.split(":")
    module = importlib.import_module(f"{pkg_name}.{module_name}")
    fn = getattr(module, attr, None)
    if not callable(fn):
        raise PluginLoadError((gettext("“%(ref)s” is not callable.") % {"ref": ref}))
    return fn


def _do_load(slug, info, root, fp) -> Loaded:
    from .api import PluginAPI

    _install_finder()
    _roots[slug] = (root, fp)
    pkg_name = f"{NAMESPACE}.{slug}"
    pkg = importlib.import_module(pkg_name)
    _module_fp[slug] = fp
    label = ""
    if info.manifest.models:
        label = _register_app(slug, pkg).label
    api = PluginAPI(slug)
    locale_dir = root / "locale"
    if locale_dir.is_dir() and not locale_dir.is_symlink():  # the plugin's own translations (core strings always win)
        from apps.core import catalogs

        catalogs.register(f"plugin:{slug}", locale_dir)
    m = info.manifest
    loaded = Loaded(slug=slug, info=info, root=root, fingerprint=fp, api=api, verified_at=time.monotonic(), app_label=label)
    for p in m.admin_pages:
        loaded.pages[p["key"]] = _resolve(pkg_name, p["view"])
    for r in m.public_routes:
        loaded.routes[r["path"]] = _resolve(pkg_name, r["view"])
    for h in m.hooks:
        loaded.hooks.setdefault(h["name"], []).append((h["priority"], _resolve(pkg_name, h["handler"])))
    for fns in loaded.hooks.values():
        fns.sort(key=lambda x: x[0])
    return loaded


def get(slug) -> Loaded | None:
    """افزونهٔ بارگذاری‌شده و تأییدشده یا None. هرگز استثنا نمی‌دهد."""
    info = state.get_active(slug)
    if info is None:
        from apps.core import catalogs

        catalogs.unregister(f"plugin:{slug}")  # a disabled plugin contributes no translations
        return None
    cur = _loaded.get(slug)
    now = time.monotonic()
    if cur and cur.fingerprint == info.fingerprint and now - cur.verified_at < VERIFY_INTERVAL:
        return cur
    with _lock:
        try:
            root = locate(slug)
            if root is None:
                events.disable(slug, kind="missing", message=gettext("The plugin folder was not found; it was disabled."))
                _loaded.pop(slug, None)
                return None
            try:
                fp = fingerprint(root)
            except IntegrityError as exc:
                fp = ""
                events.disable(slug, kind="tamper", message=(gettext("The plugin structure became invalid: %(exc)s") % {"exc": exc}))
            if fp != info.fingerprint:
                if fp:
                    events.disable(slug, kind="tamper", message=gettext("The plugin code changed after the administrator’s approval; it was disabled until re-approved."))
                _loaded.pop(slug, None)
                _roots.pop(slug, None)
                return None
            cur = _loaded.get(slug)
            if cur and cur.fingerprint == fp:
                cur.verified_at = now
                cur.info = info
                return cur
            old = _module_fp.get(slug)
            if old is not None and old != fp:  # در این پردازه کدِ قدیمیِ تأییدشده import شده است
                if app_config(slug) is not None:  # مدل‌ها را نمی‌توان با خیال راحت دوباره ثبت کرد
                    if slug not in _warned:
                        _warned.add(slug)
                        events.log_event(slug, "restart_required", gettext("The new approved version has models; restart the server to apply them."), level="warning")
                    _loaded.pop(slug, None)
                    return None
                _purge_modules(slug)
                _module_fp.pop(slug, None)
            loaded = _do_load(slug, info, root, fp)
            _loaded[slug] = loaded
            return loaded
        except Exception as exc:  # noqa: BLE001 - خطای افزونه مهار می‌شود
            _purge_modules(slug)
            _module_fp.pop(slug, None)
            _roots.pop(slug, None)
            _loaded.pop(slug, None)
            events.record_error(slug, "load", exc)
            events.disable(slug, kind="load_failed", message=(gettext("Loading the plugin failed and it was disabled: %(exc)s") % {"exc": exc})[:400])
            return None


def load_for_install(slug, root, fp):
    """برای مهاجرت هنگام نصب: کد با اثرانگشت تأییدشده بارگذاری می‌شود (مدل‌ها ثبت می‌شوند) ولی هنوز «فعال» نیست."""
    from .manifest import read_manifest

    manifest = read_manifest(root, expected_slug=slug)
    info = state.ActiveInfo(slug=slug, manifest=manifest, fingerprint=fp, config={})
    with _lock:
        if slug in _loaded:
            return _loaded[slug]
        loaded = _do_load(slug, info, root, fp)
        _loaded[slug] = loaded
        return loaded


def load_for_development(slug):
    """فقط برای توسعه‌دهنده (DEBUG): بارگذاری بدون تأیید، برای makemigrations. در محیط تولید رد می‌شود."""
    if not settings.DEBUG:
        raise PluginLoadError(gettext("This command is only allowed in DEBUG mode."))
    root = locate(slug)
    if root is None:
        raise PluginLoadError(gettext("The plugin folder was not found."))
    _dev_slugs.add(slug)
    return load_for_install(slug, root, "dev")


def app_config(slug):
    return apps.app_configs.get(f"plugin_{slug}")


def unload(slug):
    """افزونه را از حافظهٔ این پردازه خارج می‌کند (کد بدون مدل کاملاً؛ افزونهٔ دارای مدل تا راه‌اندازی مجدد ماژول‌هایش می‌ماند)."""
    from apps.core import catalogs

    catalogs.unregister(f"plugin:{slug}")
    with _lock:
        _loaded.pop(slug, None)
        if app_config(slug) is None:
            _purge_modules(slug)
            _module_fp.pop(slug, None)


def loaded_slugs():
    return list(_loaded)


def reset_for_tests():
    """فقط برای تست‌ها: همهٔ افزونه‌های بارگذاری‌شده را فراموش می‌کند (اپ‌های پویا از رجیستری جنگو حذف می‌شوند)."""
    with _lock:
        from apps.core import catalogs

        for slug in list(_loaded) + list(_roots):
            _purge_modules(slug)
            _unregister_app(f"plugin_{slug}")
            catalogs.unregister(f"plugin:{slug}")
        _loaded.clear()
        _roots.clear()
        _dev_slugs.clear()
        _module_fp.clear()
        _warned.clear()
        state.invalidate()
