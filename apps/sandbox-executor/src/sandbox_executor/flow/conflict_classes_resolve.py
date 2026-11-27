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
    resolved: list[tuple[str, ConflictClass]] = field(default_factory=list)
    code_paths: list[str] = field(default_factory=list)


def _git_ok(worktree: str, *args: str) -> bool:
    return run_git(list(args), cwd=worktree, check=False).returncode == 0


def _write(worktree: str, path: str, text: str) -> None:
    with open(Path(worktree) / path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _ledger_union_holds(merged: str, sides: ConflictSides) -> bool:
    theirs, ours = sides.theirs or "", sides.ours or ""
    kept = {line.rstrip("\r\n") for line in merged.splitlines()}
    return (
        _keeps_lines_in_order(theirs.splitlines(), merged.splitlines())
        and not _has_conflict_marker_lines(merged)
        and all(line.rstrip("\r\n") in kept for line in ours.splitlines() if line.strip())
    )


def _keep_branch_fragment(worktree: str, sides: ConflictSides) -> bool:
    # why: rows the target already promoted are dropped by identity on read and next promotion, so none is lost.
    _write(worktree, sides.path, sides.ours or "")
    return _git_ok(worktree, "add", "--", sides.path)


def _resolve_ledger(worktree: str, sides: ConflictSides) -> bool:
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
    if sides.theirs is None or not _git_ok(worktree, "checkout", "--theirs", "--", sides.path):
        return False
    if (Path(worktree) / sides.path).read_text(encoding="utf-8") != sides.theirs:
        return False
    return _git_ok(worktree, "add", "--", sides.path)


def _resolve_delete(worktree: str, sides: ConflictSides) -> bool:
    if not _git_ok(worktree, "rm", "-q", "--", sides.path):
        return False
    tracked = run_git(["ls-files", "--", sides.path], cwd=worktree, check=False).stdout
    return not unmerged_stages(worktree, sides.path) and not tracked and not (Path(worktree) / sides.path).exists()


def _resolve_appended_list(worktree: str, sides: ConflictSides) -> bool:
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
    res = run_git(["ls-files", "-u", "-z", "--", path], cwd=worktree, check=False)
    return res.stdout if res.returncode == 0 and isinstance(res.stdout, str) else ""


def _restore_stages(worktree: str, path: str, entries: str) -> None:
    if not entries or unmerged_stages(worktree, path):
        return
    subprocess.run(
        ["git", "update-index", "-z", "--index-info"], cwd=worktree, input=entries, text=True, check=False, timeout=60
    )
    if not (Path(worktree) / path).exists() and 2 in unmerged_stages(worktree, path):
        run_git(["checkout-index", "-f", "--stage=2", "--", path], cwd=worktree, check=False)


def resolve_path(worktree: str, path: str, conflict_class: ConflictClass) -> bool:
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
        # why: a doubtful resolution must never silently drop a ledger row, knowledge change or list entry.
        _restore_stages(worktree, path, entries)
        run_git(["checkout", "-m", "--", path], cwd=worktree, check=False)
    return resolved


def resolve_mechanical(worktree: str, classified: list[tuple[str, ConflictClass]]) -> MechanicalResolution:
    outcome = MechanicalResolution()
    for path, conflict_class in classified:
        if conflict_class is not ConflictClass.CODE and resolve_path(worktree, path, conflict_class):
            outcome.resolved.append((path, conflict_class))
        else:
            outcome.code_paths.append(path)
    return outcome
