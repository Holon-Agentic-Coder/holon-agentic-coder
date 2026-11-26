from __future__ import annotations

import os
from collections.abc import Mapping

from sandbox_executor.ledger.fragments import (
    FRAGMENT_SUFFIX,
    LEDGER_DIR_REL,
    LEDGER_NAMES,
    UNSCOPED_ROOT,
    iter_fragment_files,
)

PROMOTE_EVERY_ENV = "HOLON_LEDGER_PROMOTE_EVERY"
DEFAULT_PROMOTE_EVERY = 10


def promote_every_from_env(environ: Mapping[str, str]) -> int:
    raw = environ.get(PROMOTE_EVERY_ENV, "").strip()
    if not raw:
        return DEFAULT_PROMOTE_EVERY
    try:
        every = int(raw)
    except ValueError:
        every = 0
    if every < 1:
        raise ValueError(f"{PROMOTE_EVERY_ENV} must be a positive integer, got {raw!r}")
    return every


def canonical_ledger_paths(repo_root: str) -> list[str]:
    return [os.path.join(repo_root, LEDGER_DIR_REL, name) for name in LEDGER_NAMES]


def pending_root_intents(repo_root: str) -> frozenset[str]:
    stems = {
        os.path.basename(path).removesuffix(FRAGMENT_SUFFIX)
        for canonical_path in canonical_ledger_paths(repo_root)
        for path in iter_fragment_files(canonical_path)
    }
    return frozenset(stems - {UNSCOPED_ROOT})
