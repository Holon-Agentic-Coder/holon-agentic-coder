"""The Stage 4 review consensus report, rendered only from real reviewer results.

Every line of the report comes from data the review actually produced: the :class:`ReviewerResult` objects returned
by :func:`~sandbox_executor.review_ensemble.run_ensemble`, the optional reviewer data block each reviewer writes just
before its trailer, the ensemble verdict, the mergeability and CI probes, and the resolved target. Nothing is derived
from a constant: there are no generated reviewer votes, no persona matrix, no fixed verified assertions and no
boiler-plate findings. The posted shape is the "Posted review shape" block of
``holon-config/prompts/review_loop.template.md``.

The reviewer's vote comes only from the strict trailer. The data block (roles, finding ids and recommendations,
evidenced verified claims) is parsed tolerantly: a missing, malformed or misaligned block yields no roles and no
claims for that reviewer, never invented ones, and a FAILED reviewer contributes nothing but its breakdown line.

Reviewer text is untrusted: it is redacted, collapsed to one line, stripped of backticks and angle brackets, bounded
and, in table cells, pipe-escaped before it reaches the posted comment. Failure reasons are already redacted and
bounded by :meth:`ReviewerResult.failed` and are redacted once more here as defence in depth.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sandbox_executor.approval_stamp import approval_stamp
from sandbox_executor.git import run_git
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.resolver_evidence import OPEN, UNSPECIFIED_REASON, default_disposition, settle_rejection
from sandbox_executor.review_ensemble import (
    MAX_REASON_CHARS,
    SEVERITIES,
    TRAILER_OPEN,
    ConsensusVerdict,
    ReviewerResult,
    ReviewerStatus,
    ReviewerVerdict,
)
from sandbox_executor.review_target import ReviewTarget

__all__ = [
    "CLOSING_NOTICE",
    "DISPOSITIONS",
    "ROLES_CLOSE",
    "ROLES_OPEN",
    "VALID_REJECTION_REASONS",
    "FindingDisposition",
    "FindingRow",
    "ReviewData",
    "ReviewerDetail",
    "RoleReview",
    "VerifiedClaim",
    "build_on_disk_review_report",
    "build_pr_review_consensus_report",
    "build_review_consensus_report",
    "ci_failure_finding",
    "collect_review_data",
    "deserialize_finding_dispositions",
    "finding_signature",
    "parse_reviewer_detail",
    "render_dispositions_section",
    "render_on_disk_trailer",
    "render_reviewer_line",
    "report_approves",
    "resolve_report_title",
    "review_data_summary",
    "reviewer_detail_payload",
    "serialize_finding_disposition",
    "serialize_finding_for_resolver",
    "with_reviewer_detail",
]

MAX_SUMMARY_CHARS = 300
MAX_PATH_CHARS = 200
MAX_ROLE_CHARS = 60
MAX_REVIEW_WORDS = 25
MAX_BLOCK_CHARS = 20_000
MAX_BLOCK_ENTRIES = 50
ROLES_OPEN = "<!-- holon-review-roles -->"
ROLES_CLOSE = "<!-- /holon-review-roles -->"
ROLE_VERDICTS = ("PASS", "CHANGES")
DISPOSITIONS = ("applied", "rejected", OPEN)
VALID_REJECTION_REASONS = (
    "not diff-grounded",
    "inaccurate",
    "out of scope",
    "conflicting with a higher-severity finding",
    "no-convergence",
)
NOT_SUPPLIED = "not supplied"
CLOSING_NOTICE = "Stage 5 calibration will be added to this PR before merge. Merging remains with the human maintainer."
_ID_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,23}")
_EVIDENCE = re.compile(r"[\w./-]+(?:::[\w.\[\]-]+)?")
_GITHUB_SLUG = re.compile(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$")
_PR_URL_NUMBER = re.compile(r"/pull/(\d+)")


@dataclass(frozen=True)
class RoleReview:
    """One activated role: its name, ``PASS`` or ``CHANGES``, and a short review."""

    role: str
    verdict: str
    review: str


@dataclass(frozen=True)
class FindingRow:
    """One finding row; ``reviewers`` lists the index of every reviewer that raised it."""

    id: str
    severity: str
    path: str
    line: int | None
    reviewers: tuple[int, ...]
    summary: str
    recommendation: str


def ci_failure_finding(iteration: int, failing_names: Sequence[str]) -> FindingRow:
    """The ``FIND-CI-<iteration>`` finding the review loop hands the resolver when CI failed in ``iteration``."""
    names = f": {', '.join(failing_names)}" if failing_names else ""
    return FindingRow(
        id=f"FIND-CI-{iteration:03d}",
        severity="important",
        path="",
        line=None,
        reviewers=(),
        summary=f"CI checks failed in iteration {iteration}{names}.",
        recommendation="Run linters and tests locally, apply fixes, and commit the changes.",
    )


@dataclass(frozen=True)
class VerifiedClaim:
    """A claim a reviewer checked, with the file, module or test that evidences it."""

    claim: str
    evidence: str


@dataclass(frozen=True)
class ReviewerDetail:
    """What one reviewer's report supplied beyond its vote; empty for a FAILED reviewer."""

    index: int
    roles: tuple[RoleReview, ...] = ()
    findings: tuple[FindingRow, ...] = ()
    verified: tuple[VerifiedClaim, ...] = ()


@dataclass(frozen=True)
class FindingDisposition:
    """One adjudicated finding disposition from a resolver pass or convergence guard."""

    id: str
    severity: str
    path: str
    line: int | None
    summary: str
    recommendation: str = NOT_SUPPLIED
    disposition: str = "rejected"
    rejection_reason: str | None = None
    commit_sha: str | None = None
    introduced_by_action_id: str | None = None
    # What the resolver checked: a ``path:line`` or a test; required to reject an important or critical finding.
    evidence: str | None = None


@dataclass(frozen=True)
class ReviewData:
    """The merged review data of the deciding iteration, as the report renders it."""

    roles: tuple[RoleReview, ...] = ()
    findings: tuple[FindingRow, ...] = ()
    verified: tuple[VerifiedClaim, ...] = ()
    dispositions: tuple[FindingDisposition, ...] = ()


def _inline(text: Any, limit: int) -> str:
    """Make untrusted reviewer text safe for one markdown line: redacted, single-line, no code or HTML, bounded."""
    clean = " ".join(redact_agent_secrets(str(text or "")).split())
    clean = clean.replace("`", "'").replace("<", "&lt;").replace(">", "&gt;")
    return clean if len(clean) <= limit else clean[: limit - 3] + "..."


def _words(text: str, limit: int = MAX_REVIEW_WORDS) -> str:
    """Cut ``text`` to ``limit`` words, marking the cut with an ellipsis."""
    words = text.split()
    return text if len(words) <= limit else " ".join(words[:limit]) + "..."


def _cell(text: str) -> str:
    """Escape a pipe so already-inlined text cannot add a table column."""
    return text.replace("|", "\\|")


# --- Reviewer data seam --------------------------------------------------------------------------------------------


def _block_payload(report_text: str | None) -> dict[str, Any]:
    """The JSON object of the data block that sits immediately before the trailer, else an empty dict."""
    text = report_text or ""
    cut = text.rfind(TRAILER_OPEN)
    head = (text[:cut] if cut >= 0 else text).rstrip()
    if not head.endswith(ROLES_CLOSE):
        return {}
    start = head.rfind(ROLES_OPEN)
    raw = head[start + len(ROLES_OPEN) : -len(ROLES_CLOSE)].strip() if start >= 0 else ""
    if not raw or len(raw) > MAX_BLOCK_CHARS:
        return {}
    try:
        payload = json.loads(raw)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _entries(raw: Any) -> list[dict[str, Any]]:
    """The object entries of a block list, bounded; anything else is dropped."""
    return [item for item in raw[:MAX_BLOCK_ENTRIES] if isinstance(item, dict)] if isinstance(raw, list) else []


def _parse_roles(raw: Any) -> tuple[RoleReview, ...]:
    """Well-typed role entries with a name and a ``PASS`` or ``CHANGES`` verdict."""
    roles = []
    for item in _entries(raw):
        role = _inline(item.get("role"), MAX_ROLE_CHARS)
        verdict = str(item.get("verdict") or "").strip().upper()
        if role and verdict in ROLE_VERDICTS:
            roles.append(RoleReview(role, verdict, _words(_inline(item.get("review"), MAX_SUMMARY_CHARS))))
    return tuple(roles)


def _parse_verified(raw: Any) -> tuple[VerifiedClaim, ...]:
    """Claims whose evidence is a file, module or test path; a claim without evidence is dropped."""
    claims = []
    for item in _entries(raw):
        claim = _inline(item.get("claim"), MAX_SUMMARY_CHARS)
        evidence = _inline(item.get("evidence"), MAX_PATH_CHARS)
        if claim and _EVIDENCE.fullmatch(evidence) and ("/" in evidence or "." in evidence):
            claims.append(VerifiedClaim(claim, evidence))
    return tuple(claims)


def _finding_id(extra: Mapping[str, Any], index: int, number: int) -> str:
    """The block's id when it is a short token, else ``R{index}-F{number}``."""
    ident = str(extra.get("id") or "").strip()
    return ident if _ID_TOKEN.fullmatch(ident) else f"R{index}-F{number}"


def _pair_findings(result: ReviewerResult, raw: Any) -> tuple[FindingRow, ...]:
    """The trailer's findings, paired by position with the block's ids and recommendations.

    The trailer is authoritative: no row exists without a trailer finding. A block list of a different length cannot be
    aligned, so it is ignored and every row gets a generated id and ``not supplied``.
    """
    block = raw if isinstance(raw, list) and len(raw) == len(result.findings) else [{}] * len(result.findings)
    rows = []
    for number, (finding, extra) in enumerate(zip(result.findings, block, strict=True), start=1):
        extra = extra if isinstance(extra, dict) else {}
        rows.append(
            FindingRow(
                id=_finding_id(extra, result.index, number),
                severity=finding.severity,
                path=_inline(finding.path, MAX_PATH_CHARS),
                line=finding.line,
                reviewers=(result.index,),
                summary=_inline(finding.summary, MAX_SUMMARY_CHARS),
                recommendation=_inline(extra.get("recommendation"), MAX_SUMMARY_CHARS) or NOT_SUPPLIED,
            )
        )
    return tuple(rows)


def parse_reviewer_detail(result: ReviewerResult) -> ReviewerDetail:
    """Roles, finding rows and evidenced claims from one reviewer; a FAILED reviewer contributes nothing."""
    if result.status is not ReviewerStatus.COMPLETED:
        return ReviewerDetail(result.index)
    payload = _block_payload(result.report_text)
    return ReviewerDetail(
        index=result.index,
        roles=_parse_roles(payload.get("roles")),
        findings=_pair_findings(result, payload.get("findings")),
        verified=_parse_verified(payload.get("verified")),
    )


def _merge_roles(roles: Iterable[RoleReview]) -> tuple[RoleReview, ...]:
    """One role per case-insensitive name, first-seen order; CHANGES wins and keeps the review that caused it."""
    merged: dict[str, RoleReview] = {}
    for role in roles:
        seen = merged.get(role.role.lower())
        if seen is None:
            merged[role.role.lower()] = role
            continue
        verdict = "CHANGES" if "CHANGES" in (seen.verdict, role.verdict) else "PASS"
        preferred, other = (seen, role) if seen.verdict == verdict else (role, seen)
        merged[role.role.lower()] = RoleReview(seen.role, verdict, preferred.review or other.review)
    return tuple(merged.values())


def _merge_findings(rows: Iterable[FindingRow]) -> tuple[FindingRow, ...]:
    """One row per severity, path, line and summary, naming every reviewer that raised it.

    The summary is part of the key so distinct findings at the same location stay distinct rows and the rendered
    counts reconcile with the reviewer trailers; only the same finding raised by several reviewers merges.
    """
    merged: dict[tuple[str, str, int | None, str], FindingRow] = {}
    for row in rows:
        key = (row.severity, row.path, row.line, row.summary)
        seen = merged.get(key)
        if seen is None:
            merged[key] = row
            continue
        reviewers = seen.reviewers + tuple(i for i in row.reviewers if i not in seen.reviewers)
        recommendation = seen.recommendation if seen.recommendation != NOT_SUPPLIED else row.recommendation
        merged[key] = dataclasses.replace(seen, reviewers=reviewers, recommendation=recommendation)
    return _unique_ids(tuple(merged.values()))


def _unique_ids(rows: tuple[FindingRow, ...]) -> tuple[FindingRow, ...]:
    """Disambiguate block-supplied ids that collide across distinct rows.

    Two reviewers can independently supply the same id (say ``F-1``) for different findings; the first row keeps it and
    every later colliding row falls back to a generated ``R{reviewer}-F{n}`` id that no other row carries.
    """
    taken = {row.id for row in rows}
    assigned: set[str] = set()
    unique = []
    for number, row in enumerate(rows, start=1):
        ident = row.id
        while ident in assigned or (ident != row.id and ident in taken):
            ident = f"R{row.reviewers[0]}-F{number}"
            number += 1
        assigned.add(ident)
        unique.append(row if ident == row.id else dataclasses.replace(row, id=ident))
    return tuple(unique)


def collect_review_data(reviewers: Sequence[ReviewerResult]) -> ReviewData:
    """Merge the data of every completed reviewer; FAILED reviewers add no role, finding or claim."""
    details = [parse_reviewer_detail(result) for result in reviewers]
    return ReviewData(
        roles=_merge_roles(role for detail in details for role in detail.roles),
        findings=_merge_findings(row for detail in details for row in detail.findings),
        verified=tuple(dict.fromkeys(claim for detail in details for claim in detail.verified)),
    )


def reviewer_detail_payload(result: ReviewerResult) -> dict[str, Any]:
    """The reviewer's parsed, sanitised data block as JSON-safe data, for transport out of the review container."""
    detail = parse_reviewer_detail(result)
    return {
        "roles": [dataclasses.asdict(role) for role in detail.roles],
        "findings": [{"id": row.id, "recommendation": row.recommendation} for row in detail.findings],
        "verified": [dataclasses.asdict(claim) for claim in detail.verified],
    }


def with_reviewer_detail(result: ReviewerResult, payload: Any) -> ReviewerResult:
    """Re-attach a transported data block to a deserialised result; it is parsed and sanitised again on use."""
    if result.status is not ReviewerStatus.COMPLETED or not isinstance(payload, Mapping):
        return result
    try:
        block = json.dumps(dict(payload), ensure_ascii=False)
    except (TypeError, ValueError):
        return result
    return dataclasses.replace(result, report_text=f"{ROLES_OPEN}\n{block}\n{ROLES_CLOSE}")


def finding_signature(severity: Any, path: Any, line: Any, summary: Any) -> tuple[str, str, int | None, str]:
    """Normalised signature key for matching findings across iterations."""
    sev = str(severity or "nit").strip().lower()
    norm_sev = sev if sev in SEVERITIES else "nit"
    norm_path = _inline(path, MAX_PATH_CHARS)
    norm_line = line if isinstance(line, int) and not isinstance(line, bool) and line >= 1 else None
    norm_summary = _inline(summary, MAX_SUMMARY_CHARS)
    return (norm_sev, norm_path, norm_line, norm_summary)


def serialize_finding_for_resolver(
    row: FindingRow,
    *,
    introduced_by_action_id: str | None = None,
) -> dict[str, Any]:
    """Serialise one merged finding row for the resolver result-file transport."""
    payload: dict[str, Any] = {
        "id": row.id,
        "severity": row.severity,
        "path": row.path,
        "line": row.line,
        "file_line": row.path if row.line is None else f"{row.path}:{row.line}",
        "reviewers": list(row.reviewers),
        "summary": row.summary,
        "recommendation": row.recommendation,
    }
    if introduced_by_action_id:
        payload["introduced_by_action_id"] = introduced_by_action_id
    return payload


def serialize_finding_disposition(item: FindingDisposition) -> dict[str, Any]:
    """Serialise a :class:`FindingDisposition` to a JSON-safe dictionary."""
    data: dict[str, Any] = {
        "id": item.id,
        "severity": item.severity,
        "path": item.path,
        "line": item.line,
        "summary": item.summary,
        "recommendation": item.recommendation,
        "disposition": item.disposition,
        "rejection_reason": item.rejection_reason,
    }
    if item.commit_sha:
        data["commit_sha"] = item.commit_sha
    if item.introduced_by_action_id:
        data["introduced_by_action_id"] = item.introduced_by_action_id
    if item.evidence:
        data["evidence"] = item.evidence
    return data


def _normalise_rejection_reason(raw_reason: Any) -> str:
    """Validate and normalise a one-line rejection reason; an unknown or missing one is ``unspecified``.

    It used to default to ``out of scope``, which made a skipped or failed adjudication read as a deliberate rejection.
    """
    clean = _inline(raw_reason, MAX_REASON_CHARS)
    low = clean.lower()
    for allowed in VALID_REJECTION_REASONS:
        if low == allowed or low.startswith(allowed):
            return allowed
    return UNSPECIFIED_REASON


def deserialize_finding_dispositions(
    raw: Any,
    expected_findings: Sequence[FindingRow] = (),
    action_ids: Mapping[str, str] | None = None,
) -> tuple[FindingDisposition, ...]:
    """Deserialise resolver dispositions and ensure every expected finding has exactly one disposition."""
    lookup = dict(action_ids or {})
    by_id: dict[str, Mapping[str, Any]] = {}
    ordered_raw: list[Mapping[str, Any]] = []
    if isinstance(raw, list):
        for entry in raw[:MAX_BLOCK_ENTRIES]:
            if isinstance(entry, Mapping):
                ordered_raw.append(entry)
                ident = str(entry.get("id") or "").strip()
                if ident and ident not in by_id:
                    by_id[ident] = entry

    if not expected_findings:
        return tuple(_disposition_from_mapping(entry, None, lookup) for entry in ordered_raw)

    results: list[FindingDisposition] = []
    for idx, row in enumerate(expected_findings):
        matched = by_id.get(row.id)
        if matched is None and idx < len(ordered_raw) and not str(ordered_raw[idx].get("id") or "").strip():
            matched = ordered_raw[idx]
        results.append(_disposition_from_mapping(matched or {}, row, lookup))
    return tuple(results)


def _disposition_from_mapping(
    entry: Mapping[str, Any],
    fallback: FindingRow | None,
    action_ids: Mapping[str, str],
) -> FindingDisposition:
    """Construct one validated :class:`FindingDisposition` from raw mapping and optional fallback row."""
    ident = _inline(entry.get("id") or (fallback.id if fallback else "FIND-1"), 40) or "FIND-1"
    # The reviewed finding's severity is authoritative: a resolver cannot downgrade it to escape the evidence rule.
    raw_sev = str((fallback.severity if fallback else None) or entry.get("severity") or "nit").strip().lower()
    severity = raw_sev if raw_sev in SEVERITIES else (fallback.severity if fallback else "nit")
    path = _inline(entry.get("path") or entry.get("file") or (fallback.path if fallback else ""), MAX_PATH_CHARS)
    raw_line = entry.get("line", fallback.line if fallback else None)
    line = raw_line if isinstance(raw_line, int) and not isinstance(raw_line, bool) and raw_line >= 1 else None
    summary = _inline(entry.get("summary") or (fallback.summary if fallback else ""), MAX_SUMMARY_CHARS)
    recommendation = (
        _inline(entry.get("recommendation") or (fallback.recommendation if fallback else ""), MAX_SUMMARY_CHARS)
        or NOT_SUPPLIED
    )
    raw_disp = str(entry.get("disposition") or entry.get("review_disposition") or "").strip().lower()
    disposition = raw_disp if raw_disp in DISPOSITIONS else default_disposition(severity)
    reason: str | None = None
    evidence = _inline(entry.get("evidence") or "", MAX_REASON_CHARS) or None
    if disposition == "rejected":
        reason = _normalise_rejection_reason(entry.get("rejection_reason") or entry.get("reason"))
        disposition, reason, evidence = settle_rejection(severity, reason, evidence)
    commit_sha = _inline(entry.get("commit_sha") or "", 40) or None
    intro_id = _inline(entry.get("introduced_by_action_id") or action_ids.get(ident) or "", 64) or None
    return FindingDisposition(
        id=ident,
        severity=severity,
        path=path,
        line=line,
        summary=summary,
        recommendation=recommendation,
        disposition=disposition,
        rejection_reason=reason,
        commit_sha=commit_sha,
        introduced_by_action_id=intro_id,
        evidence=evidence,
    )


def render_dispositions_section(dispositions: Sequence[FindingDisposition]) -> list[str]:
    """Render a markdown table summarising adjudicated finding dispositions."""
    if not dispositions:
        return []
    lines = [
        "**Adjudicated dispositions:**",
        "",
        "| ID | Severity | File:Line | Disposition | Detail |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in dispositions:
        location = item.path if item.line is None else f"{item.path}:{item.line}"
        unapplied = item.rejection_reason or item.evidence or ""
        detail = item.commit_sha or "committed" if item.disposition == "applied" else unapplied
        lines.append(
            f"| {_cell(item.id)} | {item.severity} | {_cell(location)} | {item.disposition} | {_cell(detail)} |"
        )
    return lines


def review_data_summary(
    data: ReviewData,
    dispositions: Sequence[FindingDisposition] = (),
) -> dict[str, Any]:
    """A JSON-safe summary for the loop result: role names and verdicts, finding ids, severities and reviewers."""
    summary: dict[str, Any] = {
        "roles": [{"role": role.role, "verdict": role.verdict} for role in data.roles],
        "findings": [
            {"id": row.id, "severity": row.severity, "reviewers": list(row.reviewers)} for row in data.findings
        ],
    }
    resolved = dispositions or data.dispositions
    if resolved:
        summary["dispositions"] = [serialize_finding_disposition(item) for item in resolved]
    return summary


# --- Probe wording -------------------------------------------------------------------------------------------------


def _is_approved(consensus_data: Mapping[str, Any]) -> bool:
    """APPROVED follows the ensemble verdict alone; no flag or status can promote any other value."""
    return str(consensus_data.get("ensemble_verdict") or "") == str(ConsensusVerdict.APPROVED)


def _mergeability_word(consensus_data: Mapping[str, Any]) -> str:
    """CLEAN, DIRTY, CONFLICTING or MISSING_REF from the probe; anything else was not measured."""
    probe = consensus_data.get("mergeability") or {}
    state = str(probe.get("merge_state_status") or "").upper()
    if consensus_data.get("mergeability_note") or state == "MISSING_REF":
        return "MISSING_REF"
    if str(probe.get("mergeable") or "").upper() == "CONFLICTING":
        return "CONFLICTING"
    if probe.get("is_dirty") or state == "DIRTY":
        return "DIRTY"
    return "CLEAN" if state == "CLEAN" else "UNMEASURED"


def _ci_measured(ci_status: Mapping[str, Any]) -> bool:
    """False for an unmeasured probe, a missing probe or a probe with no checks."""
    return ci_status.get("measured") is not False and int(ci_status.get("total") or 0) > 0


def _ci_state(ci_status: Mapping[str, Any]) -> str:
    """One word for the evaluation paragraph."""
    if not _ci_measured(ci_status):
        return "unmeasured"
    if int(ci_status.get("failed") or 0):
        return "failing"
    return "pending" if int(ci_status.get("pending") or 0) else "passing"


def _ci_summary(ci_status: Mapping[str, Any]) -> str:
    """The CI part of the Checks line; an unmeasured probe is reported as such, never as a pass."""
    source = f" (source: {_inline(str(ci_status['test_source']), 40)})" if ci_status.get("test_source") else ""
    if not _ci_measured(ci_status):
        return f"CI unmeasured{source}"
    return (
        f"CI {int(ci_status.get('passed') or 0)}/{int(ci_status.get('total') or 0)} passed, "
        f"{int(ci_status.get('failed') or 0)} failing, {int(ci_status.get('pending') or 0)} pending{source}"
    )


# --- Target ---------------------------------------------------------------------------------------------------------


def resolve_report_title(intent_data: Mapping[str, Any] | None, target: ReviewTarget | None, raw_target: str) -> str:
    """Return the intent title, else the PR title, else ``PR #<n>`` or the branch name; never a fixed phrase."""
    for candidate in ((intent_data or {}).get("title"), target.title if target else None):
        if str(candidate or "").strip():
            return _inline(candidate, MAX_SUMMARY_CHARS)
    if target and target.pr_number is not None:
        return f"PR #{target.pr_number}"
    branch = (target.head_branch if target else "") or str(raw_target or "").strip()
    return _inline(branch, MAX_PATH_CHARS) or "unknown target"


def _git_output(repo_dir: str, args: list[str]) -> str:
    """Stripped stdout of a read-only git command, or an empty string when it fails."""
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        return ""
    try:
        proc = run_git(args, cwd=repo_dir, check=False)
    except Exception:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 and isinstance(proc.stdout, str) else ""


def _target_commit(repo_dir: str, consensus_data: Mapping[str, Any]) -> str:
    """The reviewed commit from the consensus data, else the checkout's short HEAD."""
    commit = str(consensus_data.get("target_commit") or "").strip()
    if commit and commit != "HEAD":
        return commit
    return _git_output(repo_dir, ["rev-parse", "--short", "HEAD"]) or "HEAD"


def _repo_name(repo_dir: str, consensus_data: Mapping[str, Any]) -> str:
    """The GitHub ``owner/name`` from the consensus data or the origin remote, else an empty string."""
    name = str(consensus_data.get("repo_name") or "").strip()
    if name:
        return name
    match = _GITHUB_SLUG.search(_git_output(repo_dir, ["remote", "get-url", "origin"]))
    return match.group(1) if match else ""


def _pr_link(repo_dir: str, target: str, consensus_data: Mapping[str, Any]) -> str:
    """A markdown link to the PR (or the branch) under review; plain text when no repository name resolves."""
    repo_name = _repo_name(repo_dir, consensus_data)
    pr_number = consensus_data.get("pr_number")
    pr_url = consensus_data.get("pr_url")
    if not pr_url and target.startswith(("http://", "https://")):
        pr_url = target
        match = _PR_URL_NUMBER.search(target)
        pr_number = match.group(1) if match else pr_number
    elif not pr_url and target.isdigit():
        pr_number = target
        pr_url = f"https://github.com/{repo_name}/pull/{target}" if repo_name else ""
    elif not pr_url and target:
        pr_url = f"https://github.com/{repo_name}/tree/{target}" if repo_name else ""
    if pr_url and pr_number and repo_name:
        return f"[{repo_name}#{pr_number}]({pr_url})"
    if pr_url:
        return f"[{target}]({pr_url})"
    return f"`{target or 'unknown'}`"


# --- Sections -------------------------------------------------------------------------------------------------------


def _clean_approval(result: ReviewerResult) -> bool:
    """True for a reviewer that completed with an ``APPROVED`` verdict and zero open findings."""
    return (
        result.status is ReviewerStatus.COMPLETED
        and result.verdict is ReviewerVerdict.APPROVED
        and not result.counts.total
        and not result.findings
    )


def _approvals(reviewers: Sequence[ReviewerResult]) -> int:
    """The number of reviewers that completed with an ``APPROVED`` verdict and zero open findings."""
    return sum(1 for r in reviewers if _clean_approval(r))


def report_approves(consensus_data: Mapping[str, Any], reviewers: Sequence[ReviewerResult]) -> bool:
    """APPROVED only for an APPROVED ensemble verdict, every reviewer clean and no activated role at ``CHANGES``.

    A defence in depth: an upstream regression that hands over an APPROVED verdict with a dissenting reviewer or role
    still renders, and posts, as changes requested.
    """
    roles = collect_review_data(reviewers).roles
    return (
        _is_approved(consensus_data)
        and bool(reviewers)
        and all(_clean_approval(r) for r in reviewers)
        and not any(role.verdict == "CHANGES" for role in roles)
    )


def _heading(consensus_data: Mapping[str, Any], approved: bool, approvals: int, configured: int) -> str:
    """The verdict heading: :func:`report_approves` decides the word, the clean approvals the fraction."""
    if approved:
        return f"## ✅ Ensemble Consensus Review — APPROVED ({approvals}/{configured})"
    iteration = consensus_data.get("iterations", 1)
    fraction = f"{approvals}/{configured} approved"
    return f"## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration {iteration}) — {fraction}"


def _severity_counts(findings: Sequence[FindingRow]) -> str:
    """``{c} critical, {i} important and {n} nit findings`` over the merged rows."""
    counts = [sum(1 for row in findings if row.severity == name) for name in SEVERITIES]
    return f"{counts[0]} critical, {counts[1]} important and {counts[2]} nit findings"


def _evaluation(repo_dir: str, target: str, consensus_data: Mapping[str, Any], data: ReviewData, title: str) -> str:
    """The evaluation paragraph: title, commit, base branch, finding counts, CI state and mergeability."""
    mergeability = _mergeability_word(consensus_data)
    ci = _ci_state(consensus_data.get("ci_checks") or {})
    wait = consensus_data.get("ci_wait") or {}
    if ci == "pending" and wait.get("state") == "pending":
        ci += f" after a {float(wait.get('budget_seconds') or 0):g} s wait"
    note = _inline(consensus_data.get("mergeability_note"), MAX_REASON_CHARS)
    base = _inline(consensus_data.get("base_branch") or "main", MAX_PATH_CHARS)
    link = _pr_link(repo_dir, target, consensus_data)
    subject = link if title == target and link == f"`{target}`" else f"{title} ({link})"
    return (
        f"Reviewed {subject} at "
        f"`{_target_commit(repo_dir, consensus_data)}` against `{base}`: {_severity_counts(data.findings)}; "
        f"CI {ci}; mergeability {mergeability}" + (f" ({note})." if note else ".")
    )


def _role_section(roles: Sequence[RoleReview]) -> list[str]:
    """The role table, one row per role a reviewer declared activated; no rows are invented."""
    if not roles:
        return ["**Role reviews:** No reviewer reported an activated role."]
    lines = ["**Role reviews:**", "", "| Role (activated) | Verdict | Review |", "| --- | --- | --- |"]
    lines.extend(f"| {_cell(role.role)} | {role.verdict} | {_cell(role.review)} |" for role in roles)
    return lines


def _verified_section(verified: Sequence[VerifiedClaim]) -> list[str]:
    """The Verified bullets, each naming its evidence; no bullet without an evidenced claim."""
    if not verified:
        return ["**Verified:** No reviewer supplied an evidenced claim."]
    return ["**Verified:**", "", *(f"- {claim.claim} — `{claim.evidence}`" for claim in verified)]


def _flagged_by(reviewers: Sequence[int]) -> str:
    """``[Flagged by Reviewer 1 & 2] `` for a finding several reviewers raised, else nothing."""
    if len(reviewers) < 2:
        return ""
    names = [str(index) for index in reviewers]
    return f"[Flagged by Reviewer {', '.join(names[:-1])} & {names[-1]}] "


def _findings_section(findings: Sequence[FindingRow]) -> list[str]:
    """The findings table; with no reviewer finding, one sentence pointing at the checks."""
    if not findings:
        return ["No reviewer reported a finding; the verdict follows from the reviewers and checks below."]
    lines = [
        "| ID | Severity | File:Line | Reviewer | Finding | Recommendation |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in findings:
        location = row.path if row.line is None else f"{row.path}:{row.line}"
        lines.append(
            f"| {_cell(row.id)} | {row.severity} | {_cell(location)} | {', '.join(map(str, row.reviewers))} | "
            f"{_flagged_by(row.reviewers)}{_cell(row.summary)} | {_cell(row.recommendation)} |"
        )
    return lines


def _approval_stamp(repo_dir: str, consensus_data: Mapping[str, Any]) -> str:
    """The line naming the head an approval covers and the CI result at that commit."""
    return approval_stamp(_target_commit(repo_dir, consensus_data), consensus_data.get("ci_checks") or {})


def _checks_line(consensus_data: Mapping[str, Any]) -> str:
    """One Checks line: the mergeability word and the CI counts (or ``CI unmeasured``)."""
    ci_summary = _ci_summary(consensus_data.get("ci_checks") or {})
    return f"**Checks:** Mergeability {_mergeability_word(consensus_data)} · {ci_summary}"


def render_reviewer_line(result: ReviewerResult) -> str:
    """One breakdown line per reviewer: index, agent and model, and verdict with counts or ``FAILED`` and the reason."""
    head = f"- **Reviewer {result.index}** (`{_inline(result.agent, 80)} / {_inline(result.model, 80)}`)"
    if result.status is not ReviewerStatus.COMPLETED:
        return f"{head}: `FAILED` ({_inline(result.failure_reason or 'unknown failure', MAX_REASON_CHARS)})"
    counts = result.counts
    return f"{head}: `{result.verdict}` (critical: {counts.critical}, important: {counts.important}, nit: {counts.nit})"


def _breakdown(reviewers: Sequence[ReviewerResult], consensus_data: Mapping[str, Any], configured: int) -> list[str]:
    """Every reviewer's line, the consensus verdict and the reviewed-by line."""
    verdict = _inline(consensus_data.get("ensemble_verdict") or "NONE", 40)
    agent = _inline(consensus_data.get("agent") or "unknown", 80)
    model = _inline(consensus_data.get("model") or "unknown", 80)
    return [
        "### 🗳️ Ensemble Review Breakdown",
        "",
        *(render_reviewer_line(result) for result in reviewers),
        f"- **Ensemble Consensus Verdict**: `{verdict}`",
        "",
        f"Reviewed by `{agent}` ({configured}-Agent Ensemble) · Model `{model}`",
    ]


def build_review_consensus_report(
    repo_dir: str,
    target: str,
    consensus_data: Mapping[str, Any],
    reviewers: Sequence[ReviewerResult] = (),
    title: str | None = None,
) -> str:
    """Render the posted consensus review from real reviewer results, probe data and the resolved title.

    Args:
        repo_dir: Repository the review ran in (for the PR link and the commit fallback).
        target: PR number, PR URL or branch under review.
        consensus_data: Consensus diagnostics from ``run_review_loop()`` (verdict, CI, mergeability, iteration).
        reviewers: The reviewer results of the deciding iteration, in order.
        title: The resolved report title; when omitted, the intent title or the target is used.

    Returns:
        The markdown body in the template's "Posted review shape", without the on-disk trailer.
    """
    clean_target = str(target or "").strip()
    data = collect_review_data(reviewers)
    configured = int(consensus_data.get("ensemble_size") or len(reviewers))
    report_title = title or resolve_report_title(consensus_data.get("intent_data"), None, clean_target)
    # Open findings are always listed; an approved report has none, so it shows its Verified list.
    body = _findings_section(data.findings)
    approved = report_approves(consensus_data, reviewers)
    if approved:
        body = _verified_section(data.verified) + (["", *body] if data.findings else [])
    lines = [
        _heading(consensus_data, approved, _approvals(reviewers), configured),
        "",
        _evaluation(repo_dir, clean_target, consensus_data, data, report_title),
        "",
        *_role_section(data.roles),
        "",
        *body,
        "",
        _checks_line(consensus_data),
        *(["", _approval_stamp(repo_dir, consensus_data)] if approved else []),
        "",
        *_breakdown(reviewers, consensus_data, configured),
        "",
        CLOSING_NOTICE,
    ]
    return "\n".join(lines)


build_pr_review_consensus_report = build_review_consensus_report


# --- On-disk report ------------------------------------------------------------------------------------------------


def _ci_word(ci_status: Mapping[str, Any]) -> str:
    """PASS, FAIL, PENDING or SKIPPED (unmeasured) for the on-disk trailer."""
    return {"passing": "PASS", "failing": "FAIL", "pending": "PENDING"}.get(_ci_state(ci_status), "SKIPPED")


def render_on_disk_trailer(consensus_data: Mapping[str, Any], review_data: ReviewData, report_path: str) -> str:
    """The machine-readable trailer for on-disk reports only; it is never part of a posted body."""
    verdict = str(consensus_data.get("ensemble_verdict") or "")
    known = (str(ConsensusVerdict.APPROVED), str(ConsensusVerdict.CHANGES_REQUESTED))
    counts = {name: sum(1 for row in review_data.findings if row.severity == name) for name in SEVERITIES}
    return "\n".join(
        [
            f"VERDICT: {verdict if verdict in known else str(ConsensusVerdict.CHANGES_REQUESTED)}",
            f"CI: {_ci_word(consensus_data.get('ci_checks') or {})}",
            f"REPORT: {_inline(report_path, MAX_PATH_CHARS)}",
            *(f"{name.upper()}: {count}" for name, count in counts.items()),
            f"FINDINGS: {len(review_data.findings)}",
        ]
    )


def build_on_disk_review_report(
    posted_body: str, consensus_data: Mapping[str, Any], reviewers: Sequence[ReviewerResult], report_path: str
) -> str:
    """A posted body from :func:`build_review_consensus_report` followed by the on-disk trailer as its final lines."""
    trailer = render_on_disk_trailer(consensus_data, collect_review_data(reviewers), report_path)
    return f"{posted_body.rstrip()}\n\n{trailer}\n"
