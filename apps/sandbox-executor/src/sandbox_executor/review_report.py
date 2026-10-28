"""The Stage 4 PR review consensus report, rendered only from real reviewer results.

Every line of the report comes from data the review actually produced: the :class:`ReviewerResult` objects returned
by :func:`~sandbox_executor.review_ensemble.run_ensemble`, the ensemble verdict, the mergeability and CI probes, and the
resolved target. Nothing is derived from a constant: there are no generated reviewer ids, no votes copied from the CI
verdict, no persona matrix and no boiler-plate findings. The title is the intent title, else the PR title, else the PR
number or branch name.

Reviewer text (finding files and summaries) is untrusted: it is redacted, collapsed to one line, stripped of
backticks and angle brackets and bounded before it reaches the posted comment. Failure reasons are already redacted
and bounded by :meth:`ReviewerResult.failed` and are redacted once more here as defence in depth.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from typing import Any

from sandbox_executor.git import run_git
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.review_ensemble import MAX_REASON_CHARS, ReviewerResult, ReviewerStatus
from sandbox_executor.review_target import ReviewTarget

__all__ = [
    "build_pr_review_consensus_report",
    "render_finding_lines",
    "render_reviewer_line",
    "resolve_report_title",
]

MAX_SUMMARY_CHARS = 300
MAX_PATH_CHARS = 200
HUMAN_MERGE_NOTICE = (
    "> **Bean 0034 Notice:** Under Bean 0034 (Human-Only PR Merging), autonomous merging to `main` is strictly "
    "prohibited. Pipeline halts for manual human approval."
)
_GITHUB_SLUG = re.compile(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$")
_PR_URL_NUMBER = re.compile(r"/pull/(\d+)")


def _inline(text: Any, limit: int) -> str:
    """Make untrusted reviewer text safe for one markdown line: redacted, single-line, no code or HTML, bounded."""
    clean = " ".join(redact_agent_secrets(str(text or "")).split())
    clean = clean.replace("`", "'").replace("<", "&lt;").replace(">", "&gt;")
    return clean if len(clean) <= limit else clean[: limit - 3] + "..."


def resolve_report_title(intent_data: Mapping[str, Any] | None, target: ReviewTarget | None, raw_target: str) -> str:
    """Return the intent title, else the PR title, else ``PR #<n>`` or the branch name; never a fixed phrase."""
    for candidate in ((intent_data or {}).get("title"), target.title if target else None):
        if str(candidate or "").strip():
            return _inline(candidate, MAX_SUMMARY_CHARS)
    if target and target.pr_number is not None:
        return f"PR #{target.pr_number}"
    branch = (target.head_branch if target else "") or str(raw_target or "").strip()
    return _inline(branch, MAX_PATH_CHARS) or "unknown target"


def render_reviewer_line(result: ReviewerResult) -> str:
    """One line per reviewer: index, agent, model, status, and verdict with counts or the redacted failure reason."""
    head = f"- **Reviewer {result.index}**: agent `{_inline(result.agent, 80)}`, model `{_inline(result.model, 80)}`"
    if result.status is not ReviewerStatus.COMPLETED:
        return f"{head}: `FAILED` ({_inline(result.failure_reason or 'unknown failure', MAX_REASON_CHARS)})"
    counts = result.counts
    return (
        f"{head}: `{result.status}`, verdict `{result.verdict}` "
        f"(critical: {counts.critical}, important: {counts.important}, nit: {counts.nit})"
    )


def render_finding_lines(result: ReviewerResult) -> list[str]:
    """One line per finding a reviewer reported: severity, file, line and the bounded summary."""
    lines: list[str] = []
    for finding in result.findings:
        location = _inline(finding.path, MAX_PATH_CHARS)
        if finding.line is not None:
            location = f"{location}:{finding.line}"
        summary = _inline(finding.summary, MAX_SUMMARY_CHARS)
        lines.append(f"- `{finding.severity}` `{location}` (reviewer {result.index}): {summary}")
    return lines


def _ci_line(ci_status: Mapping[str, Any]) -> str:
    """The CI line from the probe data; an unmeasured or empty probe is reported as such, never as a pass."""
    if ci_status.get("measured") is False:
        return "- **CI:** not measured"
    total = int(ci_status.get("total") or 0)
    source = _inline(ci_status.get("source") or "unknown", 40)
    if total == 0 and not ci_status.get("checks"):
        return f"- **CI:** no checks reported ({source})"
    return (
        f"- **CI:** {ci_status.get('passed', 0)}/{total} passed, {ci_status.get('failed', 0)} failing, "
        f"{ci_status.get('pending', 0)} pending ({source})"
    )


def _mergeability_line(consensus_data: Mapping[str, Any]) -> str:
    """The mergeability line from the probe data and the loop's note."""
    note = str(consensus_data.get("mergeability_note") or "").strip()
    if note:
        return f"- **Mergeability:** {_inline(note, MAX_REASON_CHARS)}"
    mergeability = consensus_data.get("mergeability") or {}
    if mergeability.get("is_dirty"):
        return "- **Mergeability:** conflicts with the base branch"
    state = str(mergeability.get("merge_state_status") or "unknown")
    return f"- **Mergeability:** `{_inline(state, 40)}`"


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
    """The GitHub ``owner/name`` from the consensus data or the origin remote."""
    name = str(consensus_data.get("repo_name") or "").strip()
    if name:
        return name
    match = _GITHUB_SLUG.search(_git_output(repo_dir, ["remote", "get-url", "origin"]))
    return match.group(1) if match else "Holon-Agentic-Coder/holon-agentic-coder"


def _pr_link(repo_dir: str, target: str, consensus_data: Mapping[str, Any]) -> str:
    """A markdown link to the PR (or the branch) under review."""
    repo_name = _repo_name(repo_dir, consensus_data)
    pr_number = consensus_data.get("pr_number")
    pr_url = consensus_data.get("pr_url")
    if not pr_url and target.startswith(("http://", "https://")):
        pr_url = target
        match = _PR_URL_NUMBER.search(target)
        pr_number = match.group(1) if match else pr_number
    elif not pr_url and target.isdigit():
        pr_number = target
        pr_url = f"https://github.com/{repo_name}/pull/{target}"
    elif not pr_url and target:
        pr_url = f"https://github.com/{repo_name}/tree/{target}"
    if pr_url and pr_number:
        return f"[{repo_name}#{pr_number}]({pr_url})"
    if pr_url:
        return f"[{target}]({pr_url})"
    return f"`{target or 'unknown'}`"


def _reviewer_section(reviewers: Sequence[ReviewerResult], configured: int) -> list[str]:
    """The per-reviewer lines and their findings."""
    completed = sum(1 for result in reviewers if result.status is ReviewerStatus.COMPLETED)
    lines = ["### Reviewers", "", f"Reviewers: {configured} configured, {completed} completed.", ""]
    lines.extend(render_reviewer_line(result) for result in reviewers)
    findings = [line for result in reviewers for line in render_finding_lines(result)]
    lines.extend(["", "### Findings", ""])
    lines.extend(findings or ["No findings were reported."])
    return lines


def build_pr_review_consensus_report(
    repo_dir: str,
    target: str,
    consensus_data: Mapping[str, Any],
    reviewers: Sequence[ReviewerResult] = (),
    title: str | None = None,
) -> str:
    """Render the review consensus report from real reviewer results, probe data and the resolved title.

    Args:
        repo_dir: Repository the review ran in (for the PR link and the commit fallback).
        target: PR number, PR URL or branch under review.
        consensus_data: Consensus diagnostics from ``run_review_loop()`` (verdict, CI, mergeability, iteration).
        reviewers: The reviewer results of the deciding iteration, in order.
        title: The resolved report title; when omitted, the intent title or the target is used.

    Returns:
        The markdown report.
    """
    clean_target = str(target or "").strip()
    verdict = str(consensus_data.get("ensemble_verdict") or consensus_data.get("status") or "unknown").upper()
    configured = int(consensus_data.get("ensemble_size") or len(reviewers))
    report_title = title or resolve_report_title(consensus_data.get("intent_data"), None, clean_target)
    lines = [
        f"## Holon PR Review: {report_title} (iteration {consensus_data.get('iterations', 1)})",
        "",
        f"**Pull Request:** {_pr_link(repo_dir, clean_target, consensus_data)}  ",
        f"**Target Commit:** `{_target_commit(repo_dir, consensus_data)}`  ",
        f"**Base Branch:** `{str(consensus_data.get('base_branch') or 'main').strip()}`  ",
        f"**Verdict:** `{verdict}`",
        "",
        "Approval needs every reviewer to approve with zero findings, plus a mergeable PR and green CI.",
        "",
        *_reviewer_section(reviewers, configured),
        "",
        "### Checks",
        "",
        _mergeability_line(consensus_data),
        _ci_line(consensus_data.get("ci_checks") or {}),
        "",
        "> [!NOTE]",
        HUMAN_MERGE_NOTICE,
    ]
    return "\n".join(lines)
