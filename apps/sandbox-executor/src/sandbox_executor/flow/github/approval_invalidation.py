from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.ci_failure_detail import STEP_LOOKUP_TIMEOUT_SECONDS, describe_failure, short_sha
from sandbox_executor.knowledge import CredentialRedactor

logger = logging.getLogger(__name__)

MARKER_PREFIX = "<!-- holon-approval-invalidated:"
MAX_COMMENT_CHARS = 4000
UPDATE_COMMENT_MUTATION = (
    "mutation($id: ID!, $body: String!) { updateIssueComment(input: {id: $id, body: $body}) { issueComment { id } } }"
)

_INVALIDATION_HEADING = "## ⚠️ Ensemble Consensus Approval no longer stands"
_RERUN_FAILED_AGAIN_NOTICE = "The failed job was automatically rerun once and failed again."
_HUMAN_DECISION_FOOTER = (
    "A job that exceeds its time limit is a failed check. This pull request is not verified as ready to merge; "
    "a human decides."
)


@dataclass(frozen=True)
class CommentRecord:
    id: str
    body: str


CommentLister = Callable[[str], "list[str] | None"]
CommentRecordLister = Callable[[str], "list[CommentRecord] | None"]
CommentPoster = Callable[[str, str], bool]
CommentEditor = Callable[[str, str], bool]


def invalidation_marker(failing_head: str) -> str:
    return f"{MARKER_PREFIX}{failing_head} -->"


def _check_line(detail: Mapping[str, Any]) -> str:
    steps = ", ".join(f"`{s}`" for s in detail.get("failed_steps") or [])
    return f"- {describe_failure(detail)}" + (f"; failed step: {steps}" if steps else "")


def _rerun_notice_lines(timing_rerun: Mapping[str, Any] | None) -> list[str]:
    return [_RERUN_FAILED_AGAIN_NOTICE, ""] if timing_rerun and timing_rerun.get("attempted") else []


def render_invalidation(
    approved_head: str | None,
    failing_head: str,
    details: list[Mapping[str, Any]],
    timing_rerun: Mapping[str, Any] | None = None,
) -> str:
    covered = f"`{short_sha(approved_head)}`" if approved_head else "an earlier commit"
    marker = invalidation_marker(failing_head)
    lines = [
        _INVALIDATION_HEADING,
        "",
        f"The consensus approval covered {covered}. CI is now failing on head `{short_sha(failing_head)}`:",
        "",
        *(_check_line(d) for d in details),
        "",
        *_rerun_notice_lines(timing_rerun),
        _HUMAN_DECISION_FOOTER,
    ]
    text = CredentialRedactor.redact_text("\n".join(lines))[: MAX_COMMENT_CHARS - len(marker) - 2]
    return f"{text}\n\n{marker}"


def _gh_env(proxy_env: Mapping[str, str] | None) -> dict[str, str]:
    return {**os.environ, **(proxy_env or {}), "DOCKER_CLI_HINTS": "false"}


def _run_gh(argv: list[str], repo_dir: str, env: Mapping[str, str]) -> subprocess.CompletedProcess[str] | None:
    try:
        timeout = STEP_LOOKUP_TIMEOUT_SECONDS
        return subprocess.run(argv, cwd=repo_dir, capture_output=True, text=True, check=False, timeout=timeout, env=env)
    except Exception:
        return None


def gh_comment_records(repo_dir: str, proxy_env: Mapping[str, str] | None = None) -> CommentRecordLister:
    env = _gh_env(proxy_env)

    def records(pr_number: str) -> list[CommentRecord] | None:
        proc = _run_gh(["gh", "pr", "view", pr_number, "--json", "comments"], repo_dir, env)
        if proc is None or proc.returncode != 0:
            return None
        try:
            comments = json.loads(proc.stdout).get("comments") or []
            return [CommentRecord(str(c.get("id") or ""), str(c.get("body") or "")) for c in comments]
        except Exception:
            return None

    return records


def gh_comment_lister(repo_dir: str, proxy_env: Mapping[str, str] | None = None) -> CommentLister:
    records = gh_comment_records(repo_dir, proxy_env)

    def lister(pr_number: str) -> list[str] | None:
        found = records(pr_number)
        return None if found is None else [record.body for record in found]

    return lister


def gh_comment_poster(repo_dir: str, proxy_env: Mapping[str, str] | None = None) -> CommentPoster:
    env = _gh_env(proxy_env)

    def poster(pr_number: str, body: str) -> bool:
        proc = _run_gh(["gh", "pr", "comment", pr_number, "--body", body], repo_dir, env)
        return proc is not None and proc.returncode == 0

    return poster


def gh_comment_editor(repo_dir: str, proxy_env: Mapping[str, str] | None = None) -> CommentEditor:
    env = _gh_env(proxy_env)

    def editor(comment_id: str, body: str) -> bool:
        if not comment_id:
            return False
        redacted = CredentialRedactor.redact_text(body)
        fields = [f"query={UPDATE_COMMENT_MUTATION}", f"id={comment_id}", f"body={redacted}"]
        proc = _run_gh(["gh", "api", "graphql", *(arg for field in fields for arg in ("-f", field))], repo_dir, env)
        return proc is not None and proc.returncode == 0

    return editor


def approval_pr_number(context: FlowContext) -> str | None:
    review = context.stage_results.get("review")
    payload = review.payload if review else {}
    consensus, posting = payload.get("consensus") or {}, payload.get("posting") or {}
    number = consensus.get("pr_number") or (payload.get("pr") or {}).get("pr_number")
    posted = posting.get("dual_posted") or (posting.get("official_review") or {}).get("success")
    if not (consensus.get("approved") and posted and number):
        return None
    return str(number)


def invalidate_approval(
    context: FlowContext,
    reverified: Mapping[str, Any],
    lister: CommentLister | None = None,
    poster: CommentPoster | None = None,
) -> dict[str, Any]:
    failing = str(reverified.get("final_head") or "")
    pr_number = approval_pr_number(context)
    if context.dry_run or context.skip_push:
        return {"posted": False, "reason": "dry run or push skipped"}
    if pr_number is None or not failing:
        return {"posted": False, "reason": "no approval was posted, or the failing head is unknown"}
    repo_dir = context.target_worktree
    existing = (lister or gh_comment_lister(repo_dir))(pr_number)
    if existing is None:
        logger.warning("Approval invalidation skipped: the comments of PR %s could not be listed.", pr_number)
        return {"posted": False, "reason": "comments could not be listed"}
    if any(invalidation_marker(failing) in body for body in existing):
        return {"posted": False, "reason": "already posted for this head"}
    body = render_invalidation(
        reverified.get("approved_head"),
        failing,
        list(reverified.get("failed_details") or []),
        reverified.get("timing_rerun"),
    )
    posted = (poster or gh_comment_poster(repo_dir))(pr_number, body)
    return {"posted": posted, "reason": "posted" if posted else "the comment could not be posted"}
