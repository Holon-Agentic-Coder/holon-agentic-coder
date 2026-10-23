#!/usr/bin/env python3
"""Containerised entrypoint for the Holon Review Loop Agent (Bean 0118).

Coordinates the multi-pass review loop within an isolated container:
- Proactive conflict detection and resolution.
- Git identity configuration as 'Holon Review Loop Agent'.
- Iterative code diff analysis, CI status probing, and mergeability inspection.
- Automated PR creation or update via GitHub API / gh CLI.
- RFC 2822 commit trailer formatting and discrete ledger recording.
- Enforces Bean 0034 (Human-Only PR Merging) by halting unconditionally upon consensus approval.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from typing import Any

from sandbox_executor.agent_runner import get_repo_url, get_workspace_dir
from sandbox_executor.flow import FlowContext, check_and_resolve_stage_conflicts, run_review_loop
from sandbox_executor.redaction import redact_agent_secrets, redact_command

logger = logging.getLogger("holon.entrypoint.review_loop")


def run_cmd(
    args: list[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Execute a system command and return CompletedProcess, exiting on non-zero if check=True.

    The echo and the failure diagnostics are routed through the shared redactors, because the clone
    URL and git's own error messages can carry the GitHub token. The returned stdout and stderr are
    raw and unredacted.
    """
    print(f"Running: {redact_command(args)}", file=sys.stderr)
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0 and check:
        print(f"Command failed with code {result.returncode}", file=sys.stderr)
        if result.stdout:
            print(f"Stdout:\n{redact_agent_secrets(result.stdout)}", file=sys.stderr)
        if result.stderr:
            print(f"Stderr:\n{redact_agent_secrets(result.stderr)}", file=sys.stderr)
        sys.exit(result.returncode)
    return result


def main() -> None:
    """Parse CLI arguments and execute containerised review loop."""
    parser = argparse.ArgumentParser(
        prog="review_loop",
        description="Holon Review Loop Agent container entrypoint.",
    )
    parser.add_argument("target", help="Target execution branch name, PR number, or PR URL.")
    parser.add_argument("pos_agent", nargs="?", default=None, help="Agent runner (positional fallback).")
    parser.add_argument("pos_model", nargs="?", default=None, help="Model name (positional fallback).")
    parser.add_argument(
        "--agent",
        default=None,
        help="Agent runner to execute (default: antigravity-agent).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name to pass to agent (default: gemini-3.8-flash-medium).",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=25,
        help="Maximum review loop iterations (default: 25).",
    )
    parser.add_argument(
        "--ensemble-size",
        type=int,
        default=3,
        help="Evaluation ensemble heuristic count (default: 3).",
    )
    parser.add_argument(
        "--auto-pr",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Automatically create PR if only a branch exists (default: True).",
    )
    parser.add_argument(
        "--skip-push",
        action="store_true",
        default=False,
        help="Skip pushing git commits or branches to remote.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run review evaluation without applying changes or git commits.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output review loop results in structured JSON format.",
    )
    parser.add_argument(
        "--token-reduce",
        action="store_true",
        default=False,
        help="Enable proxy-based token reduction.",
    )
    parser.add_argument(
        "--mitm-web",
        action="store_true",
        default=False,
        help="Enable mitmweb proxy dashboard.",
    )
    parser.add_argument(
        "--repo-dir",
        default=None,
        help="Repository root directory override.",
    )

    args = parser.parse_args()

    agent_name = args.agent or args.pos_agent or "antigravity-agent"
    model_name = args.model or args.pos_model or "gemini-3.8-flash-medium"
    repo_dir = args.repo_dir or get_workspace_dir()
    os.makedirs(repo_dir, exist_ok=True)

    # Git workspace preparation
    if not os.path.exists(os.path.join(repo_dir, ".git")):
        repo_url = get_repo_url()
        print(f"Cloning {args.target} into {repo_dir} from {redact_command([repo_url])}...", file=sys.stderr)
        run_cmd(["git", "clone", "--branch", args.target, repo_url, "."], cwd=repo_dir, check=False)
    else:
        run_cmd(["git", "fetch", "origin"], cwd=repo_dir, check=False)
        run_cmd(["git", "checkout", args.target], cwd=repo_dir, check=False)

    # Configure local git author and committer identity for Review Loop Agent
    run_cmd(
        ["git", "config", "--local", "user.name", "Holon Review Loop Agent"],
        cwd=repo_dir,
        check=False,
    )
    run_cmd(
        ["git", "config", "--local", "user.email", "review-loop-agent@holon-agentic-coder.com"],
        cwd=repo_dir,
        check=False,
    )

    # Proactive conflict detection and resolution against target upstream branch (Bean 0121)
    target_branch = "main"
    check_and_resolve_stage_conflicts(repo_dir=repo_dir, target_branch=target_branch, stage_name="review")

    # Read intent and plan metadata from ledger if available
    intent_data: dict[str, Any] = {}
    plan_branch: str | None = None

    intents_path = os.path.join(repo_dir, "holon-knowledge/ledger/intents.jsonl")
    if os.path.exists(intents_path):
        try:
            with open(intents_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            d = json.loads(line)
                            b = d.get("branch", "")
                            if b and b in args.target:
                                intent_data = d
                        except json.JSONDecodeError:
                            continue
        except Exception as exc:
            logger.debug("Non-fatal error reading intents ledger: %s", exc)

    plans_path = os.path.join(repo_dir, "holon-knowledge/ledger/plans.jsonl")
    if os.path.exists(plans_path):
        try:
            with open(plans_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            d = json.loads(line)
                            pid = d.get("plan_id", "")
                            if pid and pid in args.target:
                                plan_branch = d.get("plan_branch") or pid
                        except json.JSONDecodeError:
                            continue
        except Exception as exc:
            logger.debug("Non-fatal error reading plans ledger: %s", exc)

    context = FlowContext(
        repo_dir=repo_dir,
        intent_data=intent_data,
        plan_branch=plan_branch,
        execution_branch=args.target,
        agent=agent_name,
        model=model_name,
        dry_run=args.dry_run,
        skip_push=args.skip_push,
        max_review_iterations=args.max_iterations,
        token_reduce=args.token_reduce or args.mitm_web,
        mitm_web=args.mitm_web,
    )

    loop_res = run_review_loop(
        context=context,
        target=args.target,
        max_iterations=args.max_iterations,
        ensemble_size=args.ensemble_size,
        auto_pr=args.auto_pr,
        dry_run=args.dry_run,
    )

    consensus = loop_res.get("consensus", {})
    approved = bool(consensus.get("approved"))

    if args.json:
        print(json.dumps(loop_res, indent=2))
    else:
        print("\n================== Holon Review Loop Summary ==================")
        print(f"  Target         : {args.target}")
        print(
            f"  Iterations     : {consensus.get('iterations', 1)} / "
            f"{loop_res.get('max_iterations', args.max_iterations)}"
        )
        ci = loop_res.get("ci_checks", {})
        print(
            f"  CI Checks      : {ci.get('total', 0)} total, {ci.get('passed', 0)} passed, "
            f"{ci.get('failed', 0)} failed, {ci.get('pending', 0)} pending ({ci.get('source', 'unknown')})"
        )
        print(f"  Consensus      : {consensus.get('status', 'unknown').upper()}")
        print("===============================================================\n")
        if approved:
            print("[BEAN 0034 SAFETY HALT] PR Review Loop reached consensus approval.")
            print("Pipeline halted for human review (autonomous merge prohibited).")
            cmd = loop_res.get("human_approval_command", f"holon review approve {args.target}")
            print(f"To approve manually, run: {cmd}\n")

    sys.exit(0 if approved else 1)


if __name__ == "__main__":
    main()
