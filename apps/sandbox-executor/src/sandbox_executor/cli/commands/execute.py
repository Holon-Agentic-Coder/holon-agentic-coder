"""Execute command module for running Executor to implement a plan branch."""

from __future__ import annotations

import argparse
import os
import sys

from sandbox_executor import cli, git_flow
from sandbox_executor.cli.commands._host_publish import push_checked_out_branch, slug_of_branch
from sandbox_executor.cli.commands._role import prepare_role_execution
from sandbox_executor.cli.options import (
    add_preflight_option,
    add_runner_options,
    add_target_worktree_option,
    add_token_reduction_options,
)
from sandbox_executor.entrypoint.executor import OVER_BUDGET_EXIT_CODE
from sandbox_executor.flow import _read_ledger_rows

ORDER = 30


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the execute subcommand parser."""
    parser = subparsers.add_parser("execute", help="Run Sandbox Executor to execute code changes for a plan.")
    parser.add_argument("plan_branch", help="Target plan branch name")
    add_runner_options(parser)
    add_preflight_option(parser)
    add_token_reduction_options(parser)
    add_target_worktree_option(parser)
    parser.set_defaults(_run=run)


def execution_worktree(plan_branch: str, target_worktree: str | None) -> str:
    """Return the host worktree the executor container works in, with the plan branch fetched into it on the host.

    A worktree with the default branch checked out only selects the repository: the plan's flow-<slug> sibling is
    provisioned off it (or reused), so the container never clones or fetches to prepare its checkout.

    Raises:
        RuntimeError: If the flow worktree cannot be provisioned or the plan branch cannot be fetched.
    """
    worktree = target_worktree or os.getcwd()
    # Worktrees share remote-tracking refs, so fetching here also serves a flow worktree provisioned below.
    git_flow.fetch_origin_branch(worktree, plan_branch)
    git_flow.fetch_origin_branch(worktree, _target_branch(worktree, plan_branch))
    if git_flow.is_default_branch_worktree(worktree, git_flow.repository_default_branch(worktree)):
        worktree = git_flow.ensure_flow_worktree_off_default(worktree, _intent_slug(worktree, plan_branch), None)
    return worktree


def _intent_row(worktree: str, plan_branch: str) -> dict:
    """The plan's intent ledger row, read from the fetched plan branch (empty when it has none)."""
    intent = plan_branch.split("/", 1)[0]
    rows = _read_ledger_rows(worktree, f"origin/{plan_branch}", "intents.jsonl")
    return next((row for row in reversed(rows) if str(row.get("branch") or "").removesuffix("/_") == intent), {})


def _intent_slug(worktree: str, plan_branch: str) -> str:
    """The slug holon flow named the plan's flow-<slug> worktree after: the intent ledger row's, else the branch's."""
    return str(_intent_row(worktree, plan_branch).get("slug") or slug_of_branch(plan_branch))


def _target_branch(worktree: str, plan_branch: str) -> str:
    """The branch the intent targets, which the executor synchronises with before it commits."""
    return str(_intent_row(worktree, plan_branch).get("target_branch") or "main")


def run(args: argparse.Namespace) -> int | None:
    """Execute the execute command."""
    common_kwargs = prepare_role_execution(args)
    if common_kwargs is None:
        return None
    try:
        common_kwargs["target_worktree"] = execution_worktree(args.plan_branch, common_kwargs.get("target_worktree"))
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {cli.redact_cli_error(exc)}", file=sys.stderr)
        sys.exit(1)
    exit_code = cli.dispatch_role_agent(
        "executor",
        args.agent,
        args.model,
        [args.plan_branch, args.agent, args.model],
        **common_kwargs,
    )
    if exit_code in (0, OVER_BUDGET_EXIT_CODE):
        try:
            push_checked_out_branch(common_kwargs["target_worktree"])
        except RuntimeError as exc:
            print(f"Error: {cli.redact_cli_error(exc)}", file=sys.stderr)
            exit_code = 1
    if exit_code == OVER_BUDGET_EXIT_CODE:
        print(
            f"Error: plan {args.plan_branch} is over its entropy budget and was not executed (exit code "
            f"{exit_code}); its execution branch records the over_budget row. Re-plan it within the budget, or "
            "add an explicit sub-intent list.",
            file=sys.stderr,
        )
    sys.exit(exit_code)
    return None
