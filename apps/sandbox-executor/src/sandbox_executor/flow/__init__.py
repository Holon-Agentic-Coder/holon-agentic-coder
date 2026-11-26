"""Holon Flow Orchestration Pipeline Engine.

Automates the complete 5-stage Holon Flow lifecycle (Stages 1-5).
The engine is split into focused modules; see docs/architecture.md (Flow engine module map).

The re-exports resolve lazily on first access, so importing one flow submodule (as the container entrypoints do)
does not load the host engine (``flow.pipeline``, ``flow.stages``) through this package.
"""

from __future__ import annotations

import importlib
from typing import Any

# A submodule import binds the submodule over a same-named package attribute, so these two functions are bound
# eagerly, after their (light) modules load, to keep ``flow.admission_lock`` and ``flow.worktree_checkpoints`` the
# functions.
from sandbox_executor.flow.admission_lock import admission_lock as admission_lock
from sandbox_executor.flow.lazy_exports import LAZY_EXPORTS
from sandbox_executor.flow.worktree_checkpoints import worktree_checkpoints as worktree_checkpoints

_OWNER = {name: module for module, names in LAZY_EXPORTS.items() for name in names.split()}


def __getattr__(name: str) -> Any:
    """Resolve a re-exported name from its owning module on each access, so a patch on the owner is always seen."""
    module = _OWNER.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)


def __dir__() -> list[str]:
    return sorted({*globals(), *_OWNER})


# Every lazily re-exported name plus the two functions bound eagerly above.
__all__ = sorted({*_OWNER, "admission_lock", "worktree_checkpoints"})
