"""Stage 4 adjudication suppression: nit findings the resolver already rejected are not handed to it again.

Suppression only narrows the resolver's input and the adjudicated list shown to reviewers. It never rewrites a
reviewer's verdict, counts or role table, so the consensus is always decided from what the reviewers returned.
"""

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
    """The deciding iteration's merged finding rows, split into those the resolver acts on and suppressed nits."""

    actionable: tuple[FindingRow, ...] = ()
    suppressed: tuple[FindingRow, ...] = ()


def security_objectors(reviewers: Sequence[ReviewerResult]) -> frozenset[int]:
    """Indices of the reviewers whose activated Security role returned ``CHANGES``."""
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
    """True only for a nit whose exact signature was rejected at nit and no raising reviewer's Security role objects.

    The signature includes the severity, so a finding re-raised at a higher severity is a new finding; the explicit
    severity guard keeps a history entry recorded at another severity from ever matching.
    """
    if row.severity != SUPPRESSIBLE_SEVERITY or objectors.intersection(row.reviewers):
        return False
    earlier = rejected_history.get(finding_signature(row.severity, row.path, row.line, row.summary))
    return earlier is not None and earlier.severity == row.severity


def _filter_adjudicated_findings(
    reviewers: Sequence[ReviewerResult],
    rejected_history: Mapping[FindingSignature, FindingDisposition],
) -> Adjudication:
    """Split the merged finding rows into actionable rows and suppressed, already-rejected nits.

    Cause of the false consensus this replaces: the filter used to set the verdict to APPROVED once all findings were
    dropped, and matched rejected findings by positional id, so a critical re-raised as ``R3-F1`` was dropped and the
    reviewer reported as approved. It now returns finding rows only and matches on the full signature.
    """
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
    """Remember each rejected nit; a rejected critical or important finding is never treated as adjudicated."""
    for disp in dispositions:
        if disp.disposition == "rejected" and disp.severity == SUPPRESSIBLE_SEVERITY:
            rejected_history[finding_signature(disp.severity, disp.path, disp.line, disp.summary)] = disp


def _augment_pr_text_with_rejected(
    pr_text: tuple[str, str],
    rejected: Sequence[FindingDisposition],
) -> tuple[str, str]:
    """Append already-adjudicated rejected nits to the PR description so reviewers do not re-raise them.

    Only nits are listed: reviewers are never told to stay silent about an important or critical issue.
    """
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
