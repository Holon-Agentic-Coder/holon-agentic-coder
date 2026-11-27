from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sandbox_executor.review_report import FindingRow

UNATTRIBUTED_BLOCKER = "none"
PARKED_STATUSES = frozenset({"blocked", "exhausted"})

MAX_IDS_NAMED = 5
MAX_EXCERPTS_QUOTED = 3
MAX_EXCERPT_CHARS = 600
FIX_ADVICE = "Fix the code so the failing tests pass; do not weaken, skip or delete them."
BLOCKED_ADVICE = (
    "Report this failure as blocked with the evidence above; do not mask it in code by weakening, skipping or "
    "deleting the tests."
)


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
    blocked = evidence.get("blocked_evidence")
    blocked_on = str(evidence.get("blocked_on") or "").strip()
    blocker_suffix = f" (blocked_on: {blocked_on})" if blocked_on else ""
    blocked_note = (
        f" Blocked, not fixable in code: {str(blocked)[:MAX_EXCERPT_CHARS]}{blocker_suffix}." if blocked else ""
    )
    return FindingRow(
        id=f"FIND-CI-{iteration:03d}",
        severity="important",
        path="",
        line=None,
        reviewers=(),
        summary=f"Local tests failed in iteration {iteration} ({evidence.get('classification') or 'unclassified'}): "
        f"{', '.join(ids[:MAX_IDS_NAMED])}{more}.{also}{blocked_note}",
        recommendation=(BLOCKED_ADVICE if blocked else FIX_ADVICE)
        + " Failure excerpts: "
        + (" | ".join(excerpts) or "none captured"),
    )


def _is_parked_evidence(evidence: Mapping[str, Any], embedded: Any, status: str) -> bool:
    return (
        isinstance(embedded, Mapping)
        or status in PARKED_STATUSES
        or bool(evidence.get("blocked_on"))
        or bool(evidence.get("blocked_evidence"))
    )


def parked_record_from_ci_status(
    ci_status: Mapping[str, Any], *, checkpoint_path: str | None = None
) -> dict[str, Any] | None:
    evidence = ci_status.get("test_failure")
    if not isinstance(evidence, Mapping):
        return None
    embedded = evidence.get("parked_record")
    source: Mapping[str, Any] = embedded if isinstance(embedded, Mapping) else evidence
    status = str(source.get("status") or evidence.get("status") or "")
    if not _is_parked_evidence(evidence, embedded, status):
        return None
    raw_blocker = source.get("blocked_on") or evidence.get("blocked_on")
    blocked_on = str(raw_blocker).strip() if raw_blocker else UNATTRIBUTED_BLOCKER
    raw_rows = source.get("rows") if source.get("rows") is not None else evidence.get("rows")
    rows = [dict(row) if isinstance(row, Mapping) else row for row in (raw_rows or ())]
    resolved_checkpoint = checkpoint_path or source.get("checkpoint_path") or evidence.get("checkpoint_path")
    return {
        "blocked_on": blocked_on or UNATTRIBUTED_BLOCKER,
        "status": status or "blocked",
        "classification": source.get("classification") or evidence.get("classification"),
        "failing_ids": [str(test_id) for test_id in (source.get("failing_ids") or evidence.get("failing_ids") or ())],
        "signature": str(source.get("signature") or evidence.get("signature") or ""),
        "evidence": str(source.get("evidence") or evidence.get("blocked_evidence") or ""),
        "rows": rows,
        "checkpoint_path": str(resolved_checkpoint) if resolved_checkpoint else None,
        "blocked_reason": source.get("blocked_reason") or evidence.get("blocked_reason"),
        "final_tier": source.get("final_tier") or evidence.get("final_tier"),
    }
