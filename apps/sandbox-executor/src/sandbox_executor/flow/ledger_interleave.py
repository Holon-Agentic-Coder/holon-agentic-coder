from __future__ import annotations

import os

from sandbox_executor.flow.ledger_reconcile import ConflictResolutionError
from sandbox_executor.git import run_git
from sandbox_executor.ledger.reconcile import insert_incoming_lines

LEDGER_DIR_PREFIX = "holon-knowledge/ledger/"


def _appended_tail(base_lines: list[str], side_lines: list[str], side: str) -> list[str]:
    if side_lines[: len(base_lines)] != base_lines:
        raise ConflictResolutionError(
            f"ledger conflict is not append-only: {side} removed, rewrote or reordered a row of the merge base"
        )
    return side_lines[len(base_lines) :]


def interleave_ledger_rows(base_lines: list[str], ours_lines: list[str], theirs_lines: list[str]) -> list[str]:
    # why: ours stays in place, but a changed merge-base row on ours must still refuse resolution
    _appended_tail(base_lines, ours_lines, "ours")
    return insert_incoming_lines(ours_lines, _appended_tail(base_lines, theirs_lines, "theirs"))


def _staged_lines(repo_dir: str, stage: int, path: str) -> list[str]:
    res = run_git(["show", f":{stage}:{path}"], cwd=repo_dir, check=False)
    if res.returncode != 0 or not isinstance(res.stdout, str):
        return []
    return [line for line in res.stdout.splitlines() if line.strip()]


def resolve_ledger_only_conflicts(repo_dir: str) -> bool:
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
