from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sandbox_executor.review_report import FindingRow

MAX_IDS_NAMED = 5
MAX_EXCERPTS_QUOTED = 3
MAX_EXCERPT_CHARS = 600
UNATTRIBUTED_BLOCKER = "none"
PARKED_STATUSES = frozenset({"blocked", "exhausted"})
FIX_ADVICE = "Fix the code so the failing tests pass; do not weaken, skip or delete them."
BLOCKED_ADVICE = (
    "Report this failure as blocked with the evidence above; do not mask it in code by weakening, skipping or "
    "deleting the tests."
)


def parked_record_from_ci_status(ci_status: Mapping[str, Any], *, checkpoint_path: str = "") -> dict[str, Any] | None:
    evidence = ci_status.get("test_failure")
    if not isinstance(evidence, Mapping):
        return None
    raw = evidence.get("parked_record")
    if not isinstance(raw, Mapping):
        if evidence.get("status") not in PARKED_STATUSES:
            return None
        raw = evidence
    raw_rows = raw.get("ledger_rows")
    ledger_rows = list(raw_rows) if isinstance(raw_rows, list) else list(evidence.get("ledger_rows") or ())
    return {
        "status": str(raw.get("status") if raw is not evidence else "parked"),
        "loop_status": str(raw.get("loop_status") or evidence.get("status") or ""),
        "blocked_on": str(raw.get("blocked_on") or evidence.get("blocked_on") or UNATTRIBUTED_BLOCKER),
        "classification": str(raw.get("classification") or evidence.get("classification") or "unresolved"),
        "failing_ids": [str(test_id) for test_id in (raw.get("failing_ids") or evidence.get("failing_ids") or ())],
        "signature": str(raw.get("signature") or evidence.get("signature") or ""),
        "evidence": str(raw.get("evidence") or evidence.get("blocked_evidence") or ""),
        "ledger_rows": ledger_rows,
        "checkpoint_path": str(raw.get("checkpoint_path") or checkpoint_path),
    }


def local_test_failure_finding(
    iteration: int, ci_status: Mapping[str, Any], failed_names: Sequence[str] = ()
) -> FindingRow | None:
    evidence = ci_status.get("test_failure")
    if not isinstance(evidence, Mapping) or not evidence.get("failing_ids"):
        return None
    ids = [str(test_id) for test_id in evidence["failing_ids"]]
    more = f" (+{len(ids) - MAX_IDS_NAMED} more)" if len(ids) > MAX_IDS_NAMED else ""
    excerpts = [
        f"`{test_id}`: {str(text)[:MAX_EXCERPT_CHARS]}"
        for test_id, text in list((evidence.get("excerpts") or {}).items())[:MAX_EXCERPTS_QUOTED]
    ]
    checks = [name for name in failed_names if name != "local_tests"]
    also = f" Failed CI checks: {', '.join(checks)}." if checks else ""
    parked = parked_record_from_ci_status(ci_status)
    blocked_on = str((parked or {}).get("blocked_on") or evidence.get("blocked_on") or "")
    blocker_note = f" Blocked on: {blocked_on}." if blocked_on and blocked_on != UNATTRIBUTED_BLOCKER else ""
    blocked = evidence.get("blocked_evidence")
    blocked_note = f" Blocked, not fixable in code: {str(blocked)[:MAX_EXCERPT_CHARS]}." if blocked else ""
    return FindingRow(
        id=f"FIND-CI-{iteration:03d}",
        severity="important",
        path="",
        line=None,
        reviewers=(),
        summary=f"Local tests failed in iteration {iteration} ({evidence.get('classification') or 'unclassified'}): "
        f"{', '.join(ids[:MAX_IDS_NAMED])}{more}.{also}{blocked_note}{blocker_note}",
        recommendation=(BLOCKED_ADVICE if (blocked or blocker_note) else FIX_ADVICE)
        + " Failure excerpts: "
        + (" | ".join(excerpts) or "none captured"),
    )
