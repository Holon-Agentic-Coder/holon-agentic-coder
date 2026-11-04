"""The Stage 4 review consensus comment, rendered only from real reviewer results.

The posted body follows the ``## Posted review shape`` section of ``holon-config/prompts/review_loop.template.md``, in
this order: the verdict heading, one evaluation paragraph, the ``**Role reviews:**`` table, the ``**Verified:**``
bullets (approval) or the findings table (otherwise), one ``**Checks:**`` line, the ``### 🗳️ Ensemble Review
Breakdown`` footer and the closing notice that merging remains with the human maintainer.

Every element comes from data the review produced: the :class:`ReviewerResult` objects of the deciding iteration, the
ensemble verdict, the mergeability and CI probes and the resolved target. The heading follows
``consensus_data["ensemble_verdict"]`` alone, and an unmeasured probe renders as ``unmeasured``.

Reviewer details block
    The trailer parser in ``review_ensemble.py`` accepts only ``severity``, ``file``, ``line`` and ``summary`` per
    finding, so roles, per-role verdicts, recommendations and verified evidence travel in a second block of the same
    report, between :data:`DETAILS_OPEN` and :data:`DETAILS_CLOSE`, placed before the trailer.
    :func:`parse_reviewer_details` reads it defensively: a missing, malformed or oversized block yields empty details,
    so that reviewer still votes through its trailer but contributes no roles, no recommendations and no verified
    bullets. Nothing is ever invented in their place.

Reviewer text is untrusted: every string is redacted, collapsed to one line, stripped of backticks and angle brackets,
bounded and, in table cells, pipe-escaped before it reaches the posted comment. The machine-readable trailer never
appears in a posted body.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sandbox_executor.git import run_git
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.review_ensemble import (
    MAX_REASON_CHARS,
    ConsensusVerdict,
    ReviewerResult,
    ReviewerStatus,
    ReviewFinding,
)
from sandbox_executor.review_target import ReviewTarget

__all__ = [
    "CLOSING_NOTICE",
    "DETAILS_CLOSE",
    "DETAILS_OPEN",
    "ReviewerDetails",
    "RoleReview",
    "VerifiedItem",
    "build_pr_review_consensus_report",
    "build_review_consensus_report",
    "details_from_mapping",
    "finding_id",
    "parse_reviewer_details",
    "render_reviewer_details",
    "render_reviewer_line",
    "resolve_report_title",
]

MAX_SUMMARY_CHARS = 300
MAX_PATH_CHARS = 200
MAX_ROLE_CHARS = 60
MAX_REVIEW_CHARS = 160
MAX_CLAIM_CHARS = 200
MAX_DETAILS_CHARS = 64 * 1024
DETAILS_OPEN = "<!-- holon-review-details -->"
DETAILS_CLOSE = "<!-- /holon-review-details -->"
CLOSING_NOTICE = "Stage 5 calibration will be added to this PR before merge. Merging remains with the human maintainer."
ROLE_VERDICTS = ("PASS", "CHANGES")
NO_ROLES_ROW = "| none reported | — | No reviewer supplied role data. |"
NO_VERIFIED_BULLET = "- No reviewer supplied evidenced verification."
NOT_SUPPLIED = "not supplied"
UNMEASURED = "unmeasured"
_EVIDENCE_MARKERS = ("/", ".py", ".md", "test_", "::")
_SEVERITY_LABELS = {"critical": "Critical", "important": "Important", "nit": "Nit"}
_MERGE_STATES = ("CLEAN", "DIRTY", "MISSING_REF")
_GITHUB_SLUG = re.compile(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$")
_PR_URL_NUMBER = re.compile(r"/pull/(\d+)")


@dataclass(frozen=True)
class RoleReview:
    """One role a reviewer activated, with its ``PASS`` or ``CHANGES`` verdict and a short review."""

    role: str
    verdict: str
    review: str


@dataclass(frozen=True)
class VerifiedItem:
    """One claim a reviewer verified, with the file, module or test that evidences it."""

    claim: str
    evidence: str


@dataclass(frozen=True)
class ReviewerDetails:
    """What a reviewer's details block supplied; empty when the block is missing or malformed."""

    roles: tuple[RoleReview, ...] = ()
    recommendations: tuple[str, ...] = ()
    verified: tuple[VerifiedItem, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """A JSON-safe form that :func:`details_from_mapping` reads back unchanged."""
        return {
            "roles": [{"role": r.role, "verdict": r.verdict, "review": r.review} for r in self.roles],
            "recommendations": list(self.recommendations),
            "verified": [{"claim": v.claim, "evidence": v.evidence} for v in self.verified],
        }


def _inline(text: Any, limit: int) -> str:
    """Make untrusted reviewer text safe for one markdown line: redacted, single-line, no code or HTML, bounded."""
    clean = " ".join(redact_agent_secrets(str(text or "")).split())
    clean = clean.replace("`", "'").replace("<", "&lt;").replace(">", "&gt;")
    return clean if len(clean) <= limit else clean[: limit - 3] + "..."


def _cell(text: Any, limit: int) -> str:
    """:func:`_inline` text that also cannot split a markdown table cell."""
    return _inline(text, limit).replace("|", "\\|")


def finding_id(index: int, iteration: int, n: int) -> str:
    """The id of reviewer ``index``'s ``n``-th finding in ``iteration``; the ledger ``ACT-FIND`` rows use it too."""
    return f"FIND-R{index}-{iteration:03d}-{n:02d}"


# --- Reviewer details block ----------------------------------------------------------------------------------------


def parse_reviewer_details(report_text: str | None) -> ReviewerDetails:
    """The last details block in ``report_text``; empty details for a missing, malformed or oversized block."""
    text = str(report_text or "")
    start = text.rfind(DETAILS_OPEN)
    end = text.find(DETAILS_CLOSE, start) if start >= 0 else -1
    inner = text[start + len(DETAILS_OPEN) : end].strip() if end >= 0 else ""
    if not inner or len(inner) > MAX_DETAILS_CHARS:
        return ReviewerDetails()
    try:
        return details_from_mapping(json.loads(inner))
    except (ValueError, RecursionError):
        return ReviewerDetails()


def details_from_mapping(raw: Any) -> ReviewerDetails:
    """Sanitised details from a decoded block (or a transported ``as_dict()``); malformed entries are dropped."""
    if not isinstance(raw, Mapping):
        return ReviewerDetails()
    roles = tuple(item for item in map(_role_review, _entries(raw.get("roles"))) if item)
    verified = tuple(item for item in map(_verified_item, _entries(raw.get("verified"))) if item)
    recommendations = tuple(_inline(_string(item), MAX_SUMMARY_CHARS) for item in _entries(raw.get("recommendations")))
    return ReviewerDetails(roles, recommendations, verified)


def render_reviewer_details(details: ReviewerDetails) -> str:
    """The canonical details block for ``details``; :func:`parse_reviewer_details` reads it back unchanged."""
    return f"{DETAILS_OPEN}\n{json.dumps(details.as_dict(), ensure_ascii=False)}\n{DETAILS_CLOSE}"


def _entries(raw: Any) -> list[Any]:
    """``raw`` when it is a list, else no entries."""
    return raw if isinstance(raw, list) else []


def _string(value: Any) -> str:
    """``value`` when it is a string, else an empty string (numbers and objects are not text)."""
    return value if isinstance(value, str) else ""


def _field(item: Mapping[str, Any], key: str) -> str:
    """The string under ``key``, or an empty string."""
    return _string(item.get(key))


def _role_review(item: Any) -> RoleReview | None:
    """One role row; a missing role or a verdict other than ``PASS`` or ``CHANGES`` drops it."""
    if not isinstance(item, Mapping):
        return None
    role = _inline(_field(item, "role"), MAX_ROLE_CHARS)
    verdict = _field(item, "verdict").strip().upper()
    if not role or verdict not in ROLE_VERDICTS:
        return None
    return RoleReview(role, verdict, _inline(_field(item, "review"), MAX_REVIEW_CHARS))


def _verified_item(item: Any) -> VerifiedItem | None:
    """One verified claim; a claim whose evidence names no file, module or test is dropped."""
    if not isinstance(item, Mapping):
        return None
    claim = _inline(_field(item, "claim"), MAX_CLAIM_CHARS)
    evidence = _inline(_field(item, "evidence"), MAX_PATH_CHARS)
    if not claim or not any(marker in evidence for marker in _EVIDENCE_MARKERS):
        return None
    return VerifiedItem(claim, evidence)


# --- Title, link and commit ----------------------------------------------------------------------------------------


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
        return _inline(commit, 80)
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


# --- Probes --------------------------------------------------------------------------------------------------------


def _count(value: Any) -> int:
    """A non-negative probe count; anything unreadable counts as zero."""
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _ci_state(ci_status: Mapping[str, Any]) -> str:
    """``passed/total passed, failed failing, pending pending``, or ``unmeasured`` for an unmeasured or empty probe."""
    total = _count(ci_status.get("total"))
    if ci_status.get("measured") is False or not total:
        return UNMEASURED
    passed, failed, pending = (_count(ci_status.get(key)) for key in ("passed", "failed", "pending"))
    return f"{passed}/{total} passed, {failed} failing, {pending} pending"


def _merge_state(mergeability: Mapping[str, Any]) -> str:
    """``CONFLICTING``, ``CLEAN``, ``DIRTY`` or ``MISSING_REF`` from the probe, else ``unmeasured``."""
    if str(mergeability.get("mergeable") or "").upper() == "CONFLICTING":
        return "CONFLICTING"
    state = str(mergeability.get("merge_state_status") or "").upper()
    if state in _MERGE_STATES:
        return state
    return "DIRTY" if mergeability.get("is_dirty") else UNMEASURED


def _checks_line(merge: str, ci_state: str, note: Any) -> str:
    """The one ``**Checks:**`` line, with the loop's mergeability note appended when it has one."""
    line = f"**Checks:** mergeability `{merge}` · CI `{ci_state}`"
    clean_note = _inline(note, MAX_REASON_CHARS)
    return f"{line} · {clean_note}" if clean_note else line


# --- Roles, findings and verified evidence -------------------------------------------------------------------------


@dataclass
class _FindingRow:
    """One de-duplicated finding: the first occurrence's id and text, plus every contributing reviewer."""

    finding_id: str
    finding: ReviewFinding
    recommendation: str
    contributors: list[int] = field(default_factory=list)


def _role_rows(details: Sequence[ReviewerDetails]) -> list[str]:
    """One row per role any reviewer activated: ``CHANGES`` if any reviewer said so, the first non-empty review."""
    merged: dict[str, RoleReview] = {}
    for role in (role for entry in details for role in entry.roles):
        seen = merged.setdefault(role.role.casefold(), role)
        verdict = "CHANGES" if "CHANGES" in (seen.verdict, role.verdict) else "PASS"
        merged[role.role.casefold()] = RoleReview(seen.role, verdict, seen.review or role.review)
    rows = [
        f"| {_cell(r.role, MAX_ROLE_CHARS)} | {r.verdict} | {_cell(r.review, MAX_REVIEW_CHARS)} |"
        for r in merged.values()
    ]
    return rows or [NO_ROLES_ROW]


def _dedup_key(finding: ReviewFinding) -> tuple[str, str, Any]:
    """``(severity, path, line)`` when the line is set, else ``(severity, path, summary)``."""
    path = posixpath.normpath(finding.path.strip().replace("\\", "/"))
    if finding.line is not None:
        return finding.severity, path, finding.line
    return finding.severity, path, " ".join(finding.summary.split()).casefold()


def _finding_rows(completed: Sequence[tuple[ReviewerResult, ReviewerDetails]], iteration: int) -> list[_FindingRow]:
    """Findings in reviewer and report order, merged on :func:`_dedup_key`; the first occurrence keeps its id."""
    rows: dict[tuple[str, str, Any], _FindingRow] = {}
    for result, details in completed:
        for n, finding in enumerate(result.findings, start=1):
            recommendation = details.recommendations[n - 1] if n <= len(details.recommendations) else ""
            row = rows.setdefault(_dedup_key(finding), _FindingRow(finding_id(result.index, iteration, n), finding, ""))
            row.recommendation = row.recommendation or recommendation
            if result.index not in row.contributors:
                row.contributors.append(result.index)
    return list(rows.values())


def _finding_line(row: _FindingRow) -> str:
    """One findings-table row; several contributors are named at the end of the Finding cell."""
    item = row.finding
    location = _cell(item.path, MAX_PATH_CHARS) + (f":{item.line}" if item.line is not None else "")
    summary = _cell(item.summary, MAX_SUMMARY_CHARS)
    if len(row.contributors) > 1:
        summary += " (raised by " + ", ".join(f"Reviewer {index}" for index in row.contributors) + ")"
    recommendation = _cell(row.recommendation, MAX_SUMMARY_CHARS) or NOT_SUPPLIED
    severity = _SEVERITY_LABELS.get(item.severity, _cell(item.severity, 20))
    return (
        f"| {row.finding_id} | {severity} | `{location}` | Reviewer {row.contributors[0]} | {summary} | "
        f"{recommendation} |"
    )


def _findings_section(rows: Sequence[_FindingRow]) -> list[str]:
    """The findings table, or one line saying no reviewer reported a finding."""
    if not rows:
        return ["No reviewer reported a finding."]
    header = [
        "| ID | Severity | File:Line | Reviewer | Finding | Recommendation |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    return header + [_finding_line(row) for row in rows]


def _verified_section(details: Sequence[ReviewerDetails]) -> list[str]:
    """``**Verified:**`` bullets from evidenced entries only, each claim once."""
    claims = {(item.claim, item.evidence): None for entry in details for item in entry.verified}
    bullets = [
        f"- {_inline(claim, MAX_CLAIM_CHARS)} (evidence: `{_inline(path, MAX_PATH_CHARS)}`)" for claim, path in claims
    ]
    return ["**Verified:**", "", *(bullets or [NO_VERIFIED_BULLET])]


# --- Heading, paragraph and breakdown ------------------------------------------------------------------------------


def _heading(approved: bool, completed: int, iteration: int) -> str:
    """The ✅ heading with ``(n/n)`` completed reviewers, or the ❌ heading with the iteration."""
    if approved:
        return f"## ✅ Ensemble Consensus Review — APPROVED ({completed}/{completed})"
    return f"## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration {iteration})"


def _evaluation_paragraph(subject: str, commit: str, base: str, rows: Sequence[_FindingRow], probes: str) -> str:
    """One paragraph naming the change, head commit, base branch, finding counts, CI state and mergeability."""
    tally = {name: sum(1 for row in rows if row.finding.severity == name) for name in _SEVERITY_LABELS}
    return (
        f"Evaluated {subject} at head commit `{commit}` against base branch `{base}`: {tally['critical']} Critical, "
        f"{tally['important']} Important and {tally['nit']} Nit findings; {probes}."
    )


def render_reviewer_line(result: ReviewerResult) -> str:
    """One breakdown bullet: reviewer id and verdict with counts, or ``FAILED`` with the redacted reason."""
    head = f"- **Reviewer {result.index}** (`{_inline(result.agent, 80)}/{_inline(result.model, 80)}`)"
    if result.status is not ReviewerStatus.COMPLETED:
        return f"{head}: `FAILED` ({_inline(result.failure_reason or 'unknown failure', MAX_REASON_CHARS)})"
    counts = result.counts
    return f"{head}: `{result.verdict}` (Critical: {counts.critical}, Important: {counts.important}, Nit: {counts.nit})"


def _distinct(values: Sequence[str], fallback: Any) -> str:
    """Distinct values in order, comma-separated, else the bounded fallback."""
    unique = list(dict.fromkeys(_inline(value, 80) for value in values if value))
    return ", ".join(unique) or _inline(fallback, 80) or "unknown"


def _breakdown(reviewers: Sequence[ReviewerResult], verdict: str, consensus_data: Mapping[str, Any]) -> list[str]:
    """The breakdown footer: one bullet per reviewer, the consensus verdict and the reviewed-by line."""
    present = {result.index for result in reviewers}
    configured = _count(consensus_data.get("ensemble_size"))
    missing = [
        f"- **Reviewer {i}**: `FAILED` (no result returned)" for i in range(1, configured + 1) if i not in present
    ]
    agents = _distinct([result.agent for result in reviewers], consensus_data.get("agent"))
    models = _distinct([result.model for result in reviewers], consensus_data.get("model"))
    return [
        "### 🗳️ Ensemble Review Breakdown",
        "",
        *(render_reviewer_line(result) for result in reviewers),
        *missing,
        f"- **Ensemble Consensus Verdict**: `{_inline(verdict, 40) or 'not decided'}`",
        "",
        f"> 🤖 **Reviewed by**: `{agents}` · **Model**: `{models}`",
    ]


def build_review_consensus_report(
    repo_dir: str,
    target: str,
    consensus_data: Mapping[str, Any],
    reviewers: Sequence[ReviewerResult] = (),
    title: str | None = None,
) -> str:
    """Render the posted consensus review from real reviewer results, their details blocks and the probe data.

    Args:
        repo_dir: Repository the review ran in (for the PR link and the commit fallback).
        target: PR number, PR URL or branch under review.
        consensus_data: Consensus diagnostics from ``run_review_loop()`` (verdict, CI, mergeability, iteration).
        reviewers: The reviewer results of the deciding iteration, in order.
        title: The resolved report title; when omitted, the intent title or the target is used.

    Returns:
        The markdown body, in the order of the template's ``## Posted review shape``.
    """
    clean_target = str(target or "").strip()
    verdict = str(consensus_data.get("ensemble_verdict") or "")
    approved = verdict == ConsensusVerdict.APPROVED
    iteration = _count(consensus_data.get("iterations")) or 1
    completed = [(r, parse_reviewer_details(r.report_text)) for r in reviewers if r.status is ReviewerStatus.COMPLETED]
    details = [entry for _, entry in completed]
    rows = _finding_rows(completed, iteration)
    merge = _merge_state(consensus_data.get("mergeability") or {})
    ci_state = _ci_state(consensus_data.get("ci_checks") or {})
    report_title = title or resolve_report_title(consensus_data.get("intent_data"), None, clean_target)
    subject = f"{report_title} ({_pr_link(repo_dir, clean_target, consensus_data)})"
    base = _inline(consensus_data.get("base_branch") or "main", MAX_PATH_CHARS)
    commit = _target_commit(repo_dir, consensus_data)
    return "\n".join(
        [
            _heading(approved, len(completed), iteration),
            "",
            _evaluation_paragraph(subject, commit, base, rows, f"CI {ci_state}; mergeability {merge}"),
            "",
            "**Role reviews:**",
            "",
            "| Role (activated) | Verdict | Review |",
            "| --- | --- | --- |",
            *_role_rows(details),
            "",
            *(_verified_section(details) if approved else _findings_section(rows)),
            "",
            _checks_line(merge, ci_state, consensus_data.get("mergeability_note")),
            "",
            *_breakdown(reviewers, verdict, consensus_data),
            "",
            CLOSING_NOTICE,
        ]
    )


build_pr_review_consensus_report = build_review_consensus_report
