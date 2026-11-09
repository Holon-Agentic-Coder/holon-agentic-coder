"""The 'holon review-loop' CLI entry and the review loop summary helpers it shares with the entrypoint."""

from __future__ import annotations

import json
from typing import Any

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.review.loop import run_review_loop
from sandbox_executor.flow.review.records import _bounded_error_context, render_reviewer_summary_line
from sandbox_executor.review_ensemble import ConsensusVerdict


def review_loop_approved(loop_res: dict[str, Any]) -> bool:
    """True only when the ensemble verdict is APPROVED and the loop's consensus is approved (exit code 0)."""
    return loop_res.get("ensemble_verdict") == str(ConsensusVerdict.APPROVED) and bool(
        (loop_res.get("consensus") or {}).get("approved")
    )


def review_summary_lines(loop_res: dict[str, Any]) -> list[str]:
    """Summary lines with the real per-reviewer verdicts of the deciding iteration."""
    lines = [f"  Ensemble       : {loop_res.get('ensemble_verdict') or 'NOT RUN'}"]
    lines.extend(f"    {render_reviewer_summary_line(r)}" for r in loop_res.get("reviewers") or [])
    review_data = loop_res.get("review_data") or {}
    roles = (
        ", ".join(f"{r.get('role')} ({r.get('verdict')})" for r in review_data.get("roles") or []) or "none reported"
    )
    lines.append(f"  Roles          : {roles}; open findings: {len(review_data.get('findings') or [])}")
    return lines


def run_review_loop_cli(args: Any, token_reduce: bool = False) -> int:
    """CLI dispatcher for 'holon review-loop' (and aliases 'review', 'pr-review-loop').

    Args:
        args: Parsed CLI namespace containing target, max_iterations, ensemble_size, etc.
        token_reduce: Whether token reduction proxy is enabled.

    Returns:
        int: Process exit code: 0 only when the ensemble verdict is APPROVED and the consensus was approved and
        posted (human halt), otherwise 1.
    """
    target_worktree = getattr(args, "target_worktree", None) or getattr(args, "repo_dir", ".")
    target = getattr(args, "target", "")
    max_iterations = getattr(args, "max_iterations", 25)
    ensemble_size = getattr(args, "ensemble_size", 3)
    auto_pr = getattr(args, "auto_pr", True)
    dry_run = getattr(args, "dry_run", False)
    json_output = getattr(args, "json", False)

    context_kwargs: dict[str, Any] = {}
    # main() resolves --agent/--model before dispatch; keep the FlowContext defaults for other callers.
    if getattr(args, "agent", None):
        context_kwargs["agent"] = args.agent
    if getattr(args, "model", None):
        context_kwargs["model"] = args.model
    context = FlowContext(
        target_worktree=target_worktree,
        execution_branch=target,
        dry_run=dry_run,
        max_review_iterations=max_iterations,
        token_reduce=token_reduce,
        **context_kwargs,
    )

    loop_res = run_review_loop(
        context=context,
        target=target,
        max_iterations=max_iterations,
        ensemble_size=ensemble_size,
        auto_pr=auto_pr,
        dry_run=dry_run,
        skip_post=getattr(args, "skip_post", False),
    )

    consensus = loop_res.get("consensus", {})
    approved = review_loop_approved(loop_res)

    if json_output:
        print(json.dumps(loop_res, indent=2))
    else:
        print("\n================== Holon Review Loop Summary ==================")
        print(f"  Target         : {target}")
        print(f"  Iterations     : {consensus.get('iterations', 1)} / {loop_res.get('max_iterations', max_iterations)}")
        ci = loop_res.get("ci_checks", {})
        print(
            f"  CI Checks      : {ci.get('total', 0)} total, {ci.get('passed', 0)} passed, "
            f"{ci.get('failed', 0)} failed, {ci.get('pending', 0)} pending ({ci.get('source', 'unknown')})"
        )
        for line in review_summary_lines(loop_res):
            print(line)
        print(f"  Consensus      : {consensus.get('status', 'unknown').upper()}")
        print("===============================================================\n")
        if approved:
            print("[SAFETY HALT] Review Loop reached consensus approval.")
            print("Pipeline halted for human review (autonomous merge prohibited).")
            cmd = loop_res.get("human_approval_command", f"holon review approve {target}")
            print(f"To approve manually, run: {cmd}\n")
        else:
            print(f"Review Loop did not reach consensus approval (status={consensus.get('status')}).")
            if loop_res.get("error"):
                print(f"Reason: {_bounded_error_context(str(loop_res['error']))}")
            print()

    return 0 if approved else 1
