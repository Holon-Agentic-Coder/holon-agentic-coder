"""Ledger-only merge conflicts: keep the merge-base rows as an untouched prefix and interleave both sides' appended
rows on ``created_at``.

Used when a branch is merged into another (a plan branch into a child head, a plan branch into the parent execution
branch) and the only conflicts are in ``holon-knowledge/ledger/*.jsonl``. No existing line is removed or rewritten:
the merge-base rows stay a byte-identical prefix, each side keeps the relative order of its own appended rows, and a
row both sides appended is kept once.
"""

from __future__ import annotations

import json
import os

from sandbox_executor.flow.ledger_reconcile import ConflictResolutionError
from sandbox_executor.git import run_git

LEDGER_DIR_PREFIX = "holon-knowledge/ledger/"


def _created_at_key(line: str) -> str | None:
    """Return a sortable ``created_at`` for a ledger row (``Z`` normalised to ``+00:00``), or None when it has none."""
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    value = row.get("created_at") if isinstance(row, dict) else None
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().replace("Z", "+00:00")


def _appended_tail(base_lines: list[str], side_lines: list[str], side: str) -> list[str]:
    """Return the rows ``side`` appended after the merge base, refusing a side that changed a base row."""
    if side_lines[: len(base_lines)] != base_lines:
        raise ConflictResolutionError(
            f"ledger conflict is not append-only: {side} removed, rewrote or reordered a row of the merge base"
        )
    return side_lines[len(base_lines) :]


def _precedes(left: str, right: str) -> bool:
    """True when ``left`` (from ours) goes before ``right`` (from theirs): earlier ``created_at``, ours on a tie.

    A row without a parsable ``created_at`` sorts after every timed row, so it waits until the other side runs out of
    timed rows; that keeps each side's own order intact.
    """
    left_key, right_key = _created_at_key(left), _created_at_key(right)
    if left_key is None:
        return right_key is None
    return right_key is None or left_key <= right_key


def interleave_ledger_rows(base_lines: list[str], ours_lines: list[str], theirs_lines: list[str]) -> list[str]:
    """Merge two append-only versions of one ledger into a single append-only result.

    Args:
        base_lines: The ledger's rows at the merge base (empty when the file did not exist there).
        ours_lines: The rows on the checked-out side.
        theirs_lines: The rows on the side being merged in.

    Returns:
        list[str]: The merge-base rows unchanged, then both sides' appended rows interleaved on ``created_at`` (each
        side's own order kept, ours first on a tie), with a row already kept never repeated.

    Raises:
        ConflictResolutionError: When either side does not start with the merge-base rows, so the conflict is not a
            pure append and must not be resolved automatically.
    """
    ours = _appended_tail(base_lines, ours_lines, "ours")
    theirs = _appended_tail(base_lines, theirs_lines, "theirs")
    merged = list(base_lines)
    seen = set(base_lines)
    while ours or theirs:
        take_ours = bool(ours) and (not theirs or _precedes(ours[0], theirs[0]))
        line = ours.pop(0) if take_ours else theirs.pop(0)
        if line not in seen:
            seen.add(line)
            merged.append(line)
    return merged


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
