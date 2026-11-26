"""The resolver finding for a Stage 4 local test failure the test failure loop diagnosed.

The head test gate (:mod:`.local_tests`) stores the loop's evidence in ``ci_status["test_failure"]``; this turns it
into the ``FIND-CI`` finding the resolver fixes, naming the failing tests and quoting their redacted excerpts.
Without that evidence the generic CI finding (:func:`sandbox_executor.review_report.ci_failure_finding`) applies.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sandbox_executor.review_report import FindingRow

MAX_IDS_NAMED = 5
MAX_EXCERPTS_QUOTED = 3
MAX_EXCERPT_CHARS = 600


def local_test_failure_finding(
    iteration: int, ci_status: Mapping[str, Any], failed_names: Sequence[str] = ()
) -> FindingRow | None:
    """The ``FIND-CI`` finding naming the diagnosed failing tests and their excerpts, else None.

    Failed CI checks other than the local tests are named too, so the resolver still sees them.
    """
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
    return FindingRow(
        id=f"FIND-CI-{iteration:03d}",
        severity="important",
        path="",
        line=None,
        reviewers=(),
        summary=f"Local tests failed in iteration {iteration} ({evidence.get('classification') or 'unclassified'}): "
        f"{', '.join(ids[:MAX_IDS_NAMED])}{more}.{also}",
        recommendation="Fix the code so the failing tests pass; do not weaken, skip or delete them. Failure excerpts: "
        + (" | ".join(excerpts) or "none captured"),
    )
