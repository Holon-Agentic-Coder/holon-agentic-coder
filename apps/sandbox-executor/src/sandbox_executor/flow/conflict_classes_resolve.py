"""Resolve the mechanical conflict classes of an in-progress merge, each resolver verifying its own result.

A resolver returns True only after its output passed its check and was staged. On any doubt (a missing side, a check
that fails, a git error) the path's conflicted state is restored with ``git checkout -m`` and the path is handed back
as ``CODE``, so a mechanical resolver can never silently drop a ledger row, a knowledge-base change or a list entry.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sandbox_executor.flow.conflict_classes import ConflictClass, ConflictSides, read_sides, unmerged_stages
from sandbox_executor.flow.conflict_classes_lists import appended_list_union
from sandbox_executor.flow.ledger_reconcile import _append_only_union, _has_conflict_marker_lines
from sandbox_executor.git import run_git

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MechanicalResolution:
    """The paths resolved mechanically (with their class) and the paths left for the code resolver, both in order."""

    resolved: list[tuple[str, ConflictClass]] = field(default_factory=list)
    code_paths: list[str] = field(default_factory=list)


def _git_ok(worktree: str, *args: str) -> bool:
    return run_git(list(args), cwd=worktree, check=False).returncode == 0


def _write(worktree: str, path: str, text: str) -> None:
    with open(Path(worktree) / path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _ledger_union_holds(merged: str, sides: ConflictSides) -> bool:
    """The target ledger is a byte prefix, no marker remains and every flow-branch row is kept byte-identically."""
    theirs, ours = sides.theirs or "", sides.ours or ""
    kept = {line.rstrip("\r\n") for line in merged.splitlines()}
    return (
        merged.startswith(theirs)
        and not _has_conflict_marker_lines(merged)
        and all(line.rstrip("\r\n") in kept for line in ours.splitlines() if line.strip())
    )


def _resolve_ledger(worktree: str, sides: ConflictSides) -> bool:
    """Append the flow branch's rows the target lacks after the untouched target ledger."""
    if sides.ours is None or sides.theirs is None:
        return False
    merged = _append_only_union(sides.theirs.splitlines(keepends=True), sides.ours.splitlines(keepends=True))
    if not _ledger_union_holds(merged, sides):
        return False
    _write(worktree, sides.path, merged)
    return _git_ok(worktree, "add", "--", sides.path)


def _resolve_timestamp_only(worktree: str, sides: ConflictSides) -> bool:
    """Keep the target copy of a knowledge-base entry that differs only in its timestamps."""
    if sides.theirs is None or not _git_ok(worktree, "checkout", "--theirs", "--", sides.path):
        return False
    if (Path(worktree) / sides.path).read_text(encoding="utf-8") != sides.theirs:
        return False
    return _git_ok(worktree, "add", "--", sides.path)


def _resolve_delete(worktree: str, sides: ConflictSides) -> bool:
    """Keep the target's deletion of a file the flow branch changed only mechanically."""
    if not _git_ok(worktree, "rm", "-q", "--", sides.path):
        return False
    tracked = run_git(["ls-files", "--", sides.path], cwd=worktree, check=False).stdout
    return not unmerged_stages(worktree, sides.path) and not tracked and not (Path(worktree) / sides.path).exists()


def _resolve_appended_list(worktree: str, sides: ConflictSides) -> bool:
    """Write the target file with the flow branch's appended entries added (``appended_list_union`` verifies it)."""
    if sides.base is None or sides.ours is None or sides.theirs is None:
        return False
    union = appended_list_union(sides.base, sides.ours, sides.theirs)
    if union is None:
        return False
    _write(worktree, sides.path, union)
    return _git_ok(worktree, "add", "--", sides.path)


_RESOLVERS: dict[ConflictClass, Callable[[str, ConflictSides], bool]] = {
    ConflictClass.LEDGER: _resolve_ledger,
    ConflictClass.KB_TIMESTAMP_ONLY: _resolve_timestamp_only,
    ConflictClass.DELETE_VS_TOUCH: _resolve_delete,
    ConflictClass.APPENDED_LIST: _resolve_appended_list,
}


def resolve_path(worktree: str, path: str, conflict_class: ConflictClass) -> bool:
    """Resolve one unmerged path of class ``conflict_class``; restore its conflicted state and return False on doubt."""
    resolver = _RESOLVERS.get(conflict_class)
    if resolver is None:
        return False
    try:
        resolved = resolver(worktree, read_sides(worktree, path))
    except (OSError, UnicodeError, RuntimeError) as exc:
        logger.warning("Mechanical %s resolution of %s failed: %s", conflict_class.value, path, exc)
        resolved = False
    if not resolved:
        run_git(["checkout", "-m", "--", path], cwd=worktree, check=False)
    return resolved


def resolve_mechanical(worktree: str, classified: list[tuple[str, ConflictClass]]) -> MechanicalResolution:
    """Resolve every mechanical path in order; ``CODE`` paths and failed resolutions are left for the code resolver."""
    outcome = MechanicalResolution()
    for path, conflict_class in classified:
        if conflict_class is not ConflictClass.CODE and resolve_path(worktree, path, conflict_class):
            outcome.resolved.append((path, conflict_class))
        else:
            outcome.code_paths.append(path)
    return outcome
