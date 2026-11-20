"""Where an intent's flow checkpoint lives: the checkpoint key, its default path and discovery across worktrees.

``save_checkpoint`` writes to the default path built here, so a ``--from-stage`` resume without ``--checkpoint`` reads
exactly the file the last run wrote.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from typing import Any


def get_default_checkpoint_path(target_worktree: str, slug: str) -> str:
    """Derive the canonical checkpoint file path for an intent slug."""
    clean_slug = re.sub(r"[^a-zA-Z0-9_-]", "-", slug).strip("-") or "default"
    return os.path.join(target_worktree, ".holon", "flow", f"checkpoint-{clean_slug}.json")


def _checkpoint_names(intent_data: dict[str, Any]) -> tuple[str, str]:
    """The intent's checkpoint ``(slug, key)``: the slug is recorded in the checkpoint, the key names its file."""
    slug = intent_data.get("slug")
    if not slug and intent_data.get("branch"):
        slug = intent_data["branch"].replace("I-", "").split("-", 2)[-1]
    if not slug:
        slug = "default"
    # A sub-intent's slug can repeat its parent's or a sibling's, so its checkpoint is keyed on its unique branch.
    if intent_data.get("parent_intent_id") and intent_data.get("branch"):
        return slug, str(intent_data["branch"]).removesuffix("/_")
    return slug, slug


def _find_intent_checkpoint(intent_data: dict[str, Any], worktrees: Iterable[str]) -> str | None:
    """The first existing default checkpoint for the intent in ``worktrees`` (in order), or ``None``."""
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
