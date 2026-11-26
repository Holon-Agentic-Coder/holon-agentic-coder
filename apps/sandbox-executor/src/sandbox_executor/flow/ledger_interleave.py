"""Ledger-only merge conflicts: keep every row of the checked-out side in place and insert the incoming side's
appended rows at their ``created_at`` slot.

Used when a branch is merged into another (a plan branch into a child head, a plan branch into the parent execution
branch) and the only conflicts are in ``holon-knowledge/ledger/*.jsonl``. No existing line is removed, rewritten or
moved: the merge-base rows and ours' appended rows keep their relative order, theirs' appended rows are inserted by
parsed UTC instant (ours first on a tie, also between merge-base rows when they are older), and a row both sides
appended is kept once at ours' position. Merging into a sorted side is additions only and stays sorted.
"""

from __future__ import annotations

import os

from sandbox_executor.flow.ledger_reconcile import ConflictResolutionError
from sandbox_executor.git import run_git
from sandbox_executor.ledger.reconcile import insert_incoming_lines

LEDGER_DIR_PREFIX = "holon-knowledge/ledger/"


def _appended_tail(base_lines: list[str], side_lines: list[str], side: str) -> list[str]:
    """Return the rows ``side`` appended after the merge base, refusing a side that changed a base row."""
    if side_lines[: len(base_lines)] != base_lines:
        raise ConflictResolutionError(
            f"ledger conflict is not append-only: {side} removed, rewrote or reordered a row of the merge base"
        )
    return side_lines[len(base_lines) :]


def interleave_ledger_rows(base_lines: list[str], ours_lines: list[str], theirs_lines: list[str]) -> list[str]:
    """Merge two append-only versions of one ledger into a single append-only result.

    Args:
        base_lines: The ledger's rows at the merge base (empty when the file did not exist there).
        ours_lines: The rows on the checked-out side.
        theirs_lines: The rows on the side being merged in.

    Returns:
        list[str]: Ours unchanged (merge-base rows, then ours' appended rows), with each of theirs' appended rows
        inserted after every ours row whose instant is not later than its own; a row ours already holds is not
        inserted again.

    Raises:
        ConflictResolutionError: When either side does not start with the merge-base rows, so the conflict is not a
            pure append and must not be resolved automatically.
    """
    _appended_tail(base_lines, ours_lines, "ours")
    return insert_incoming_lines(ours_lines, _appended_tail(base_lines, theirs_lines, "theirs"))


def _staged_lines(repo_dir: str, stage: int, path: str) -> list[str]:
    """Return the non-blank lines of ``path`` at an index stage (1 base, 2 ours, 3 theirs); empty when absent."""
    res = run_git(["show", f":{stage}:{path}"], cwd=repo_dir, check=False)
    if res.returncode != 0 or not isinstance(res.stdout, str):
        return []
    return [line for line in res.stdout.splitlines() if line.strip()]


def resolve_ledger_only_conflicts(repo_dir: str) -> bool:
    """Resolve an in-progress merge whose only conflicts are ledger files, staging each resolution.

    Returns:
        bool: True when every unmerged path was a ``holon-knowledge/ledger/*.jsonl`` file and is now resolved and
        staged; False when there is no conflict to resolve, a non-ledger path conflicts, or a ledger conflict is not
        a pure append. The caller aborts the merge on False.
    """
    res = run_git(["diff", "--name-only", "--diff-filter=U"], cwd=repo_dir, check=False)
    paths = [p.strip() for p in (res.stdout or "").splitlines() if p.strip()] if res.returncode == 0 else []
    if not paths or any(not (p.startswith(LEDGER_DIR_PREFIX) and p.endswith(".jsonl")) for p in paths):
        return False
    for path in paths:
        try:
            lines = interleave_ledger_rows(*(_staged_lines(repo_dir, stage, path) for stage in (1, 2, 3)))
        except ConflictResolutionError:
            return False
        with open(os.path.join(repo_dir, path), "w", encoding="utf-8") as f:
            f.write("".join(f"{line}\n" for line in lines))
        if run_git(["add", path], cwd=repo_dir, check=False).returncode != 0:
            return False
    return True
