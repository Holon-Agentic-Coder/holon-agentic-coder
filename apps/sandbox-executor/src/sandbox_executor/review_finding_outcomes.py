from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace

from sandbox_executor.resolver_evidence import has_stated_reason
from sandbox_executor.review_ensemble import ReviewerResult, ReviewerStatus
from sandbox_executor.review_report import FindingDisposition, FindingRow, finding_signature

REQUIRED_VERDICTS = 3


def is_disposed(disposition: FindingDisposition) -> bool:
    if disposition.disposition == "applied":
        return bool((disposition.commit_sha or "").strip())
    if disposition.disposition == "rejected":
        return has_stated_reason(disposition.rejection_reason)
    return False


def with_commit(dispositions: Iterable[FindingDisposition], head_sha: str | None) -> tuple[FindingDisposition, ...]:
    return tuple(
        replace(d, commit_sha=d.commit_sha or head_sha or None) if d.disposition == "applied" else d
        for d in dispositions
    )


def undisposed_findings(findings: Sequence[FindingRow], dispositions: Iterable[FindingDisposition]) -> list[FindingRow]:
    disposed = {finding_signature(d.severity, d.path, d.line, d.summary) for d in dispositions if is_disposed(d)}
    return [row for row in findings if finding_signature(row.severity, row.path, row.line, row.summary) not in disposed]


def undisposed_finding_ids(findings: Sequence[FindingRow], dispositions: Iterable[FindingDisposition]) -> list[str]:
    return list(dict.fromkeys(row.id for row in undisposed_findings(findings, dispositions)))


def rejected_as(
    findings: Iterable[FindingRow], reason: str, introduced_by: Callable[[FindingRow], str | None]
) -> tuple[FindingDisposition, ...]:
    return tuple(
        FindingDisposition(
            id=row.id,
            severity=row.severity,
            path=row.path,
            line=row.line,
            summary=row.summary,
            recommendation=row.recommendation,
            disposition="rejected",
            rejection_reason=reason,
            introduced_by_action_id=introduced_by(row),
        )
        for row in findings
    )


def returned_verdict_count(reviewers: Sequence[ReviewerResult]) -> int:
    return sum(1 for result in reviewers if result.status is ReviewerStatus.COMPLETED)


def verdict_count_ok(count: int) -> bool:
    return count == REQUIRED_VERDICTS
