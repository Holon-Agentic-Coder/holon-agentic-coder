#!/usr/bin/env python3
"""Containerised entrypoint for the Holon Review Loop Agent (Bean 0118).

Coordinates the multi-pass review loop within an isolated container:
- Proactive conflict detection and resolution.
- Git identity configuration as 'Holon Review Loop Agent'.
- Iterative code diff analysis, CI status probing, and mergeability inspection.
- Automated PR creation or update via GitHub API / gh CLI.
- RFC 2822 commit trailer formatting and discrete ledger recording.
- Enforces Bean 0034 (Human-Only PR Merging) by halting unconditionally upon consensus approval.

Preparation fails closed. The target is resolved before any git operation (a PR number or URL becomes its
head branch and head SHA through gh, never a raw 'git clone --branch 90'); the head branch is cloned or
fetched and checked out with every return code checked; and HEAD must equal the resolved head SHA. On any
resolution, clone, fetch or checkout failure, one ACT-REV failure row is appended to
holon-knowledge/ledger/executions.jsonl when that directory exists (otherwise the reason goes to stderr only),
a FAILED summary is printed and the process exits 1: no consensus is computed or posted.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.agent_defaults import FALLBACK_AGENT, default_model_for
from sandbox_executor.agent_runner import get_repo_url, get_workspace_dir
from sandbox_executor.flow import (
    FlowContext,
    _bounded_error_context,
    check_and_resolve_stage_conflicts,
    run_review_loop,
)
from sandbox_executor.ledger import generate_action_id, try_append_action_record
from sandbox_executor.redaction import redact_agent_secrets, redact_command
from sandbox_executor.review_target import (
    ReviewTarget,
    ReviewTargetError,
    RunGh,
    github_repo_slug,
    is_pr_reference,
    resolve_review_target,
)

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


class ReviewPreparationError(RuntimeError):
    """A preparation step failed; ``stage`` names it and the message is already redacted."""

    def __init__(self, stage: str, detail: str) -> None:
        """Store the failing stage and the redacted detail."""
        super().__init__(redact_agent_secrets(detail))
        self.stage = stage


def _has_checkout(repo_dir: str) -> bool:
    """Return True when ``repo_dir`` already holds a git checkout."""
    return os.path.exists(os.path.join(repo_dir, ".git"))


def _gh_runner(repo_dir: str) -> RunGh:
    """Return a gh runner that names the repository through GH_REPO when there is no checkout yet."""
    env: dict[str, str] | None = None
    if not _has_checkout(repo_dir):
        slug = github_repo_slug(os.getenv("HOLON_REPO_URL", ""))
        if slug:
            env = {**os.environ, "GH_REPO": slug}

    def _run(argv: list[str], cwd: str | None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=False, timeout=60)

    return _run


def _resolve_target(repo_dir: str, raw: str, run_gh: RunGh | None = None) -> ReviewTarget:
    """Resolve the raw target before any git operation; a branch's head SHA is read after the checkout."""
    clean = raw.strip()
    if not clean or clean == "HEAD":
        raise ReviewPreparationError("resolve", "the container review needs a branch, PR number or PR URL target")
    if not is_pr_reference(clean):
        return ReviewTarget(raw=clean, head_branch=clean, head_sha="")
    try:
        return resolve_review_target(repo_dir, clean, run_gh=run_gh or _gh_runner(repo_dir))
    except ReviewTargetError as exc:
        raise ReviewPreparationError("resolve", str(exc)) from exc


def _checked(args: list[str], repo_dir: str, stage: str) -> subprocess.CompletedProcess[str]:
    """Run a git command and raise ``ReviewPreparationError`` with its redacted stderr when it fails."""
    result = run_cmd(args, cwd=repo_dir, check=False)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip() or f"exit code {result.returncode}"
        raise ReviewPreparationError(stage, f"{redact_command(args)} failed: {stderr}")
    return result


def _clone_or_fetch(repo_dir: str, target: ReviewTarget) -> None:
    """Clone the head branch into a fresh workspace, or fetch and check it out in an existing one."""
    branch = target.head_branch
    if not _has_checkout(repo_dir):
        try:
            repo_url = get_repo_url()
        except RuntimeError as exc:
            raise ReviewPreparationError("clone", str(exc)) from exc
        print(f"Cloning {branch} into {repo_dir} from {redact_command([repo_url])}...", file=sys.stderr)
        _checked(["git", "clone", "--branch", branch, repo_url, "."], repo_dir, "clone")
        return
    _checked(["git", "fetch", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}"], repo_dir, "fetch")
    _checked(["git", "checkout", "-B", branch, f"origin/{branch}"], repo_dir, "checkout")


def _rev_parse(repo_dir: str, rev: str, stage: str) -> str:
    """Return the full SHA ``rev`` names, raising when git cannot resolve it."""
    sha = (_checked(["git", "rev-parse", "--verify", f"{rev}^{{commit}}"], repo_dir, stage).stdout or "").strip()
    if not sha:
        raise ReviewPreparationError(stage, f"git rev-parse {rev} returned no commit")
    return sha


def _verify_head(repo_dir: str, target: ReviewTarget) -> ReviewTarget:
    """Make HEAD the resolved head commit, fetching that SHA explicitly if the branch has moved."""
    head_sha = target.head_sha or _rev_parse(repo_dir, f"refs/remotes/origin/{target.head_branch}", "verify")
    if _rev_parse(repo_dir, "HEAD", "verify") != head_sha:
        _checked(["git", "fetch", "origin", head_sha], repo_dir, "fetch")
        _checked(["git", "checkout", "--detach", head_sha], repo_dir, "checkout")
        actual = _rev_parse(repo_dir, "HEAD", "verify")
        if actual != head_sha:
            raise ReviewPreparationError("verify", f"HEAD is {actual}, expected the review head {head_sha}")
    return ReviewTarget(
        raw=target.raw,
        head_branch=target.head_branch,
        head_sha=head_sha,
        pr_number=target.pr_number,
        title=target.title,
        body=target.body,
    )


def _record_preparation_failure(repo_dir: str, raw: str, stage: str, detail: str) -> bool:
    """Append one ACT-REV failure row when the workspace has a ledger directory; True when it was written."""
    ledger_dir = os.path.join(repo_dir, "holon-knowledge", "ledger")
    if not os.path.isdir(ledger_dir):
        return False
    record = {
        "action_id": generate_action_id("ACT-REV"),
        "execution_id": f"REV-PREP-{stage}-{int(datetime.now(UTC).timestamp())}",
        "action": f"PR Review Loop preparation ({stage}) failed for {raw}",
        "category": "code",
        "target": raw,
        "outcome": "failure",
        "entry_type": "action",
        "action_origin": "pr_review",
        "labels": ["review", "review-loop", "preparation"],
        "error_context": _bounded_error_context(detail),
        "created_at": datetime.now(UTC).isoformat(),
    }
    written = try_append_action_record(
        os.path.join(ledger_dir, "executions.jsonl"),
        record,
        label="Stage 4 review preparation failure",
        preserve_shape=True,
    )
    return written is not None


def _fail_preparation(repo_dir: str, raw: str, error: ReviewPreparationError, json_output: bool) -> None:
    """Record the failure, print a FAILED summary and exit 1 without computing or posting a consensus."""
    detail = _bounded_error_context(str(error))
    if not _record_preparation_failure(repo_dir, raw, error.stage, detail):
        print(f"Review preparation failed ({error.stage}): {detail}", file=sys.stderr)
        print("No ledger row was written: the workspace has no holon-knowledge/ledger/ directory.", file=sys.stderr)
    if json_output:
        result = {
            "target": raw,
            "consensus": {"approved": False, "status": "failed", "iterations": 0},
            "error": f"Review preparation failed ({error.stage}): {detail}",
        }
        print(json.dumps(result, indent=2))
    else:
        print("\n================== Holon Review Loop Summary ==================")
        print(f"  Target         : {raw}")
        print(f"  Stage          : {error.stage}")
        print(f"  Reason         : {detail}")
        print("  Consensus      : FAILED")
        print("===============================================================\n")
    sys.exit(1)


def _prepare_workspace(repo_dir: str, raw: str) -> ReviewTarget:
    """Resolve the target, then clone or fetch it and verify HEAD; raises ``ReviewPreparationError``."""
    target = _resolve_target(repo_dir, raw)
    _clone_or_fetch(repo_dir, target)
    return _verify_head(repo_dir, target)


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
        help="Agent runner to execute (default: the fallback runner, antigravity-agent).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name to pass to agent (default: the runner's default model).",
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
        help=(
            "Automatically create PR if only a branch exists (default: True). The empty-diff guard runs either way; "
            "the exit code is 0 only when the consensus is approved and posted."
        ),
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

    # The host CLI always passes resolved values; no harness detection inside the container.
    agent_name = args.agent or args.pos_agent or FALLBACK_AGENT
    model_name = args.model or args.pos_model or default_model_for(agent_name)
    repo_dir = args.repo_dir or get_workspace_dir()
    os.makedirs(repo_dir, exist_ok=True)

    # Git workspace preparation: fail closed before any consensus is computed.
    try:
        review = _prepare_workspace(repo_dir, args.target)
    except ReviewPreparationError as exc:
        _fail_preparation(repo_dir, args.target, exc, args.json)
        return
    # A PR stays the loop target so mergeability, CI and posting address the PR; ledger lookups use its head branch.
    loop_target = review.raw if review.is_pr else review.head_branch
    head_branch = review.head_branch

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
                            if b and b in head_branch:
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
                            if pid and pid in head_branch:
                                plan_branch = d.get("plan_branch") or pid
                        except json.JSONDecodeError:
                            continue
        except Exception as exc:
            logger.debug("Non-fatal error reading plans ledger: %s", exc)

    context = FlowContext(
        repo_dir=repo_dir,
        intent_data=intent_data,
        plan_branch=plan_branch,
        execution_branch=head_branch,
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
        target=loop_target,
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
        if not approved and loop_res.get("error"):
            print(f"  Reason         : {_bounded_error_context(str(loop_res['error']))}")
        print("===============================================================\n")
        if approved:
            print("[BEAN 0034 SAFETY HALT] PR Review Loop reached consensus approval.")
            print("Pipeline halted for human review (autonomous merge prohibited).")
            cmd = loop_res.get("human_approval_command", f"holon review approve {args.target}")
            print(f"To approve manually, run: {cmd}\n")

    sys.exit(0 if approved else 1)


if __name__ == "__main__":
    main()
