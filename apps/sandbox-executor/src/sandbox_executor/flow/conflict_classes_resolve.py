"""Resolve the mechanical conflict classes of an in-progress merge, each resolver verifying its own result.

A resolver returns True only after its output passed its check and was staged. On any doubt (a missing side, a check
that fails, a git error) the path's unmerged index stages are put back if a resolver removed them, its conflicted
copy is restored with ``git checkout -m`` and the path is handed back as ``CODE``, so a mechanical resolver can never
silently drop a ledger row, a knowledge-base change or a list entry.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sandbox_executor.flow.conflict_classes import ConflictClass, ConflictSides, read_sides, unmerged_stages
from sandbox_executor.flow.conflict_classes_lists import appended_list_union
from sandbox_executor.flow.ledger_reconcile import _has_conflict_marker_lines
from sandbox_executor.git import run_git
from sandbox_executor.ledger.fragments import PENDING_DIR_REL
from sandbox_executor.ledger.reconcile import _insertion_union, _keeps_lines_in_order

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
    """Every target row stays in order, no marker remains and every flow-branch row is kept byte-identically."""
    theirs, ours = sides.theirs or "", sides.ours or ""
    kept = {line.rstrip("\r\n") for line in merged.splitlines()}
    return (
        _keeps_lines_in_order(theirs.splitlines(), merged.splitlines())
        and not _has_conflict_marker_lines(merged)
        and all(line.rstrip("\r\n") in kept for line in ours.splitlines() if line.strip())
    )


def _keep_branch_fragment(worktree: str, sides: ConflictSides) -> bool:
    """Keep the flow branch's copy of a pending fragment the target deleted when it promoted the fragment.

    Rows the target already promoted stay in the fragment; the merged reader and the next promotion drop them by
    identity, so no row is lost or duplicated.
    """
    _write(worktree, sides.path, sides.ours or "")
    return _git_ok(worktree, "add", "--", sides.path)


def _resolve_ledger(worktree: str, sides: ConflictSides) -> bool:
    """Insert the flow branch's rows the target lacks by ``created_at``, never moving a target row."""
    if sides.ours is not None and sides.theirs is None and sides.path.startswith(f"{PENDING_DIR_REL}/"):
        return _keep_branch_fragment(worktree, sides)
    if sides.ours is None or sides.theirs is None:
        return False
    merged = _insertion_union(sides.theirs.splitlines(keepends=True), sides.ours.splitlines(keepends=True))
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


def _unmerged_entries(worktree: str, path: str) -> str:
    """The path's unmerged index entries (``mode sha stage<TAB>path``, NUL-terminated), for ``_restore_stages``."""
    res = run_git(["ls-files", "-u", "-z", "--", path], cwd=worktree, check=False)
    return res.stdout if res.returncode == 0 and isinstance(res.stdout, str) else ""


def _restore_stages(worktree: str, path: str, entries: str) -> None:
    """Put the unmerged stages (and the flow copy git left in the worktree) back after a failed ``git rm``."""
    if not entries or unmerged_stages(worktree, path):
        return
    subprocess.run(
        ["git", "update-index", "-z", "--index-info"], cwd=worktree, input=entries, text=True, check=False, timeout=60
    )
    if not (Path(worktree) / path).exists() and 2 in unmerged_stages(worktree, path):
        run_git(["checkout-index", "-f", "--stage=2", "--", path], cwd=worktree, check=False)


def resolve_path(worktree: str, path: str, conflict_class: ConflictClass) -> bool:
    """Resolve one unmerged path of class ``conflict_class``; restore its conflicted state and return False on doubt."""
    resolver = _RESOLVERS.get(conflict_class)
    if resolver is None:
        return False
    entries = _unmerged_entries(worktree, path)
    try:
        resolved = resolver(worktree, read_sides(worktree, path))
    except (OSError, UnicodeError, RuntimeError) as exc:
        logger.warning("Mechanical %s resolution of %s failed: %s", conflict_class.value, path, exc)
        resolved = False
    if not resolved:
        _restore_stages(worktree, path, entries)
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
