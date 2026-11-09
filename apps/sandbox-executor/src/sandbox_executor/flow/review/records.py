"""Stage 4 review records: refused-review results, bounded error context, review action rows in the ledger
and the reviewer summary line.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.git import EMPTY_DIFF_FAILURE_REASON
from sandbox_executor.knowledge import CredentialRedactor
from sandbox_executor.ledger import ActionRecord, generate_action_id, try_append_action_record
from sandbox_executor.review_ensemble import ReviewerResult, ReviewerStatus, ReviewerVerdict
from sandbox_executor.review_report import (
    finding_signature,
    parse_reviewer_detail,
    reviewer_detail_payload,
    with_reviewer_detail,
)

POSTING_ERROR_CONTEXT_MAX_CHARS = 2000


def _refused_review_result(review_target: str, last_iteration: int, label_budget: int, error: str) -> dict[str, Any]:
    """Build the non-approved result for a target whose diff is empty: no PR, no new passes, no post.

    ``last_iteration`` is the label of the latest pass already run (``first_label - 1``; 0 on a fresh
    run), so the refusal speaks the same label units as every other result.
    """
    return {
        "target": review_target,
        "consensus": {
            "approved": False,
            "score": 0.0,
            "iterations": last_iteration,
            "passes": 0,
            "max_review_iterations": label_budget,
            "status": "rejected",
        },
        "halted_for_human": False,
        "human_merge_gate_enforced": False,
        "mergeability": {},
        "ci_checks": {},
        "trailers": "",
        "total_iterations": last_iteration,
        "max_iterations": label_budget,
        "findings": [],
        "severity": "critical",
        "posting": {},
        "pr": {"success": False, "skipped": True, "reason": EMPTY_DIFF_FAILURE_REASON},
        "error": error,
    }


def _bounded_error_context(detail: str) -> str:
    """Redact ``detail`` with the canonical ``CredentialRedactor``, then cap its length.

    Redaction runs before truncation so a secret cut at the boundary cannot slip past the
    redaction patterns. Used for every Stage 4 / Stage 5 ``error_context`` that can carry
    unbounded external output (posting failures, calibration hook output).

    Args:
        detail: Raw failure detail.

    Returns:
        The redacted detail, at most ``POSTING_ERROR_CONTEXT_MAX_CHARS`` characters long.
    """
    detail = CredentialRedactor.redact_text(detail)
    if len(detail) > POSTING_ERROR_CONTEXT_MAX_CHARS:
        detail = detail[: POSTING_ERROR_CONTEXT_MAX_CHARS - 3] + "..."
    return detail


def _posting_failure_context(posting_res: dict[str, Any]) -> str:
    """Build the redacted ``error_context`` for a failed ACT-POST row.

    ``post_pr_review_consensus()`` reports failures through its return value rather than by
    raising, so the detail is collected from ``error``, ``diagnostics`` and the ``output`` of
    each unsuccessful posting step. The text is redacted with the canonical
    ``CredentialRedactor`` before it is truncated, so a partially cut secret cannot slip past
    the redaction patterns.

    Args:
        posting_res: Return value of ``post_pr_review_consensus()``, or ``{"error": ...}``.

    Returns:
        The redacted, length-bounded failure detail.
    """
    parts: list[str] = []
    for value in (posting_res.get("error"), posting_res.get("diagnostics")):
        if value:
            parts.append(str(value))
    for step in ("discussion_comment", "official_review"):
        step_res = posting_res.get(step)
        if isinstance(step_res, dict) and not step_res.get("success") and step_res.get("output"):
            parts.append(f"{step}: {step_res['output']}")
    unique_parts = list(dict.fromkeys(part.strip() for part in parts if part.strip()))
    detail = "; ".join(unique_parts) or "Consensus posting did not report success"
    return _bounded_error_context(detail)


def record_review_action(
    context: FlowContext,
    ledger_path: str,
    record: ActionRecord | dict[str, Any],
) -> dict[str, Any] | None:
    """Append a Stage 4 review action record to ``executions.jsonl``.

    This is the canonical writer for review provenance (ACT-REV, ACT-FIND, ACT-RES and
    ACT-POST rows), whichever integration point drives the review. The record is
    credential-redacted, validated against ``ActionRecord``, written verbatim (key order
    unchanged) under LedgerLock, and any failure is logged at WARNING level and mirrored to ``context.log``
    without raising.

    Args:
        context: Active FlowContext, used for operator-facing warnings.
        ledger_path: Path to the target repository's ``holon-knowledge/ledger/executions.jsonl``.
        record: Action record to append.

    Returns:
        The written record, or None if the append failed.
    """
    if isinstance(record, ActionRecord):
        label = f"Stage 4 review action {record.action_id}"
    else:
        label = f"Stage 4 review action {str(record.get('action_id') or '?').rsplit('-', 2)[0]}"
    return try_append_action_record(ledger_path, record, label=label, preserve_shape=True, log_fn=context.log)


_SEVERITY_RANK = {"clean": 0, "nit": 1, "important": 2, "critical": 3}


def _worst_severity(*severities: str) -> str:
    """The most severe of ``severities`` (``clean`` when none is given)."""
    return max(severities, key=lambda value: _SEVERITY_RANK.get(value, 0), default="clean")


def _reviewer_summary(result: ReviewerResult) -> dict[str, Any]:
    """A JSON-safe per-reviewer summary; never the report text."""
    findings_list = [
        {"severity": f.severity, "file": f.path, "line": f.line, "summary": f.summary} for f in result.findings
    ]
    return {
        "index": result.index,
        "agent": result.agent,
        "model": result.model,
        "status": str(result.status),
        "verdict": str(result.verdict) if result.verdict else None,
        "counts": {"critical": result.counts.critical, "important": result.counts.important, "nit": result.counts.nit},
        "findings": findings_list,
        "failure_reason": result.failure_reason,
        "review_data": reviewer_detail_payload(result),
    }


def _attach_transported_detail(
    reviewers: list[ReviewerResult], transported: Sequence[Any] | None
) -> list[ReviewerResult]:
    """Re-attach each reviewer's sanitised data block, which the container transport carries beside the result."""
    payloads = {item.get("index"): item.get("review_data") for item in transported or [] if isinstance(item, Mapping)}
    return [with_reviewer_detail(result, payloads.get(result.index)) for result in reviewers]


def _reviewer_finding_ids(result: ReviewerResult, iteration: int) -> list[str]:
    """Stable ids for one reviewer's findings, numbered in report order."""
    return [f"FIND-R{result.index}-{iteration:03d}-{n:02d}" for n in range(1, len(result.findings) + 1)]


def _review_row(  # noqa: PLR0913
    prefix: str,
    review_target: str,
    iteration: int,
    action: str,
    outcome: str,
    *,
    severity: str | None,
    finding_ids: list[str] | None,
    labels: list[str],
    error_context: str | None,
    **extra: Any,
) -> dict[str, Any]:
    """A Stage 4 ACT-REV or ACT-FIND row in the historical key order; ``extra`` keys go before ``created_at``."""
    kind = "FIND" if prefix == "ACT-FIND" else "ITR"
    row: dict[str, Any] = {
        "action_id": generate_action_id(prefix),
        "execution_id": f"REV-{kind}-{iteration}-{int(time.time())}",
        "action": action,
        "category": "code",
        "target": review_target,
        "outcome": outcome,
        "entry_type": "action",
        "action_origin": "pr_review",
        "review_severity": severity,
        "review_iteration": iteration,
        "review_finding_ids": finding_ids or None,
        "labels": labels,
        "error_context": error_context,
        **extra,
    }
    row["created_at"] = datetime.now(UTC).isoformat()
    return row


def _record_reviewer_rows(
    context: FlowContext,
    review_target: str,
    iteration: int,
    results: list[ReviewerResult],
    finding_action_ids: dict[str, str] | None = None,
) -> None:
    """Append one ACT-REV row per reviewer and one ACT-FIND row per reviewer finding."""
    exec_file = os.path.join(context.target_worktree, "holon-knowledge", "ledger", "executions.jsonl")
    for result in results:
        summary = _reviewer_summary(result)
        clean = summary["verdict"] == str(ReviewerVerdict.APPROVED) and not result.counts.total and not result.findings
        finding_ids = _reviewer_finding_ids(result, iteration)
        worst = _worst_severity(*(finding.severity for finding in result.findings))
        record_review_action(
            context,
            exec_file,
            _review_row(
                "ACT-REV",
                review_target,
                iteration,
                f"Review Loop iteration {iteration} reviewer {result.index} {summary['status'].lower()}",
                "success" if clean else "failure",
                severity=None if worst == "clean" else worst,
                finding_ids=finding_ids,
                labels=["review", "reviewer", f"iteration-{iteration}"],
                error_context=_bounded_error_context(result.failure_reason) if result.failure_reason else None,
                review_reviewer_index=result.index,
                review_agent=result.agent,
                review_model=result.model,
                review_status=summary["status"],
                review_verdict=summary["verdict"],
                review_counts=summary["counts"],
            ),
        )
        detail_rows = parse_reviewer_detail(result).findings
        for idx, (finding_id, finding) in enumerate(zip(finding_ids, result.findings, strict=True)):
            find_row = _review_row(
                "ACT-FIND",
                review_target,
                iteration,
                f"Review Loop iteration {iteration} finding detected: {finding_id}",
                "failure",
                severity=finding.severity,
                finding_ids=[finding_id],
                labels=["review", "finding", f"iteration-{iteration}"],
                error_context=_bounded_error_context(finding.summary),
                review_reviewer_index=result.index,
                review_file=finding.path,
                review_line=finding.line,
            )
            record_review_action(context, exec_file, find_row)
            if finding_action_ids is not None:
                act_id = str(find_row["action_id"])
                finding_action_ids.setdefault(finding_id, act_id)
                sig_key = str(finding_signature(finding.severity, finding.path, finding.line, finding.summary))
                finding_action_ids.setdefault(sig_key, act_id)
                if idx < len(detail_rows):
                    finding_action_ids.setdefault(detail_rows[idx].id, act_id)
                    row_sig = str(
                        finding_signature(
                            detail_rows[idx].severity,
                            detail_rows[idx].path,
                            detail_rows[idx].line,
                            detail_rows[idx].summary,
                        )
                    )
                    finding_action_ids.setdefault(row_sig, act_id)


def _record_infrastructure_findings(  # noqa: PLR0913
    context: FlowContext,
    exec_file: str,
    review_target: str,
    iteration: int,
    finding_ids: list[str],
    note: str,
    finding_action_ids: dict[str, str] | None = None,
) -> None:
    """Append one ACT-FIND row per mergeability or CI finding (``FIND-DIRTY``, ``FIND-REF``, ``FIND-CI``)."""
    for finding_id in finding_ids:
        row = _review_row(
            "ACT-FIND",
            review_target,
            iteration,
            f"Review Loop iteration {iteration} finding detected: {finding_id}",
            "failure",
            severity="critical" if finding_id.startswith("FIND-DIRTY") else "important",
            finding_ids=[finding_id],
            labels=["review", "finding", f"iteration-{iteration}"],
            error_context=note,
        )
        record_review_action(context, exec_file, row)
        if finding_action_ids is not None:
            act_id = str(row["action_id"])
            finding_action_ids.setdefault(finding_id, act_id)


def render_reviewer_summary_line(result: ReviewerResult | Mapping[str, Any]) -> str:
    """One plain-text summary line for a reviewer (a ``ReviewerResult`` or its serialised summary)."""
    data = _reviewer_summary(result) if isinstance(result, ReviewerResult) else result
    head = f"Reviewer {data.get('index')}: {data.get('agent')} / {data.get('model')}"
    if data.get("status") != str(ReviewerStatus.COMPLETED):
        return f"{head}: FAILED ({_bounded_error_context(str(data.get('failure_reason') or 'unknown failure'))})"
    counts = data.get("counts") or {}
    return (
        f"{head}: {data.get('verdict')} (critical {counts.get('critical', 0)}, "
        f"important {counts.get('important', 0)}, nit {counts.get('nit', 0)})"
    )
