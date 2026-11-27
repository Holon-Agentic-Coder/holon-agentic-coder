from __future__ import annotations

import os
import re
from collections.abc import Iterable
from typing import Any


def get_default_checkpoint_path(target_worktree: str, slug: str) -> str:
    clean_slug = re.sub(r"[^a-zA-Z0-9_-]", "-", slug).strip("-") or "default"
    return os.path.join(target_worktree, ".holon", "flow", f"checkpoint-{clean_slug}.json")


def _checkpoint_names(intent_data: dict[str, Any]) -> tuple[str, str]:
    slug = intent_data.get("slug")
    if not slug and intent_data.get("branch"):
        slug = intent_data["branch"].replace("I-", "").split("-", 2)[-1]
    if not slug:
        slug = "default"
    # why: a sub-intent's slug can repeat its parent's or a sibling's, so its checkpoint is keyed on its unique branch
    if intent_data.get("parent_intent_id") and intent_data.get("branch"):
        return slug, str(intent_data["branch"]).removesuffix("/_")
    return slug, slug


def _find_intent_checkpoint(intent_data: dict[str, Any], worktrees: Iterable[str]) -> str | None:
    _, key = _checkpoint_names(intent_data)
    seen: set[str] = set()
    for worktree in worktrees:
        resolved = os.path.abspath(worktree)
        if resolved in seen:
            continue
        seen.add(resolved)
        path = get_default_checkpoint_path(worktree, key)
        if os.path.isfile(path):
            return path
    return None
