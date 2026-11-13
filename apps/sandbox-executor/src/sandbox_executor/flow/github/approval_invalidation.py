"""The one follow-up comment that says a consensus approval no longer stands because CI is red on a later head.

The comment is posted once per failing head: a hidden marker holding the failing SHA is checked against the PR's
existing comments first, and a PR whose comments cannot be listed gets no comment. It never approves or merges.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.ci_failure_detail import STEP_LOOKUP_TIMEOUT_SECONDS, describe_failure, short_sha
from sandbox_executor.knowledge import CredentialRedactor

logger = logging.getLogger(__name__)

MARKER_PREFIX = "<!-- holon-approval-invalidated:"
MAX_COMMENT_CHARS = 4000

CommentLister = Callable[[str], "list[str] | None"]
CommentPoster = Callable[[str, str], bool]


def invalidation_marker(failing_head: str) -> str:
    """The hidden marker that keys the comment on the full failing SHA."""
    return f"{MARKER_PREFIX}{failing_head} -->"


def _check_line(detail: Mapping[str, Any]) -> str:
    steps = ", ".join(f"`{s}`" for s in detail.get("failed_steps") or [])
    return f"- {describe_failure(detail)}" + (f"; failed step: {steps}" if steps else "")


def render_invalidation(approved_head: str | None, failing_head: str, details: list[Mapping[str, Any]]) -> str:
    """The comment body: both heads, each failing check and, for a timing gate, its duration against the limit."""
    covered = f"`{short_sha(approved_head)}`" if approved_head else "an earlier commit"
    marker = invalidation_marker(failing_head)
    lines = [
        "## ⚠️ Ensemble Consensus Approval no longer stands",
        "",
        f"The consensus approval covered {covered}. CI is now failing on head `{short_sha(failing_head)}`:",
        "",
        *(_check_line(d) for d in details),
        "",
        "A job that exceeds its time limit is a failed check. This pull request is not verified as ready to merge; "
        "a human decides.",
    ]
    text = CredentialRedactor.redact_text("\n".join(lines))[: MAX_COMMENT_CHARS - len(marker) - 2]
    return f"{text}\n\n{marker}"


def gh_comment_lister(repo_dir: str, proxy_env: Mapping[str, str] | None = None) -> CommentLister:
    """A lister of the comment bodies of a PR through ``gh pr view``; None when they cannot be read."""
    env = {**os.environ, **(proxy_env or {}), "DOCKER_CLI_HINTS": "false"}

    def lister(pr_number: str) -> list[str] | None:
        try:
            proc = subprocess.run(
                ["gh", "pr", "view", pr_number, "--json", "comments"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=STEP_LOOKUP_TIMEOUT_SECONDS,
                env=env,
            )
            if proc.returncode != 0:
                return None
            return [str(c.get("body") or "") for c in json.loads(proc.stdout).get("comments") or []]
        except Exception:
            return None

    return lister


def gh_comment_poster(repo_dir: str, proxy_env: Mapping[str, str] | None = None) -> CommentPoster:
    """A poster of one PR comment through ``gh pr comment``; False when it fails."""
    env = {**os.environ, **(proxy_env or {}), "DOCKER_CLI_HINTS": "false"}

    def poster(pr_number: str, body: str) -> bool:
        try:
            proc = subprocess.run(
                ["gh", "pr", "comment", pr_number, "--body", body],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=STEP_LOOKUP_TIMEOUT_SECONDS,
                env=env,
            )
        except Exception:
            return False
        return proc.returncode == 0

    return poster


def approval_pr_number(context: FlowContext) -> str | None:
    """The PR number of the approval Stage 4 posted, or None when no approval was posted."""
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
    """Post the once-per-head comment for a red head (an unverified head posts none); ``{"posted", "reason"}``."""
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
    body = render_invalidation(reverified.get("approved_head"), failing, list(reverified.get("failed_details") or []))
    posted = (poster or gh_comment_poster(repo_dir))(pr_number, body)
    return {"posted": posted, "reason": "posted" if posted else "the comment could not be posted"}
