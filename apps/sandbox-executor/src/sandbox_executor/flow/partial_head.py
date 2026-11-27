from __future__ import annotations

import re

from sandbox_executor.flow.git_refs import _latest_execution_summary, _read_ledger_rows
from sandbox_executor.git import EMPTY_DIFF_STATUS

# why: the executor entrypoint historically writes "failure" while guard rows write "failed"; both mean failure.
FAILED_EXECUTION_STATUSES: frozenset[str] = frozenset({"failed", "failure"})

_EXECUTION_SEQ = re.compile(r"/E-(\d+)-[^/]+/_$")


def _execution_seq(branch: str) -> int:
    match = _EXECUTION_SEQ.search(branch)
    return int(match.group(1)) if match else -1


def _execution_id(branch: str) -> str:
    return branch.removesuffix("/_").rsplit("/", 1)[-1]


def failed_partial_head(repo_dir: str, ref: str, execution_id: str) -> str | None:
    row = _latest_execution_summary(_read_ledger_rows(repo_dir, ref, "executions.jsonl"), execution_id, ref) or {}
    partial_head = row.get("partial_head")
    # why: an empty-diff row can carry salvaged partial work just like a failure row.
    if row.get("status") in ("failure", EMPTY_DIFF_STATUS) and isinstance(partial_head, str) and partial_head:
        return partial_head
    return None


def _has_summary_row(repo_dir: str, ref: str, execution_id: str) -> bool:
    rows = _read_ledger_rows(repo_dir, ref, "executions.jsonl")
    return _latest_execution_summary(rows, execution_id, ref) is not None


def _newest_partial_head(repo_dir: str, branches: set[str]) -> tuple[str, str] | None:
    for branch in sorted(branches, key=_execution_seq, reverse=True):
        head = failed_partial_head(repo_dir, f"refs/remotes/origin/{branch}", _execution_id(branch))
        if head:
            return branch, head
    return None
