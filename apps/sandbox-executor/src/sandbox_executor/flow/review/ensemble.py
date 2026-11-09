"""Dispatch of the Stage 4 reviewer ensemble for one review iteration."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from collections.abc import Callable

from sandbox_executor.agent_defaults import FALLBACK_AGENT
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.dispatch import _dispatch_role
from sandbox_executor.flow.git_refs import _resolve_branch_ref
from sandbox_executor.flow.github.pr import generate_semantic_pr_metadata
from sandbox_executor.flow.review.records import _attach_transported_detail, render_reviewer_summary_line
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.review_ensemble import ConsensusVerdict, ReviewerResult, build_review_request
from sandbox_executor.review_target import ReviewTarget, ReviewTargetError, resolve_review_target

logger = logging.getLogger(__name__)


def _resolve_reviewed_target(repo_dir: str, review_target: str) -> tuple[ReviewTarget | None, str | None]:
    """Resolve the target once before the loop; ``(None, error)`` fails the review closed."""
    try:
        return resolve_review_target(repo_dir, review_target), None
    except (ReviewTargetError, RuntimeError, TimeoutError, OSError) as exc:
        return None, f"Could not resolve the review target '{review_target}': {redact_agent_secrets(str(exc))}"


def _semantic_pr_text(context: FlowContext) -> tuple[str, str]:
    """The PR title and body generated from the intent and plan; empty strings if generation fails."""
    plan_res = context.stage_results.get("plan")
    plan_meta = plan_res.payload if plan_res and isinstance(plan_res.payload, dict) else None
    try:
        return generate_semantic_pr_metadata(context.intent_data, plan_metadata=plan_meta)
    except Exception as exc:
        logger.debug("Non-fatal PR metadata generation in review loop: %s", exc)
        return "", ""


def _review_base_ref(repo_dir: str, target: ReviewTarget, target_branch: str) -> str:
    """The ref the reviewed diff starts from: the PR's base branch, else the intent's target branch (origin first)."""
    base = target.base_branch or target_branch
    return _resolve_branch_ref(repo_dir, base, prefer_remote=True) or base


def _ensemble_iteration(  # noqa: PLR0913
    context: FlowContext,
    target: ReviewTarget,
    target_branch: str,
    iteration: int,
    pr_text: tuple[str, str],
    ensemble_fn: Callable[..., list[ReviewerResult]],
    ensemble_size: int,
) -> tuple[list[ReviewerResult], str | None]:
    """Build the request from the real diff and run the ensemble; ``([], error)`` on an infrastructure failure."""
    base_ref = _review_base_ref(context.target_worktree, target, target_branch)
    try:
        request = build_review_request(target, base_ref, iteration, pr_text[0], pr_text[1], context.target_worktree)
    except (RuntimeError, OSError) as exc:
        return [], f"Could not build the review diff for '{target.head_branch}': {redact_agent_secrets(str(exc))}"
    try:
        results = ensemble_fn(request, agent=context.agent, model=context.model, size=ensemble_size)
    except Exception as exc:
        return [], f"The reviewer ensemble could not run: {redact_agent_secrets(str(exc))}"
    for result in results:
        context.log(f"[Review Loop] {render_reviewer_summary_line(result)}")
    return list(results), None


# The engine owns the Stage 4 loop: each dispatch asks the container for one ensemble pass, labelled by --iteration.
_ENGINE_PASSES_PER_DISPATCH = 1


def _dispatch_review_ensemble(
    context: FlowContext,
    target: ReviewTarget,
    iteration: int,
    ensemble_size: int,
) -> tuple[list[ReviewerResult], ConsensusVerdict | None, str | None]:
    """Dispatch one reviewer ensemble pass into the review-loop role container via _dispatch_role.

    ``iteration`` is the engine's pass label (``--iteration``), never the container's loop limit.
    """
    from sandbox_executor.review_ensemble import deserialize_review_outcome

    tmp_dir = os.path.expanduser("~/.holon/tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    fd, result_file = tempfile.mkstemp(prefix="holon-review-result-", suffix=".json", dir=tmp_dir)
    try:
        os.fchmod(fd, 0o666)
    finally:
        os.close(fd)

    target_ref = target.raw or target.head_branch or "HEAD"
    container_args = [
        target_ref,
        context.agent or FALLBACK_AGENT,
        context.model or "inherit",
        f"--max-iterations={_ENGINE_PASSES_PER_DISPATCH}",
        f"--iteration={iteration}",
        f"--ensemble-size={ensemble_size}",
        "--no-auto-pr",
        "--skip-post",
        "--result-file=/tmp/review_result.json",
    ]
    if context.skip_push:
        container_args.append("--skip-push")

    try:
        exit_code = _dispatch_role(context, "review-loop", container_args, result_file=result_file)
        if not os.path.exists(result_file) or os.path.getsize(result_file) == 0:
            err = f"Reviewer ensemble container dispatch exited with code {exit_code} without writing results"
            return [], None, err

        with open(result_file, encoding="utf-8") as f:
            data = json.load(f)

        reviewers, verdict, outcome_err = deserialize_review_outcome(data)
        reviewers = _attach_transported_detail(reviewers, data.get("reviewers"))
        if exit_code != 0 and not reviewers:
            err = outcome_err or f"Reviewer ensemble container dispatch exited with code {exit_code}"
            return [], None, err

        for r in reviewers:
            context.log(f"[Review Loop] {render_reviewer_summary_line(r)}")

        return reviewers, verdict, outcome_err
    except Exception as exc:
        return [], None, f"Reviewer ensemble container dispatch failed: {redact_agent_secrets(str(exc))}"
    finally:
        with contextlib.suppress(OSError):
            os.remove(result_file)
