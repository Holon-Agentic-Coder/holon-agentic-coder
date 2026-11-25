from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

_DEFAULT_COMMAND_ORDER = 999


def _is_command_module(mod: ModuleType) -> bool:
    return hasattr(mod, "register") and hasattr(mod, "run")


def _command_sort_key(mod: ModuleType) -> tuple[int, str]:
    return (getattr(mod, "ORDER", _DEFAULT_COMMAND_ORDER), mod.__name__)


def discover() -> list[ModuleType]:
    modules: list[ModuleType] = []
    for module_info in pkgutil.iter_modules(__path__):
        name = module_info.name
        if name.startswith("_"):
            continue
        mod = importlib.import_module(f".{name}", package=__name__)
        if _is_command_module(mod):
            modules.append(mod)

    return sorted(modules, key=_command_sort_key)
