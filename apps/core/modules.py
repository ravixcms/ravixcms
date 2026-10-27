"""Built-in modules ("built-in plugins"): optional features that ship inside Ravix (Ravix Shop today, others later) and are switched on and
off from the panel, like a WordPress plugin, without editing ``ravix.env`` or restarting.

Three layers decide whether a module is live:

1. **Installed** – the Django app is in ``INSTALLED_APPS`` (for Ravix Shop: ``RAVIX_SHOP_ENABLED``). When it is not, the server forces the module
   off ("server lock"): the panel shows it, but the switch is disabled.
2. **Enabled** – the superadmin's switch, stored in the database (``ModuleState``). Absent row → ``settings.BUILTIN_MODULES_DEFAULT_ON``
   (false in production: a new module waits for its first activation; the migration of an existing site switches on what it already used).
3. A disabled module is *hidden, never deleted*: its menu items, public and panel routes, API, plugin hooks and health warnings disappear; its
   tables and rows stay untouched, and enabling it again brings everything back.

To add a module: ``register_module(ModuleDef(...))`` (see ``apps/core/builtin_modules.py``), give its nav items ``module="<key>"``, name its URL
namespaces / API prefixes / hook prefixes in the definition, and optionally supply ``wizard_steps`` for the setup guide.
"""
from dataclasses import dataclass, field
from typing import Callable

from django.apps import apps as django_apps
from django.conf import settings
from django.db import OperationalError, ProgrammingError


@dataclass(frozen=True)
class WizardStep:
    key: str
    title: object  # lazy, translated
    description: object
    url: str  # where the step is done (a panel page)
    done: bool = False


@dataclass(frozen=True)
class ModuleDef:
    key: str
    name: object  # lazy, translated
    description: object
    version: str
    app_label: str  # Django app label; the module is "installed" when it is in INSTALLED_APPS
    icon: str = "brand/ravix-icon.svg"  # static path
    namespaces: tuple[str, ...] = ()  # URL namespaces hidden while disabled
    path_prefixes: tuple[str, ...] = ()  # raw path prefixes hidden while disabled (API)
    hook_prefixes: tuple[str, ...] = ()  # plugin hooks that stop firing while disabled
    lock_hint: object = ""  # how the server forces it off (shown when the switch is locked)
    settings_url_name: str = ""
    wizard_steps: Callable | None = None  # (request) -> list[WizardStep]; None = no setup guide


_registry: dict[str, ModuleDef] = {}


def register_module(module: ModuleDef) -> None:
    _registry[module.key] = module


def all_modules() -> list[ModuleDef]:
    return list(_registry.values())


def get_module(key: str) -> ModuleDef | None:
    return _registry.get(key)


def is_installed(key: str) -> bool:
    mod = _registry.get(key)
    return bool(mod) and django_apps.is_installed(f"apps.{mod.app_label}")


def server_locked(key: str) -> bool:
    """The server forces the module off (its app is not installed). The panel cannot override it."""
    return key in _registry and not is_installed(key)


def _row(key):
    from .models import ModuleState

    try:
        return ModuleState.objects.filter(key=key).first()
    except (OperationalError, ProgrammingError):  # before migrate
        return None


def state_row(key):
    return _row(key)


def is_enabled(key: str) -> bool:
    """Live right now: installed and switched on."""
    if key not in _registry or not is_installed(key):
        return False
    row = _row(key)
    if row is None:
        return bool(getattr(settings, "BUILTIN_MODULES_DEFAULT_ON", False))
    return row.enabled


def set_enabled(key: str, enabled: bool, user=None):
    """Persist the switch. Raises ``ValueError`` if the module is unknown or locked off by the server."""
    from .models import ModuleState

    if key not in _registry:
        raise ValueError("unknown module")
    if enabled and server_locked(key):
        raise ValueError("locked by the server")
    row, _ = ModuleState.objects.get_or_create(key=key)
    row.enabled = bool(enabled)
    row.changed_by = user if getattr(user, "pk", None) else None
    row.save()
    return row


def set_setup(key: str, value: str):
    from .models import ModuleState

    row, _ = ModuleState.objects.get_or_create(key=key, defaults={"enabled": is_enabled(key)})
    row.setup = value
    row.save(update_fields=["setup", "changed_at"])
    return row


def module_for_namespaces(namespaces) -> ModuleDef | None:
    names = set(namespaces or ())
    for mod in _registry.values():
        if names & set(mod.namespaces):
            return mod
    return None


def module_for_path(path: str) -> ModuleDef | None:
    for mod in _registry.values():
        if any(path.startswith(p) for p in mod.path_prefixes):
            return mod
    return None


def hook_allowed(name: str) -> bool:
    """False for a hook that belongs to a disabled module."""
    for mod in _registry.values():
        if any(name.startswith(p) for p in mod.hook_prefixes):
            return is_enabled(mod.key)
    return True


def wizard(key: str, request) -> list[WizardStep]:
    mod = _registry.get(key)
    if mod is None or mod.wizard_steps is None or not is_installed(key):
        return []
    return list(mod.wizard_steps(request))
