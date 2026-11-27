from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass

from sandbox_executor.review_ensemble import ReviewerResult
from sandbox_executor.review_report import (
    FindingDisposition,
    FindingRow,
    collect_review_data,
    finding_signature,
    parse_reviewer_detail,
)

SUPPRESSIBLE_SEVERITY = "nit"

FindingSignature = tuple[str, str, int | None, str]


@dataclass(frozen=True)
class Adjudication:
    actionable: tuple[FindingRow, ...] = ()
    suppressed: tuple[FindingRow, ...] = ()


def security_objectors(reviewers: Sequence[ReviewerResult]) -> frozenset[int]:
    return frozenset(
        result.index
        for result in reviewers
        for role in parse_reviewer_detail(result).roles
        if "security" in role.role.lower() and role.verdict == "CHANGES"
    )


def is_suppressible(
    row: FindingRow,
    rejected_history: Mapping[FindingSignature, FindingDisposition],
    objectors: frozenset[int],
) -> bool:
    if row.severity != SUPPRESSIBLE_SEVERITY or objectors.intersection(row.reviewers):
        return False
    # why: a history entry recorded at another severity must never match, even if signatures were to collide.
    earlier = rejected_history.get(finding_signature(row.severity, row.path, row.line, row.summary))
    return earlier is not None and earlier.severity == row.severity


def _filter_adjudicated_findings(
    reviewers: Sequence[ReviewerResult],
    rejected_history: Mapping[FindingSignature, FindingDisposition],
) -> Adjudication:
    # why: only rows are split, never verdicts; a filter that set APPROVED once findings were dropped caused a false
    # why: consensus, and positional ids let a critical re-raised as R3-F1 be dropped, so rows match on full signature.
    rows = collect_review_data(reviewers).findings
    if not rejected_history:
        return Adjudication(actionable=rows)
    objectors = security_objectors(reviewers)
    suppressed = tuple(row for row in rows if is_suppressible(row, rejected_history, objectors))
    return Adjudication(actionable=tuple(row for row in rows if row not in suppressed), suppressed=suppressed)


def record_nit_rejections(
    rejected_history: MutableMapping[FindingSignature, FindingDisposition],
    dispositions: Iterable[FindingDisposition],
) -> None:
    for disp in dispositions:
        if disp.disposition == "rejected" and disp.severity == SUPPRESSIBLE_SEVERITY:
            rejected_history[finding_signature(disp.severity, disp.path, disp.line, disp.summary)] = disp


def _augment_pr_text_with_rejected(
    pr_text: tuple[str, str],
    rejected: Sequence[FindingDisposition],
) -> tuple[str, str]:
    # why: reviewers are never told to stay silent about an important or critical issue.
    nits = [item for item in rejected if item.severity == SUPPRESSIBLE_SEVERITY]
    if not nits:
        return pr_text
    lines = [
        pr_text[1] or "",
        "",
        "### Adjudicated Rejected Nit Findings (Already Adjudicated — Do Not Re-Raise)",
        "",
    ]
    for item in nits:
        loc = item.path if item.line is None else f"{item.path}:{item.line}"
        lines.append(f"- `{item.id}` ({item.severity}, `{loc}`): {item.summary} — rejected: `{item.rejection_reason}`")
    return pr_text[0], "\n".join(lines).strip()


def _lookup_introducing_action_id(row: FindingRow, action_ids: Mapping[str, str]) -> str | None:
    sig_key = str(finding_signature(row.severity, row.path, row.line, row.summary))
    return action_ids.get(row.id) or action_ids.get(sig_key)
