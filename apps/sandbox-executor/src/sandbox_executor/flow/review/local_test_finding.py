from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sandbox_executor.review_report import FindingRow

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
    blocked_note = f" Blocked, not fixable in code: {str(blocked)[:MAX_EXCERPT_CHARS]}." if blocked else ""
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
