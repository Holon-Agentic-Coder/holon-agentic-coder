from __future__ import annotations

from dataclasses import replace
from typing import Any

from sandbox_executor.flow.target_sync_result import SyncBoundary, SyncRequest, SyncResult
from sandbox_executor.ledger import append_action_record, generate_action_id, query_ledger

SYNC_ACTION = "target_sync"
SYNC_CONFLICT_STATUS = "sync_conflict"
_OUTCOMES = {"merged": "success", "blocked": "blocked"}


def superseded_sync_conflict(ledger_path: str, execution_id: str) -> str:
    rows = [
        row
        for row in query_ledger(ledger_path, entry_type="summary", outcome=SYNC_CONFLICT_STATUS)
        if row.get("execution_id") == execution_id and row.get("status") == SYNC_CONFLICT_STATUS
    ]
    if not rows:
        return ""
    return f"{execution_id}@r{rows[-1].get('ledger_revision', 1)}"


def sync_row(request: SyncRequest, result: SyncResult, supersedes: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "action_id": generate_action_id("SYNC"),
        "execution_id": request.execution_id,
        "action": SYNC_ACTION,
        "category": "code",
        "target": request.target_branch,
        "outcome": _OUTCOMES[result.status],
        "event_type": SYNC_ACTION,
        "labels": [SYNC_ACTION, request.boundary.value],
        "stage": request.stage,
        "boundary": request.boundary.value,
        "branch": request.branch,
        "target_sha": result.target_sha,
        "head_before": result.head_before,
        "conflicted_paths": list(result.conflicted_paths),
        "resolutions": [{"path": path, "class": cls, "how": how} for path, cls, how in result.resolutions],
        "code_paths": list(result.code_paths),
        "resolver_tier": result.resolver_tier,
        "attempts": result.attempts,
        "test_result": result.test_result,
        "supersedes": supersedes,
        "error_context": result.error or None,
    }
    if request.reentry_round > 0 or request.boundary is SyncBoundary.FLOW_END:
        row["reentry_round"] = request.reentry_round
    return row


def append_sync_row(request: SyncRequest, result: SyncResult) -> SyncResult:
    merged = result.status == "merged"
    supersedes = superseded_sync_conflict(request.ledger_path, request.execution_id) if merged else ""
    row = sync_row(request, result, supersedes)
    if row["error_context"] is None:
        del row["error_context"]
    # why: append-only ledger: append_action_record is the only writer; the superseded sync_conflict row is only read
    written = append_action_record(request.ledger_path, row, preserve_shape=True)
    return replace(result, ledger_row_id=written["action_id"])
