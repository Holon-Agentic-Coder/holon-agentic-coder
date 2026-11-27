from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sandbox_executor.review_report import FindingRow

MAX_IDS_NAMED = 5
MAX_EXCERPTS_QUOTED = 3
MAX_EXCERPT_CHARS = 600
BLOCKED_ON_NONE = "none"
PARKED_STATUSES = frozenset({"blocked", "exhausted"})
FIX_ADVICE = "Fix the code so the failing tests pass; do not weaken, skip or delete them."
BLOCKED_ADVICE = (
    "Report this failure as blocked with the evidence above; do not mask it in code by weakening, skipping or "
    "deleting the tests."
)


def _normalized_parked_record(
    raw: Mapping[str, Any], evidence: Mapping[str, Any], checkpoint_path: str | None
) -> dict[str, Any]:
    record = dict(raw)
    record["status"] = str(record.get("status") or "parked")
    record["blocked_on"] = str(record.get("blocked_on") or evidence.get("blocked_on") or BLOCKED_ON_NONE)
    record.setdefault("classification", evidence.get("classification"))
    record["failing_ids"] = [str(item) for item in record.get("failing_ids", evidence.get("failing_ids") or ())]
    record["signature"] = str(record.get("signature") or evidence.get("signature") or "")
    record["evidence"] = str(record.get("evidence") or evidence.get("blocked_evidence") or "")
    rows = record.get("rows")
    if rows is None:
        rows = evidence.get("attempt_rows") or evidence.get("rows") or ()
    record["rows"] = list(rows)
    record["checkpoint_path"] = record.get("checkpoint_path") or checkpoint_path
    return record


def parked_record_from_ci_status(
    ci_status: Mapping[str, Any], *, checkpoint_path: str | None = None
) -> dict[str, Any] | None:
    evidence = ci_status.get("test_failure")
    if not isinstance(evidence, Mapping):
        return None
    existing = evidence.get("parked_record")
    if isinstance(existing, Mapping):
        return _normalized_parked_record(existing, evidence, checkpoint_path)
    status = str(evidence.get("status") or "")
    if status not in PARKED_STATUSES and not evidence.get("blocked_evidence") and not evidence.get("blocked_on"):
        return None
    return _normalized_parked_record({"outcome_status": status or "blocked"}, evidence, checkpoint_path)


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
    parked = parked_record_from_ci_status(ci_status)
    blocked_on = str(evidence.get("blocked_on") or (parked.get("blocked_on") if parked else "") or "")
    blocked_note = f" Blocked, not fixable in code: {str(blocked)[:MAX_EXCERPT_CHARS]}." if blocked else ""
    blocked_on_note = f" Blocked on: {blocked_on}." if blocked_on else ""
    return FindingRow(
        id=f"FIND-CI-{iteration:03d}",
        severity="important",
        path="",
        line=None,
        reviewers=(),
        summary=f"Local tests failed in iteration {iteration} ({evidence.get('classification') or 'unclassified'}): "
        f"{', '.join(ids[:MAX_IDS_NAMED])}{more}.{also}{blocked_note}{blocked_on_note}",
        recommendation=(BLOCKED_ADVICE if blocked else FIX_ADVICE)
        + " Failure excerpts: "
        + (" | ".join(excerpts) or "none captured"),
    )
