# why: re-exports resolve lazily so importing one flow submodule from a container entrypoint never loads the host engine
from __future__ import annotations

import importlib
from typing import Any

# why: a submodule import rebinds a same-named package attribute, so these two functions are bound eagerly
from sandbox_executor.flow.admission_lock import admission_lock as admission_lock
from sandbox_executor.flow.lazy_exports import LAZY_EXPORTS
from sandbox_executor.flow.worktree_checkpoints import worktree_checkpoints as worktree_checkpoints

_OWNER = {name: module for module, names in LAZY_EXPORTS.items() for name in names.split()}


# why: names resolve on each access so a patch on the owner module is always seen
def __getattr__(name: str) -> Any:
    module = _OWNER.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)


def __dir__() -> list[str]:
    return sorted({*globals(), *_OWNER})


__all__ = sorted({*_OWNER, "admission_lock", "worktree_checkpoints"})
