"""The Stage 4 consensus verdict, decided from what the reviewers returned and cross-checked before any approval.

``decide_consensus`` judges the returned reviewer verdicts and counts; the blockers here add the checks it cannot see:
an activated role at ``CHANGES``, a reviewer whose verdict adjudication would have changed, and a container whose own
ensemble verdict was not APPROVED. Any blocker turns an APPROVED verdict into CHANGES_REQUESTED, never the reverse.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sandbox_executor.flow.review.adjudication import Adjudication
from sandbox_executor.flow.review.records import _reviewer_summary, render_reviewer_summary_line
from sandbox_executor.review_ensemble import (
    ConsensusVerdict,
    ReviewerResult,
    ReviewerStatus,
    ReviewerVerdict,
    decide_consensus,
)
from sandbox_executor.review_report import finding_signature, parse_reviewer_detail


def role_objections(reviewers: Sequence[ReviewerResult]) -> list[str]:
    """One entry per activated role at ``CHANGES``; a role table that cannot be read counts as an objection."""
    objections: list[str] = []
    for result in reviewers:
        try:
            roles = parse_reviewer_detail(result).roles
        except Exception:
            objections.append(f"reviewer {result.index} role table unreadable")
            continue
        objections.extend(
            f"reviewer {result.index} {role.role} role CHANGES" for role in roles if role.verdict == "CHANGES"
        )
    return objections


def adjudication_mismatches(reviewers: Sequence[ReviewerResult], adjudication: Adjudication) -> list[str]:
    """Reviewers whose verdict would read APPROVED once suppressed findings were removed, with the finding ids."""
    suppressed = {finding_signature(r.severity, r.path, r.line, r.summary): r.id for r in adjudication.suppressed}
    mismatches = []
    for result in reviewers:
        signatures = [finding_signature(f.severity, f.path, f.line, f.summary) for f in result.findings]
        if signatures and all(sig in suppressed for sig in signatures):
            ids = ", ".join(sorted({suppressed[sig] for sig in signatures}))
            mismatches.append(
                f"reviewer {result.index} returned {result.verdict} with {len(signatures)} finding(s) but would read "
                f"APPROVED after adjudication (suppressed: {ids})"
            )
    return mismatches


def consensus_blockers(
    reviewers: Sequence[ReviewerResult],
    container_verdict: ConsensusVerdict | None,
    adjudication: Adjudication,
) -> list[str]:
    """Every reason beyond the reviewer verdicts that forbids approval; a missing container verdict adds none."""
    blockers = role_objections(reviewers) + adjudication_mismatches(reviewers, adjudication)
    if container_verdict is not None and container_verdict is not ConsensusVerdict.APPROVED:
        blockers.append(f"reviewer container reported ensemble verdict {container_verdict}")
    return blockers


def decide_returned_consensus(
    reviewers: Sequence[ReviewerResult],
    ensemble_size: int,
    mergeable: bool | None,
    ci_green: bool | None,
    blockers: Sequence[str],
) -> ConsensusVerdict:
    """``decide_consensus`` on the returned reviewers, with APPROVED downgraded while any blocker stands."""
    verdict = decide_consensus(reviewers, ensemble_size, mergeable=mergeable, ci_green=ci_green)
    if verdict is ConsensusVerdict.APPROVED and blockers:
        return ConsensusVerdict.CHANGES_REQUESTED
    return verdict


def _summaries(reviewers: Sequence[ReviewerResult | Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [_reviewer_summary(r) if isinstance(r, ReviewerResult) else r for r in reviewers]


def _final_verdict(data: Mapping[str, Any]) -> str:
    return str(data.get("verdict")) if data.get("status") == str(ReviewerStatus.COMPLETED) else "FAILED"


def render_final_verdicts(reviewers: Sequence[ReviewerResult | Mapping[str, Any]]) -> str:
    """``Reviewer 1 APPROVED (0/0/0); ... Reviewer 3 CHANGES_REQUESTED (1/0/0)`` (critical/important/nit) and roles."""
    parts: list[str] = []
    for data in _summaries(reviewers):
        counts = data.get("counts") or {}
        tally = "/".join(str(counts.get(name, 0)) for name in ("critical", "important", "nit"))
        parts.append(f"Reviewer {data.get('index')} {_final_verdict(data)} ({tally})")
        roles = (data.get("review_data") or {}).get("roles") or []
        parts.extend(
            f"Reviewer {data.get('index')} {role.get('role')} role CHANGES"
            for role in roles
            if isinstance(role, Mapping) and role.get("verdict") == "CHANGES"
        )
    return "; ".join(parts) or "no reviewer returned a verdict"


def not_approved_verdicts(reviewers: Sequence[ReviewerResult | Mapping[str, Any]]) -> list[str]:
    """``reviewer N final verdict X`` for each reviewer that did not approve cleanly with zero findings."""
    return [
        f"reviewer {data.get('index')} final verdict {_final_verdict(data)}"
        for data in _summaries(reviewers)
        if _final_verdict(data) != str(ReviewerVerdict.APPROVED) or any((data.get("counts") or {}).values())
    ]


def review_loop_approved(loop_res: dict[str, Any]) -> bool:
    """True only for an APPROVED ensemble verdict, an approved consensus and every final verdict a clean APPROVED."""
    return (
        loop_res.get("ensemble_verdict") == str(ConsensusVerdict.APPROVED)
        and bool((loop_res.get("consensus") or {}).get("approved"))
        and not not_approved_verdicts(loop_res.get("reviewers") or [])
    )


def review_summary_lines(loop_res: dict[str, Any]) -> list[str]:
    """Summary lines with the real per-reviewer verdicts of the deciding iteration, naming any that did not approve."""
    reviewers = loop_res.get("reviewers") or []
    lines = [f"  Ensemble       : {loop_res.get('ensemble_verdict') or 'NOT RUN'}"]
    lines.extend(f"    {render_reviewer_summary_line(r)}" for r in reviewers)
    not_approved = not_approved_verdicts(reviewers)
    if not_approved:
        lines.append(f"  Not approved   : {'; '.join(not_approved)}")
    review_data = loop_res.get("review_data") or {}
    roles = (
        ", ".join(f"{r.get('role')} ({r.get('verdict')})" for r in review_data.get("roles") or []) or "none reported"
    )
    lines.append(f"  Roles          : {roles}; open findings: {len(review_data.get('findings') or [])}")
    return lines
