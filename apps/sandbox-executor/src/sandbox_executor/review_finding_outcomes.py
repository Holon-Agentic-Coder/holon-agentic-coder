from __future__ import annotations

from collections.abc import Iterable, Sequence

from sandbox_executor.resolver_evidence import has_stated_reason
from sandbox_executor.review_ensemble import ReviewerResult, ReviewerStatus
from sandbox_executor.review_report import FindingDisposition, FindingRow, finding_signature

REQUIRED_VERDICTS = 3


def is_disposed(disposition: FindingDisposition) -> bool:
    if disposition.disposition == "applied":
        # why: an applied fix without a commit is in no reviewed head, so the finding is still open there.
        return bool((disposition.commit_sha or "").strip())
    if disposition.disposition == "rejected":
        return has_stated_reason(disposition.rejection_reason)
    return False


def undisposed_finding_ids(findings: Sequence[FindingRow], dispositions: Iterable[FindingDisposition]) -> list[str]:
    # why: finding ids such as R1-F1 repeat across iterations for different findings, so the signature is the key.
    disposed = {finding_signature(d.severity, d.path, d.line, d.summary) for d in dispositions if is_disposed(d)}
    undisposed: list[str] = []
    for row in findings:
        if finding_signature(row.severity, row.path, row.line, row.summary) in disposed or row.id in undisposed:
            continue
        undisposed.append(row.id)
    return undisposed


def returned_verdict_count(reviewers: Sequence[ReviewerResult]) -> int:
    return sum(1 for result in reviewers if result.status is ReviewerStatus.COMPLETED)


def verdict_count_ok(count: int) -> bool:
    return count == REQUIRED_VERDICTS
