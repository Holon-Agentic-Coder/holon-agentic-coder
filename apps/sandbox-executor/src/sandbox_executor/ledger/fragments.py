from __future__ import annotations

import os
import re
from typing import Any

from sandbox_executor.plan_paths import bare_branch

LEDGER_DIR_REL = "holon-knowledge/ledger"
PENDING_DIR_REL = f"{LEDGER_DIR_REL}/pending"
LEDGER_NAMES = ("intents.jsonl", "plans.jsonl", "executions.jsonl")
UNSCOPED_ROOT = "_unscoped"
FRAGMENT_SUFFIX = ".jsonl"

_ROOT_BRANCH_KEYS = ("branch", "intent_branch", "plan_branch", "parent_branch")
_CHILD_SUFFIX = re.compile(r"--\d+-.*$")


def ledger_stem(ledger_name: str) -> str:
    return ledger_name.removesuffix(FRAGMENT_SUFFIX)


def ledger_name_of(path: str) -> str | None:
    name = os.path.basename(path)
    parent = os.path.dirname(os.path.normpath(path)).replace(os.sep, "/")
    if name not in LEDGER_NAMES:
        return None
    return name if parent == LEDGER_DIR_REL or parent.endswith(f"/{LEDGER_DIR_REL}") else None


def pending_dir_for(canonical_path: str) -> str:
    return os.path.join(os.path.dirname(canonical_path), "pending", ledger_stem(os.path.basename(canonical_path)))


def pending_rel_dir(ledger_name: str) -> str:
    return f"{PENDING_DIR_REL}/{ledger_stem(ledger_name)}"


def iter_fragment_files(canonical_path: str) -> list[str]:
    if ledger_name_of(canonical_path) is None:
        return []
    pending_dir = pending_dir_for(canonical_path)
    try:
        names = os.listdir(pending_dir)
    except OSError:
        return []
    return [
        os.path.join(pending_dir, name)
        for name in sorted(names)
        if name.endswith(FRAGMENT_SUFFIX) and os.path.isfile(os.path.join(pending_dir, name))
    ]


def root_intent_id(row: dict[str, Any]) -> str:
    for key in _ROOT_BRANCH_KEYS:
        value = row.get(key)
        if not isinstance(value, str):
            continue
        segment = bare_branch(value).split("/", 1)[0]
        root = _CHILD_SUFFIX.sub("", segment)
        if root:
            return root
    return UNSCOPED_ROOT


def fragment_path(canonical_path: str, row: dict[str, Any]) -> str:
    return os.path.join(pending_dir_for(canonical_path), f"{root_intent_id(row)}{FRAGMENT_SUFFIX}")
