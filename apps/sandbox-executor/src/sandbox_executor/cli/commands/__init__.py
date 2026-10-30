"""Dynamic discovery of CLI command modules."""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType


def discover() -> list[ModuleType]:
    """Discover all command modules in this package, sorted by ORDER then name."""
    modules: list[ModuleType] = []
    for module_info in pkgutil.iter_modules(__path__):
        name = module_info.name
        if name.startswith("_"):
            continue
        mod = importlib.import_module(f".{name}", package=__name__)
        if hasattr(mod, "register") and hasattr(mod, "run"):
            modules.append(mod)

    return sorted(modules, key=lambda m: (getattr(m, "ORDER", 999), m.__name__))
