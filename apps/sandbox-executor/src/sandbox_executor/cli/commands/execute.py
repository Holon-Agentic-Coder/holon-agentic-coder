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
_DEFAULT_TARGET_BRANCH = "main"


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("execute", help="Run Sandbox Executor to execute code changes for a plan.")
    parser.add_argument("plan_branch", help="Target plan branch name")
    add_runner_options(parser)
    add_preflight_option(parser)
    add_token_reduction_options(parser)
    add_target_worktree_option(parser)
    parser.set_defaults(_run=run)


def execution_worktree(plan_branch: str, target_worktree: str | None) -> str:
    shared_ref_worktree = target_worktree or os.getcwd()
    git_flow.fetch_origin_branch(shared_ref_worktree, plan_branch)
    git_flow.fetch_origin_branch(shared_ref_worktree, target_branch(shared_ref_worktree, plan_branch))
    if git_flow.is_default_branch_worktree(
        shared_ref_worktree, git_flow.repository_default_branch(shared_ref_worktree)
    ):
        return git_flow.ensure_flow_worktree_off_default(
            shared_ref_worktree, _intent_slug(shared_ref_worktree, plan_branch), None
        )
    return shared_ref_worktree


def _intent_row(worktree: str, plan_branch: str) -> dict:
    intent = plan_branch.split("/", 1)[0]
    rows = _read_ledger_rows(worktree, f"origin/{plan_branch}", "intents.jsonl")
    return next((row for row in reversed(rows) if str(row.get("branch") or "").removesuffix("/_") == intent), {})


def _intent_slug(worktree: str, plan_branch: str) -> str:
    return str(_intent_row(worktree, plan_branch).get("slug") or slug_of_branch(plan_branch))


def target_branch(worktree: str, plan_branch: str) -> str:
    return str(_intent_row(worktree, plan_branch).get("target_branch") or _DEFAULT_TARGET_BRANCH)


def _push_branch_preserving_failure_exit(worktree: str, dispatch_exit_code: int) -> int:
    try:
        push_checked_out_branch(worktree)
    except RuntimeError as exc:
        # why: after a failed dispatch there may be no branch to push; a push error must not bury the run's exit code
        if dispatch_exit_code == 0:
            print(f"Error: {cli.redact_cli_error(exc)}", file=sys.stderr)
            return 1
    return dispatch_exit_code


def run(args: argparse.Namespace) -> int | None:
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
    exit_code = _push_branch_preserving_failure_exit(common_kwargs["target_worktree"], exit_code)
    if exit_code == OVER_BUDGET_EXIT_CODE:
        print(
            f"Error: plan {args.plan_branch} is over its entropy budget and was not executed (exit code "
            f"{exit_code}); its execution branch records the over_budget row. Re-plan it within the budget, or "
            "add an explicit sub-intent list.",
            file=sys.stderr,
        )
    sys.exit(exit_code)
    return None
