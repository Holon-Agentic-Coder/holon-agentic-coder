#!/usr/bin/env python3
"""Containerised entrypoint for the Holon Review Loop Agent (Bean 0118).

Coordinates the multi-pass review loop within an isolated container:
- Proactive conflict detection and resolution.
- Git identity configuration as 'Holon Review Loop Agent'.
- Independent reviewer agent turns on the real diff (the container is the sandbox they must run in), plus CI
  status probing and mergeability inspection; the exit code is 0 only for an APPROVED ensemble verdict.
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
import contextlib
import dataclasses
import json
import logging
import os
import subprocess
import sys
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.agent_defaults import FALLBACK_AGENT, default_model_for
from sandbox_executor.agent_runner import get_repo_url, get_runner, get_workspace_dir
from sandbox_executor.flow import (
    FlowContext,
    _bounded_error_context,
    check_and_resolve_stage_conflicts,
    review_loop_approved,
    review_summary_lines,
    run_review_loop,
)
from sandbox_executor.ledger import generate_action_id, try_append_action_record
from sandbox_executor.redaction import redact_agent_secrets, redact_command
from sandbox_executor.review_ensemble import DEFAULT_ENSEMBLE_SIZE, ENSEMBLE_SIZE_HELP
from sandbox_executor.review_report import (
    VALID_REJECTION_REASONS,
    FindingDisposition,
    FindingRow,
    deserialize_finding_dispositions,
    serialize_finding_disposition,
)
from sandbox_executor.review_target import (
    GH_TIMEOUT_SECONDS,
    ReviewTarget,
    ReviewTargetError,
    RunGh,
    github_repo_slug,
    is_pr_reference,
    resolve_review_target,
)
from sandbox_executor.turns import run_turn

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
        return subprocess.run(
            argv, cwd=cwd, env=env, capture_output=True, text=True, check=False, timeout=GH_TIMEOUT_SECONDS
        )

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
    return dataclasses.replace(target, head_sha=head_sha)


def _record_preparation_failure(repo_dir: str, raw: str, stage: str, detail: str) -> bool:
    """Append one ACT-REV failure row when the workspace has a ledger directory; True when it was written."""
    ledger_dir = os.path.join(repo_dir, "holon-knowledge", "ledger")
    if not os.path.isdir(ledger_dir):
        return False
    record = {
        "action_id": generate_action_id("ACT-REV"),
        "execution_id": f"REV-PREP-{stage}-{int(datetime.now(UTC).timestamp())}",
        "action": f"Review Loop preparation ({stage}) failed for {raw}",
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


def _fail_preparation(
    repo_dir: str,
    raw: str,
    error: ReviewPreparationError,
    json_output: bool,
    result_file: str | None = None,
) -> None:
    """Record the failure, print a FAILED summary and exit 1 without computing or posting a consensus."""
    detail = _bounded_error_context(str(error))
    if not _record_preparation_failure(repo_dir, raw, error.stage, detail):
        print(f"Review preparation failed ({error.stage}): {detail}", file=sys.stderr)
        print("No ledger row was written: the workspace has no holon-knowledge/ledger/ directory.", file=sys.stderr)
    result = {
        "target": raw,
        "consensus": {"approved": False, "status": "failed", "iterations": 0},
        "ensemble_verdict": "FAILED",
        "reviewers": [],
        "error": f"Review preparation failed ({error.stage}): {detail}",
    }
    if result_file:
        try:
            with open(result_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
        except OSError as exc:
            print(f"Warning: could not write result file '{result_file}': {exc}", file=sys.stderr)
    if json_output:
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


_FORBIDDEN_GIT_FLAGS = frozenset({"--force", "-f", "--force-with-lease", "--amend", "rebase"})
_SEVERITY_ORDER = {"clean": 0, "nit": 1, "important": 2, "critical": 3}


def _assert_safe_git_args(args: list[str]) -> None:
    """Refuse any git command that force-pushes or rewrites history."""
    for token in args:
        clean = token.strip()
        if clean in _FORBIDDEN_GIT_FLAGS or clean.startswith("--force"):
            raise ValueError(f"Forbidden history-rewriting or force-push git argument: {clean}")


def _verify_before_push(repo_dir: str) -> tuple[bool, bool, str | None]:
    """Run ``uv run task lint`` and ``uv run task test`` before committing or pushing resolver changes."""
    lint_res = run_cmd(["uv", "run", "task", "lint"], cwd=repo_dir, check=False)
    if lint_res.returncode != 0:
        detail = _bounded_error_context((lint_res.stderr or lint_res.stdout or "").strip() or "lint failed")
        return False, False, f"Resolver pre-push lint failed (exit code {lint_res.returncode}): {detail}"
    test_res = run_cmd(["uv", "run", "task", "test"], cwd=repo_dir, check=False)
    if test_res.returncode != 0:
        detail = _bounded_error_context((test_res.stderr or test_res.stdout or "").strip() or "tests failed")
        return True, False, f"Resolver pre-push test failed (exit code {test_res.returncode}): {detail}"
    return True, True, None


def _load_resolver_findings(findings_file: str | None) -> tuple[list[FindingRow], dict[str, str]]:
    """Read input findings and their introducing action IDs from ``findings_file``."""
    if not findings_file or not os.path.exists(findings_file):
        return [], {}
    try:
        with open(findings_file, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return [], {}
    raw_items = payload.get("findings", []) if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list):
        return [], {}
    rows: list[FindingRow] = []
    action_ids: dict[str, str] = {}
    for idx, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        ident = str(item.get("id") or f"R1-F{idx}").strip()
        raw_line = item.get("line")
        line = raw_line if isinstance(raw_line, int) and not isinstance(raw_line, bool) and raw_line >= 1 else None
        raw_revs = item.get("reviewers")
        revs = tuple(int(r) for r in raw_revs if isinstance(r, int)) if isinstance(raw_revs, list) else (1,)
        rows.append(
            FindingRow(
                id=ident,
                severity=str(item.get("severity") or "nit").strip().lower(),
                path=str(item.get("path") or item.get("file") or "").strip(),
                line=line,
                reviewers=revs or (1,),
                summary=str(item.get("summary") or "").strip(),
                recommendation=str(item.get("recommendation") or "not supplied").strip(),
            )
        )
        intro_id = str(item.get("introduced_by_action_id") or "").strip()
        if intro_id:
            action_ids[ident] = intro_id
    return rows, action_ids


def _build_resolver_prompt(
    repo_dir: str,
    head_branch: str,
    iteration: int,
    findings: list[FindingRow],
    dispositions_path: str,
) -> str:
    """Build the resolver turn prompt instructing the agent to adjudicate every finding."""
    serialized = [
        {
            "id": row.id,
            "severity": row.severity,
            "path": row.path,
            "line": row.line,
            "summary": row.summary,
            "recommendation": row.recommendation,
        }
        for row in findings
    ]
    reasons = ", ".join(f"`{r}`" for r in VALID_REJECTION_REASONS if r != "no-convergence")
    return (
        f"# Holon Stage 4 Resolver Pass (Iteration {iteration})\n\n"
        f"You are operating in `{repo_dir}` on branch `{head_branch}`.\n"
        "Adjudicate EVERY finding below (critical, important, and nit) to exactly one disposition:\n"
        "- `applied`: fix the code in the working tree so `uv run task lint` and `uv run task test` pass.\n"
        f"- `rejected`: provide a one-line `rejection_reason` from: {reasons}.\n"
        "Never force-push or rewrite git history. Do not run `git push` yourself; write the JSON list of "
        f"dispositions to `{dispositions_path}` with keys `id`, `disposition`, and `rejection_reason`.\n\n"
        f"```json\n{json.dumps(serialized, indent=2)}\n```\n"
    )


def _run_resolver_agent(
    repo_dir: str,
    agent_name: str,
    model_name: str,
    prompt: str,
    dispositions_path: str,
) -> list[dict[str, Any]]:
    """Run the resolver agent turn and parse its dispositions JSON file."""
    os.makedirs(os.path.dirname(dispositions_path), exist_ok=True)
    runner = get_runner(agent_name)
    outcome = run_turn(runner, prompt, model_name, cwd=repo_dir)
    if os.path.exists(dispositions_path):
        try:
            with open(dispositions_path, encoding="utf-8") as f:
                parsed = json.load(f)
            if isinstance(parsed, dict) and isinstance(parsed.get("dispositions"), list):
                return parsed["dispositions"]
            if isinstance(parsed, list):
                return parsed
        except (OSError, ValueError):
            pass
    if outcome.exit_code != 0:
        raise RuntimeError(f"Resolver agent exited with code {outcome.exit_code}")
    return []


@dataclasses.dataclass(frozen=True)
class CommitPushOptions:
    """Options governing resolution commit and push behavior."""

    skip_push: bool = False
    dry_run: bool = False


def _commit_and_push_resolution(
    repo_dir: str,
    head_branch: str,
    iteration: int,
    dispositions: tuple[FindingDisposition, ...],
    options: CommitPushOptions | None = None,
) -> tuple[tuple[FindingDisposition, ...], str, bool, bool, bool, str | None]:
    """Verify via lint and test, commit applied fixes with finding IDs, and fast-forward push."""
    opts = options or CommitPushOptions()
    initial_head = (_checked(["git", "rev-parse", "HEAD"], repo_dir, "resolve").stdout or "").strip()
    status_proc = run_cmd(["git", "status", "--porcelain"], cwd=repo_dir, check=False)
    has_worktree_changes = bool((status_proc.stdout or "").strip())
    applied_items = [d for d in dispositions if d.disposition == "applied"]

    if not applied_items or not has_worktree_changes:
        return dispositions, initial_head, False, True, True, None

    if opts.dry_run:
        return dispositions, initial_head, False, True, True, None

    lint_ok, tests_ok, verify_err = _verify_before_push(repo_dir)
    if not lint_ok or not tests_ok:
        return dispositions, initial_head, False, lint_ok, tests_ok, verify_err

    run_cmd(["git", "add", "-A"], cwd=repo_dir, check=True)
    finding_ids = [d.id for d in applied_items]
    ids_csv = ", ".join(finding_ids) or f"ITER-{iteration}"
    severities = [d.severity for d in applied_items] or ["nit"]
    worst = max(severities, key=lambda s: _SEVERITY_ORDER.get(s, 1))
    commit_msg = (
        f"fix(review): resolve findings {ids_csv} (iteration {iteration})\n\n"
        "Action-Origin: pr_review\n"
        f"Review-Iteration: {iteration}\n"
        f"Review-Finding-Ids: {ids_csv}\n"
        f"Review-Severity: {worst}\n"
    )
    commit_cmd = ["git", "commit", "-m", commit_msg]
    _assert_safe_git_args(commit_cmd)
    commit_res = run_cmd(commit_cmd, cwd=repo_dir, check=False)
    if commit_res.returncode != 0:
        err = _bounded_error_context((commit_res.stderr or commit_res.stdout or "").strip() or "git commit failed")
        return dispositions, initial_head, False, True, True, f"Resolver commit failed: {err}"

    new_head = (_checked(["git", "rev-parse", "HEAD"], repo_dir, "resolve").stdout or "").strip()
    updated = tuple(
        dataclasses.replace(d, commit_sha=new_head) if d.disposition == "applied" else d for d in dispositions
    )

    pushed = False
    if not opts.skip_push and new_head != initial_head:
        push_cmd = ["git", "push", "origin", f"HEAD:refs/heads/{head_branch}"]
        _assert_safe_git_args(push_cmd)
        push_res = run_cmd(push_cmd, cwd=repo_dir, check=False)
        if push_res.returncode != 0:
            err = _bounded_error_context((push_res.stderr or push_res.stdout or "").strip() or "git push failed")
            return updated, new_head, False, True, True, f"Resolver push failed: {err}"
        pushed = True

    return updated, new_head, pushed, True, True, None


def _run_resolver_mode(
    args: argparse.Namespace,
    repo_dir: str,
    head_branch: str,
    agent_name: str,
    model_name: str,
) -> None:
    """Execute the containerised resolver pass for ``--resolve`` and exit."""
    findings_source = args.findings_file or args.result_file
    findings, action_ids = _load_resolver_findings(findings_source)
    dispositions_path = os.path.join(repo_dir, ".holon", "review", f"iter-{args.iteration}", "dispositions.json")
    error: str | None = None
    raw_dispositions: list[dict[str, Any]] = []
    if findings and not args.dry_run:
        prompt = _build_resolver_prompt(repo_dir, head_branch, args.iteration, findings, dispositions_path)
        try:
            raw_dispositions = _run_resolver_agent(repo_dir, agent_name, model_name, prompt, dispositions_path)
        except Exception as exc:
            error = f"Resolver turn failed: {_bounded_error_context(str(exc))}"
        finally:
            if os.path.exists(dispositions_path):
                with contextlib.suppress(OSError):
                    os.remove(dispositions_path)

    dispositions = deserialize_finding_dispositions(raw_dispositions, findings, action_ids)
    head_sha = (_checked(["git", "rev-parse", "HEAD"], repo_dir, "resolve").stdout or "").strip()
    pushed = False
    lint_ok = True
    tests_ok = True
    if error is None and findings:
        dispositions, head_sha, pushed, lint_ok, tests_ok, error = _commit_and_push_resolution(
            repo_dir,
            head_branch,
            args.iteration,
            dispositions,
            CommitPushOptions(skip_push=args.skip_push, dry_run=args.dry_run),
        )

    outcome = {
        "resolver": True,
        "target": args.target,
        "head_branch": head_branch,
        "iteration": args.iteration,
        "dispositions": [serialize_finding_disposition(item) for item in dispositions],
        "head_sha": head_sha,
        "pushed": pushed,
        "lint_passed": lint_ok,
        "tests_passed": tests_ok,
        "error": error,
    }
    if args.result_file:
        try:
            with open(args.result_file, "w", encoding="utf-8") as f:
                json.dump(outcome, f, indent=2)
        except OSError as exc:
            print(f"Warning: could not write result file '{args.result_file}': {exc}", file=sys.stderr)
    if args.json:
        print(json.dumps(outcome, indent=2))
    sys.exit(0 if error is None else 1)


def main() -> None:  # noqa: C901, PLR0912, PLR0915
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
        "--iteration",
        type=int,
        default=1,
        help=(
            "Label of the first pass (default: 1), used for the ledger review_iteration fields and the posted "
            "'Iteration {n}' heading. The flow engine owns the counter, saves it in the flow checkpoint "
            "(review_iterations_done), and on a resume from the review stage passes the continued count as this "
            "label so ledger review_iteration values and posted 'Iteration {n}' headings form one monotonic sequence "
            "across halts and resumes; it is never a loop limit and is never set by operators."
        ),
    )
    parser.add_argument(
        "--resolve",
        action="store_true",
        default=False,
        help=(
            "Run in resolver mode: read findings from --findings-file (or --result-file), adjudicate every finding "
            "to applied or rejected, verify with lint and tests before pushing, and write the resolver outcome JSON."
        ),
    )
    parser.add_argument(
        "--findings-file",
        default=None,
        help="Path to input findings JSON for --resolve mode (defaults to --result-file).",
    )
    parser.add_argument(
        "--ensemble-size",
        type=int,
        default=DEFAULT_ENSEMBLE_SIZE,
        help=ENSEMBLE_SIZE_HELP,
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
        "--skip-post",
        action="store_true",
        default=False,
        help="Skip posting review consensus to GitHub.",
    )
    parser.add_argument(
        "--result-file",
        default=None,
        help="Path to write structured review outcome JSON for host transport.",
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
    if args.iteration < 1:
        parser.error(f"--iteration must be at least 1, got {args.iteration}.")

    # The host CLI always passes resolved values; no harness detection inside the container.
    agent_name = args.agent or args.pos_agent or FALLBACK_AGENT
    model_name = args.model or args.pos_model or default_model_for(agent_name)
    repo_dir = args.repo_dir or get_workspace_dir()
    os.makedirs(repo_dir, exist_ok=True)

    # Git workspace preparation: fail closed before any consensus is computed.
    try:
        review = _prepare_workspace(repo_dir, args.target)
    except ReviewPreparationError as exc:
        _fail_preparation(repo_dir, args.target, exc, args.json, result_file=args.result_file)
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

    if args.resolve:
        _run_resolver_mode(args, repo_dir, head_branch, agent_name, model_name)
        return

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
        skip_post=args.skip_post,
        first_iteration=args.iteration,
    )

    if args.result_file:
        try:
            with open(args.result_file, "w", encoding="utf-8") as f:
                json.dump(loop_res, f, indent=2)
        except OSError as exc:
            print(f"Warning: could not write result file '{args.result_file}': {exc}", file=sys.stderr)

    consensus = loop_res.get("consensus", {})
    # Exit 0 only for an APPROVED ensemble verdict whose consensus was approved and posted.
    approved = review_loop_approved(loop_res)

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
        for line in review_summary_lines(loop_res):
            print(line)
        print(f"  Consensus      : {consensus.get('status', 'unknown').upper()}")
        if not approved and loop_res.get("error"):
            print(f"  Reason         : {_bounded_error_context(str(loop_res['error']))}")
        print("===============================================================\n")
        if approved:
            print("[BEAN 0034 SAFETY HALT] Review Loop reached consensus approval.")
            print("Pipeline halted for human review (autonomous merge prohibited).")
            cmd = loop_res.get("human_approval_command", f"holon review approve {args.target}")
            print(f"To approve manually, run: {cmd}\n")

    sys.exit(0 if approved else 1)


if __name__ == "__main__":
    main()
