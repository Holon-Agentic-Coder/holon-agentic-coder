"""Review command module for running native review loop on execution branch or change branch."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from sandbox_executor import cli, git_flow
from sandbox_executor.cli.commands._worktree_guard import DEFAULT_BRANCH_GUARD_HELP, default_branch_worktree_allowed
from sandbox_executor.cli.options import (
    _AGENT_HELP,
    _MODEL_HELP,
    _TOKEN_REDUCE_HELP,
    add_target_worktree_option,
)
from sandbox_executor.review_ensemble import ENSEMBLE_SIZE_HELP
from sandbox_executor.review_target import is_pr_reference, resolve_review_target

ORDER = 70


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the review-loop subcommand parser."""
    parser = subparsers.add_parser(
        "review-loop",
        aliases=["review", "pr-review-loop"],
        help=(
            "Run autonomous native review loop on an execution branch or change branch. "
            "Fails closed: exits 1 unless the consensus is approved and posted."
        ),
    )
    parser.add_argument(
        "target",
        help="Target execution branch name, PR number, or PR URL.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=25,
        help="Maximum number of review loop iterations (default: 25).",
    )
    parser.add_argument(
        "--ensemble-size",
        type=int,
        default=3,
        help=ENSEMBLE_SIZE_HELP,
    )
    parser.add_argument(
        "--auto-pr",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Automatically create PR if only a branch exists (default: True). "
            "The empty-diff guard runs whatever this is."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run review evaluation without applying changes or git commits.",
    )
    add_target_worktree_option(parser, default=".", help=DEFAULT_BRANCH_GUARD_HELP)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output review loop results in JSON format.",
    )
    parser.add_argument(
        "--token-reduce",
        action="store_true",
        help=_TOKEN_REDUCE_HELP,
    )
    parser.add_argument("--agent", default=None, help=_AGENT_HELP)
    parser.add_argument("--model", default=None, help=_MODEL_HELP)
    parser.add_argument(
        "--local",
        action="store_true",
        help=(
            "Run the review loop on the host rather than inside the Docker container; reviewer agent turns are "
            "refused outside a sandbox, so only --dry-run is useful here."
        ),
    )
    parser.add_argument(
        "--skip-push",
        action="store_true",
        help="Skip pushing git commits or branches to remote.",
    )
    parser.add_argument(
        "--mitm-web",
        action="store_true",
        help=(
            "Launch mitmweb dashboard on port 8081 (or HOLON_MITM_WEB_PORT) for real-time traffic inspection. "
            "For shared staging environments, pass HOLON_MITM_WEB_PASSWORD to restrict access."
        ),
    )
    parser.set_defaults(_run=run)


def _validate_review_args(args: argparse.Namespace) -> bool:
    if getattr(args, "max_iterations", 25) < 1:
        print("Error: --max-iterations must be at least 1.", file=sys.stderr)
        sys.exit(1)
        return False
    if getattr(args, "ensemble_size", 3) < 1:
        print("Error: --ensemble-size must be at least 1.", file=sys.stderr)
        sys.exit(1)
        return False
    return True


def _run_local_review(args: argparse.Namespace, effective_token_reduce: bool) -> None:
    from sandbox_executor.flow import run_review_loop_cli

    exit_code = run_review_loop_cli(args, token_reduce=effective_token_reduce)
    sys.exit(exit_code)


def _build_container_review_args(args: argparse.Namespace) -> list[str]:
    container_args = [
        args.target,
        args.agent,
        args.model,
        f"--max-iterations={args.max_iterations}",
        f"--ensemble-size={args.ensemble_size}",
    ]
    if not getattr(args, "auto_pr", True):
        container_args.append("--no-auto-pr")
    if getattr(args, "skip_push", False):
        container_args.append("--skip-push")
    if getattr(args, "json", False):
        container_args.append("--json")
    return container_args


def _container_review_worktree(args: argparse.Namespace) -> str:
    """Return the host worktree the review-loop container reviews in place.

    A PR target is resolved to its head branch on the host first; the worktree that has that branch checked out is
    reused (normally the run's flow-<slug>), and a pr<N>-<slug> worktree is provisioned only when none exists.
    """
    worktree = os.path.abspath(getattr(args, "target_worktree", None) or ".")
    target = str(args.target).strip()
    if is_pr_reference(target):
        resolved = resolve_review_target(worktree, target)
        return git_flow.resolve_review_worktree(worktree, resolved.head_branch, resolved.pr_number)
    return git_flow.resolve_review_worktree(worktree, target)


def _run_container_review(args: argparse.Namespace, effective_token_reduce: bool) -> None:
    agent_id = cli.normalise_agent_id(args.agent)
    image_name = cli.image_for_agent(args.agent)
    container_args = _build_container_review_args(args)
    kwargs: dict[str, Any] = {
        "agent_id": agent_id,
        "token_reduce": effective_token_reduce,
        "target_worktree": _container_review_worktree(args),
    }
    if getattr(args, "mitm_web", False):
        kwargs["mitm_web"] = True
    sys.exit(cli.run_docker_container("review-loop", image_name, container_args, **kwargs))


def run(args: argparse.Namespace) -> int | None:
    """Execute the review-loop command."""
    try:
        if not _validate_review_args(args):
            return None
        command = f"holon {getattr(args, 'command', None) or 'review-loop'}"
        if not default_branch_worktree_allowed(getattr(args, "target_worktree", None), command):
            sys.exit(1)
            return None

        effective_token_reduce = cli.detect_holon_coherence(
            bool(getattr(args, "token_reduce", False) or getattr(args, "mitm_web", False))
        )

        is_local = (
            getattr(args, "local", False)
            or getattr(args, "dry_run", False)
            or getattr(args, "json", False)
            or bool(os.getenv("HOLON_ROLE"))
            or bool(os.getenv("HOLON_IN_SANDBOX"))
        )
        agent_res, model_res = cli.resolve_runner_and_model(getattr(args, "agent", None), getattr(args, "model", None))
        args.agent, args.model = agent_res.value, model_res.value

        if is_local:
            _run_local_review(args, effective_token_reduce)
        else:
            _run_container_review(args, effective_token_reduce)
    except Exception as e:
        print(f"Error during review-loop execution: {cli.redact_cli_error(e)}", file=sys.stderr)
        sys.exit(1)
    return None
