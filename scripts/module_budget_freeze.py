"""The module budget freeze: compare the budget configuration with its merge-base copy.

The configuration is compared byte for byte with the merge base's copy (``git merge-base HEAD origin/main``). Any
difference, a reformat included, fails unless ``HOLON_ALLOW_MODULE_BUDGET_CHANGE=1``: the maintainer's explicit
instruction (the ``module-budget-change`` pull request label in CI, or the intent's ``module_budget_change`` field in
the flow). With no merge base the comparison is skipped quietly; with no copy at the merge base it is skipped with a
notice.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

ALLOW_CHANGE_ENV = "HOLON_ALLOW_MODULE_BUDGET_CHANGE"
NO_BASE = "none"
FROZEN_RULE = (
    "the budget configuration is frozen; split the module into smaller modules instead. It changes only on the "
    "maintainer's explicit instruction: the 'module-budget-change' pull request label in CI, or the intent's "
    "'module_budget_change' field in the flow"
)


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """Run ``git`` in ``root``; ``None`` when git cannot be started."""
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    except OSError:
        return None


def merge_base_copy(root: Path, config_path: Path) -> tuple[str | None, str | None]:
    """Return ``(base text, notice)`` for the configuration at ``git merge-base HEAD origin/main``.

    Both are ``None`` when there is no merge base (no remote, a shallow clone or no common ancestor): the comparison
    is skipped quietly. A notice is returned when the merge base has no copy of the configuration.
    """
    base = run_git(root, "merge-base", "HEAD", "origin/main")
    if base is None or base.returncode != 0 or not base.stdout.strip():
        return None, None
    try:
        relative = config_path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None, f"{config_path} lies outside the repository; the freeze comparison is skipped"
    shown = run_git(root, "show", f"{base.stdout.strip()}:{relative}")
    if shown is None or shown.returncode != 0:
        return None, f"{relative} has no copy at the merge base; the freeze comparison is skipped"
    return shown.stdout, None


def _override_changes(base: dict[str, Any], head: dict[str, Any]) -> list[str]:
    changes = [f"override added: {path} ({head[path]})" for path in head if path not in base]
    changes += [f"override removed: {path} ({base[path]})" for path in base if path not in head]
    for path in (path for path in head if path in base and head[path] != base[path]):
        if not isinstance(head[path], int) or not isinstance(base[path], int):
            verb = "changed"
        else:
            verb = "raised" if head[path] > base[path] else "lowered"
        changes.append(f"override {verb}: {path} ({base[path]} -> {head[path]})")
    return changes


def _key_change(key: str, base: dict[str, Any], head: dict[str, Any]) -> str | None:
    if key not in head:
        return f"{key} removed"
    if key not in base:
        return f"{key} added"
    if base[key] == head[key]:
        return None
    if key == "default_max_lines":
        return f"default_max_lines {base[key]} -> {head[key]}"
    return f"{key} changed"


def describe_change(base_text: str, head_text: str) -> list[str]:
    """Name what changed between two configuration texts; ``reformatted`` when the parsed values are equal."""
    try:
        base, head = json.loads(base_text), json.loads(head_text)
    except ValueError:
        return ["reformatted or no longer valid JSON"]
    if not isinstance(base, dict) or not isinstance(head, dict):
        return ["reformatted or no longer valid JSON"]
    keys = [*base, *(key for key in head if key not in base)]
    changes = [change for key in keys if key != "overrides" and (change := _key_change(key, base, head))]
    base_overrides, head_overrides = base.get("overrides"), head.get("overrides")
    if isinstance(base_overrides, dict) and isinstance(head_overrides, dict):
        changes += _override_changes(base_overrides, head_overrides)
    elif base_overrides != head_overrides:
        changes.append("overrides changed")
    return changes or ["reformatted"]


def freeze_messages(head_text: str, base_text: str | None, label: str) -> tuple[list[str], list[str]]:
    """Return ``(errors, notices)`` from comparing the configuration with its base copy (``None``: no base)."""
    if base_text is None or base_text == head_text:
        return [], []
    changes = "; ".join(describe_change(base_text, head_text))
    if os.environ.get(ALLOW_CHANGE_ENV) == "1":
        return [], [f"{label} differs from the merge-base copy ({changes}); authorised by {ALLOW_CHANGE_ENV}=1"]
    return [f"{label} differs from the merge-base copy ({changes}); {FROZEN_RULE}"], []
