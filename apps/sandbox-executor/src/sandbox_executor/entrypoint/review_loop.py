#!/usr/bin/env python3
"""Containerised entrypoint for the Holon Review Loop Agent.

Coordinates the multi-pass review loop within an isolated container:
- Proactive conflict detection and resolution.
- Git identity 'Holon Review Loop Agent' applied only to the resolver commit, through a per-call environment copy
  of REVIEW_GIT_ENV; never in the process environment, never in git config.
- Independent reviewer agent turns on the real diff (the container is the sandbox they must run in), plus CI
  status probing and mergeability inspection; the exit code is 0 only for an APPROVED ensemble verdict.
- Automated PR creation or update via GitHub API / gh CLI.
- RFC 2822 commit trailer formatting and discrete ledger recording.
- Enforces the human-only PR merge rule by completing with approved consensus without merging to main.

Preparation fails closed. The target is resolved before any git operation (a PR number or URL becomes its
head branch and head SHA through gh). The workspace is the host worktree, bind-mounted by the host and used
in place: it is never cloned, fetched, reset or cleaned here, so a crash loses no edit. It must have the head
branch checked out and HEAD must equal the resolved head SHA. On any resolution or validation failure, one
ACT-REV failure row is appended to
holon-knowledge/ledger/executions.jsonl when that directory exists (otherwise the reason goes to stderr only),
a FAILED summary is printed and the process exits 1: no consensus is computed or posted.
"""

import argparse
import contextlib
import dataclasses
import json
import logging
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.agent_defaults import FALLBACK_AGENT, default_model_for
from sandbox_executor.agent_runner import get_runner, get_workspace_dir
from sandbox_executor.budgets import load_budgets
from sandbox_executor.entrypoint.rejected_attempt import (
    clear_rejected_attempts,
    load_rejected_attempt,
    previous_failure_section,
    read_saved_patch,
)
from sandbox_executor.entrypoint.review_failure_detail import clear_full_output, with_short_summary
from sandbox_executor.entrypoint.review_loop_args import build_parser
from sandbox_executor.entrypoint.review_markdown_gate import run_markdown_gate
from sandbox_executor.entrypoint.review_verification import (
    missing_tool_result,
    verification_error,
)
from sandbox_executor.entrypoint.uv_lock import LockfileMismatchError, check_lockfile, frozen_env
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.review.delta_review_forwarding import load_forwarded_decision
from sandbox_executor.flow.review.git_identity import review_git_env
from sandbox_executor.flow.review.loop import run_review_loop
from sandbox_executor.flow.review.records import _bounded_error_context
from sandbox_executor.flow.review.verdict import review_loop_approved, review_summary_lines
from sandbox_executor.flow.stage_conflicts import check_and_resolve_stage_conflicts
from sandbox_executor.ledger import generate_action_id, read_merged_rows, try_append_action_record
from sandbox_executor.ledger.fragments import PENDING_EXCLUDE_PATHSPEC
from sandbox_executor.module_budget_guard import (
    MODULE_BUDGET_CONFIG,
    module_budget_change_authorised,
    refusal_message,
    with_module_budget_flag,
)
from sandbox_executor.plan_paths import is_row_of_plan, plan_branch_of
from sandbox_executor.redaction import redact_agent_secrets, redact_command
from sandbox_executor.ref_names import INTENT_SUFFIX
from sandbox_executor.resolver_result import RESOLVER_RESULT_MARKER, bounded_output_tail
from sandbox_executor.review_report import (
    VALID_REJECTION_REASONS,
    FindingDisposition,
    FindingRow,
    deserialize_finding_dispositions,
    serialize_finding_disposition,
)
from sandbox_executor.review_target import (
    ReviewTarget,
    ReviewTargetError,
    RunGh,
    is_pr_reference,
    resolve_review_target,
)
from sandbox_executor.turns import run_turn
from sandbox_executor.worktree_head import ReattachStatus, head_guard, reattach_branch

logger = logging.getLogger("holon.entrypoint.review_loop")


def run_cmd(
    args: list[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Execute a system command and return CompletedProcess, exiting on non-zero if check=True.

    The echo and the failure diagnostics are routed through the shared redactors, because git's own error
    messages can carry the GitHub token when a remote URL embeds it. The returned stdout and stderr are
    raw and unredacted. An executable that cannot be started (missing from PATH, not executable) is reported as
    exit code 127 with a stderr naming it, never raised.
    """
    print(f"Running: {redact_command(args)}", file=sys.stderr)
    try:
        result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    except OSError as exc:
        result = missing_tool_result(args, exc)
    if result.returncode != 0 and check:
        print(f"Command failed with code {result.returncode}", file=sys.stderr)
        if result.stdout:
            print(f"Stdout:\n{redact_agent_secrets(result.stdout)}", file=sys.stderr)
        if result.stderr:
            print(f"Stderr:\n{redact_agent_secrets(result.stderr)}", file=sys.stderr)
        sys.exit(result.returncode)
    return result


def _run_git_cmd(
    args: list[str], cwd: str | None = None, check: bool = False, **_: Any
) -> subprocess.CompletedProcess[str]:
    """Adapt ``run_cmd`` to ``run_git``'s convention (no leading ``git``) for the worktree_head helpers."""
    return run_cmd(["git", *args], cwd=cwd, check=check)


def _log_stderr(message: str) -> None:
    print(message, file=sys.stderr)


def _load_delta_request(context: FlowContext, repo_dir: str, rel_path: str | None) -> None:
    """Load the host's scoped decision for this pass; an invalid one is ignored, so the pass is full."""
    if rel_path and not load_forwarded_decision(context, repo_dir, rel_path):
        _log_stderr(f"Ignoring --delta-request={rel_path}: not a valid scoped decision; reviewing in full.")


class ReviewPreparationError(RuntimeError):
    """A preparation step failed; ``stage`` names it and the message is already redacted."""

    def __init__(self, stage: str, detail: str) -> None:
        """Store the failing stage and the redacted detail."""
        super().__init__(redact_agent_secrets(detail))
        self.stage = stage


def _resolve_target(repo_dir: str, raw: str, run_gh: RunGh | None = None) -> ReviewTarget:
    """Resolve the raw target before any git operation; a branch's head SHA is read from the mounted HEAD."""
    clean = raw.strip()
    if not clean or clean == "HEAD":
        raise ReviewPreparationError("resolve", "the container review needs a branch, PR number or PR URL target")
    if not is_pr_reference(clean):
        return ReviewTarget(raw=clean, head_branch=clean, head_sha="")
    try:
        return resolve_review_target(repo_dir, clean, run_gh=run_gh)
    except ReviewTargetError as exc:
        raise ReviewPreparationError("resolve", str(exc)) from exc


def _checked(args: list[str], repo_dir: str, stage: str) -> subprocess.CompletedProcess[str]:
    """Run a git command and raise ``ReviewPreparationError`` with its redacted stderr when it fails."""
    result = run_cmd(args, cwd=repo_dir, check=False)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip() or f"exit code {result.returncode}"
        raise ReviewPreparationError(stage, f"{redact_command(args)} failed: {stderr}")
    return result


def _validate_mounted_workspace(repo_dir: str, target: ReviewTarget) -> ReviewTarget:
    """Validate that ``repo_dir`` is the host worktree checked out at the review head.

    HEAD must be a readable commit, on ``target.head_branch`` when a branch is checked out, and equal to
    ``target.head_sha`` when the target resolved one (a PR head): a stale worktree is never reviewed under the PR
    head's label. A branch target's head SHA is the mounted HEAD. A branch target whose HEAD is detached exactly at the
    branch tip is re-attached (``git symbolic-ref``, the one documented change to ``HEAD``; the index and the tree,
    uncommitted ledger rows included, are untouched); a HEAD detached anywhere else is refused. No clone, fetch,
    reset, wipe or tree change.
    """
    if not os.path.exists(repo_dir) or not os.path.isdir(repo_dir):
        raise ReviewPreparationError("validate", f"workspace directory '{repo_dir}' does not exist")

    run_cmd(["git", "config", "--global", "--add", "safe.directory", repo_dir], check=False)
    proc = run_cmd(["git", "rev-parse", "--verify", "HEAD^{commit}"], cwd=repo_dir, check=False)
    sha = (proc.stdout or "").strip()
    if proc.returncode != 0 or not sha:
        stderr = (proc.stderr or "").strip()
        msg = f"mounted workspace at '{repo_dir}' is not a valid git commit"
        if stderr:
            msg += f": {stderr}"
        raise ReviewPreparationError("validate", msg)

    if target.head_branch:
        branch_proc = run_cmd(["git", "symbolic-ref", "--short", "-q", "HEAD"], cwd=repo_dir, check=False)
        current_branch = branch_proc.stdout.strip() if branch_proc.returncode == 0 else ""
        if not current_branch and not target.is_pr:
            outcome = reattach_branch(repo_dir, target.head_branch, run=_run_git_cmd)
            if outcome.status is not ReattachStatus.REATTACHED:
                raise ReviewPreparationError("validate", f"mounted workspace is on a detached HEAD: {outcome.message}")
            print(outcome.message, file=sys.stderr)
            current_branch = target.head_branch
        if current_branch and current_branch != target.head_branch:
            raise ReviewPreparationError(
                "validate",
                f"checked-out branch '{current_branch}' does not match target '{target.head_branch}'",
            )

    if target.head_sha and target.head_sha != sha:
        raise ReviewPreparationError(
            "validate",
            f"mounted workspace HEAD {sha} is not the review head {target.head_sha}; "
            "sync the host worktree with the branch before reviewing",
        )
    return dataclasses.replace(target, head_sha=sha)


def _record_preparation_failure(
    repo_dir: str, raw: str, stage: str, detail: str, ledger_file: str | None = None
) -> bool:
    """Append one ACT-REV failure row to ``ledger_file`` or, when unset, to the workspace ledger if its directory
    exists; True when it was written."""
    ledger_dir = os.path.join(repo_dir, "holon-knowledge", "ledger")
    if ledger_file is None and not os.path.isdir(ledger_dir):
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
        ledger_file or os.path.join(ledger_dir, "executions.jsonl"),
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
    ledger_file = _SCRATCH_LEDGER if result_file else None
    if not _record_preparation_failure(repo_dir, raw, error.stage, detail, ledger_file):
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
    """Resolve the target, then validate the mounted workspace in place; raises ``ReviewPreparationError``."""
    target = _resolve_target(repo_dir, raw)
    return _validate_mounted_workspace(repo_dir, target)


_FORBIDDEN_GIT_FLAGS = frozenset({"--force", "-f", "--force-with-lease", "--amend", "rebase"})
_SEVERITY_ORDER = {"clean": 0, "nit": 1, "important": 2, "critical": 3}
#: The resolver stages only its own changes: the host loop's uncommitted ledger rows in the shared worktree stay out.
_RESOLVER_PATHSPEC = ["--", ".", ":(exclude)holon-knowledge/ledger/executions.jsonl", PENDING_EXCLUDE_PATHSPEC]
#: Where a host-dispatched pass (one given --result-file) appends its ledger rows. The host loop owns the canonical
#: ledger of the bind-mounted worktree and records every pass from the result file, so writing there would duplicate.
_SCRATCH_LEDGER = "/tmp/holon-review-executions.jsonl"
_PLANS_LEDGER_REL = "holon-knowledge/ledger/plans.jsonl"


def _assert_safe_git_args(args: list[str]) -> None:
    """Refuse any git command that force-pushes or rewrites history."""
    for token in args:
        clean = token.strip()
        if clean in _FORBIDDEN_GIT_FLAGS or clean.startswith("--force"):
            raise ValueError(f"Forbidden history-rewriting or force-push git argument: {clean}")


def _verify_before_push(repo_dir: str, iteration: int | None = None) -> tuple[bool, bool, str | None]:
    """Run lint, the changed-Markdown Prettier gate and tests before committing or pushing resolver changes.

    A missing ``uv`` is a structured failure naming it (lint and tests unmeasured), never an exception.
    """
    try:
        lock_problem = check_lockfile(repo_dir)
    except LockfileMismatchError as exc:
        return False, False, f"Resolver pre-push verification failed: {_bounded_error_context(str(exc))}"
    if lock_problem:
        return False, False, f"Resolver pre-push verification could not run: {_bounded_error_context(lock_problem)}"
    lint_res = run_cmd(["uv", "run", "task", "lint"], cwd=repo_dir, env=frozen_env(cwd=repo_dir), check=False)
    if lint_res.returncode != 0:
        return False, False, verification_error("lint", lint_res, _bounded_error_context)
    markdown = run_markdown_gate(repo_dir, lambda args: run_cmd(args, cwd=repo_dir, check=False), _RESOLVER_PATHSPEC)
    if markdown.error:
        return False, False, f"Resolver pre-push markdown formatting failed: {_bounded_error_context(markdown.error)}"
    if markdown.formatted:
        logger.info("Resolver formatted Markdown files: %s", ", ".join(markdown.formatted))
    test_env = with_short_summary(frozen_env(cwd=repo_dir))
    test_res = run_cmd(["uv", "run", "task", "test"], cwd=repo_dir, env=test_env, check=False)
    if test_res.returncode != 0:
        return (
            True,
            False,
            verification_error("test", test_res, _bounded_error_context, workspace=repo_dir, iteration=iteration),
        )
    clear_full_output(repo_dir, "test")
    clear_rejected_attempts(repo_dir)
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
    has_ci = any(row.id.startswith("FIND-CI") for row in findings)
    ci_instruction = (
        "- For `FIND-CI` findings, inspect the failing linters or tests, fix the underlying errors, and verify "
        "locally with `uv run task lint` and `uv run task test` before marking `applied`.\n"
        if has_ci
        else ""
    )
    return (
        f"# Holon Stage 4 Resolver Pass (Iteration {iteration})\n\n"
        f"You are operating in `{repo_dir}` on branch `{head_branch}`.\n"
        "Adjudicate EVERY finding below (critical, important, and nit) to exactly one disposition:\n"
        "- `applied`: fix the code in the working tree so `uv run task lint` and `uv run task test` pass.\n"
        f"- `rejected`: provide a one-line `rejection_reason` from: {reasons}. To reject a `critical` or "
        "`important` finding you must also give an `evidence` value naming the code you checked "
        "(`path/to/file.py:LINE` or a test such as `tests/test_x.py::test_name`); `out of scope` alone is not "
        "accepted for them, and a rejection without both stays open.\n"
        "- `open`: a finding you could neither fix nor reject with evidence; it goes to the next iteration.\n"
        f"{ci_instruction}"
        "A `uv.lock` change that no `pyproject.toml` dependency or `requires-python` change explains is a defect: "
        "revert it with `git checkout <base> -- uv.lock`, run `uv` only with `UV_FROZEN=1`, and never "
        "commit an incidental rewrite.\n"
        "A module over its line limit is fixed by splitting it into smaller modules; never edit the frozen module "
        f"budget configuration (`{MODULE_BUDGET_CONFIG}`): the harness reverts any unauthorised change to it.\n"
        "The workspace is the shared flow worktree, so never move HEAD in it: no `git checkout <commit-or-branch>`, "
        "`git switch`, `git reset --hard`, `git stash` or `gh pr checkout` (restoring one file with "
        "`git checkout <rev> -- <path>` is fine); read other revisions with `git show <rev>:<path>`, `git diff` or "
        "`git log`.\n"
        "Never force-push or rewrite git history. Do not run git commit or git push yourself; leave your edits "
        "in the working tree so the harness can verify, attach review trailers, commit, and push. "
        f"Write the JSON list of dispositions to `{dispositions_path}` "
        "with keys `id`, `disposition`, `rejection_reason` and `evidence`.\n\n"
        f"```json\n{json.dumps(serialized, indent=2)}\n```\n"
    )


_USAGE_LIMIT = re.compile(r"session limit|usage limit|rate limit", re.IGNORECASE)


class ResolverTurnError(RuntimeError):
    """The resolver agent turn exited non-zero; carries the bounded, redacted tail of its output."""

    def __init__(self, message: str, output_tail: str = "") -> None:
        super().__init__(message)
        self.output_tail = output_tail


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
    if hasattr(runner, "build_command") and callable(runner.build_command):
        cmd = runner.build_command(prompt, model_name)
    elif hasattr(runner, "compose_cmd") and callable(runner.compose_cmd):
        cmd = runner.compose_cmd(model_name, prompt)
    else:
        raise AttributeError(
            f"Runner for agent '{agent_name}' satisfies neither 'build_command' nor 'compose_cmd' contract"
        )
    # Same turn and stall budgets as the executor and planner: HOLON_TURN_*_SECONDS apply to the resolver too.
    outcome = run_turn(cmd, cwd=repo_dir, turn_budgets=load_budgets(os.environ).turn)
    # A failed turn decides nothing: its exit code is checked before any dispositions file it may have left behind.
    code = getattr(outcome, "returncode", getattr(outcome, "exit_code", 0))
    output = f"{getattr(outcome, 'stdout', '') or ''}\n{getattr(outcome, 'stderr', '') or ''}"
    breach = getattr(outcome, "breach", None)
    if breach is not None:  # A timed-out turn decides nothing, even when the runner exits 0 on SIGTERM.
        raise ResolverTurnError(f"Resolver agent timed out: {breach}", bounded_output_tail(output))
    if code != 0:
        raise ResolverTurnError(f"Resolver agent {_turn_failure_label(code, output)}", bounded_output_tail(output))
    with contextlib.suppress(OSError, ValueError), open(dispositions_path, encoding="utf-8") as f:
        parsed = json.load(f)
        if isinstance(parsed, dict) and isinstance(parsed.get("dispositions"), list):
            return parsed["dispositions"]
        if isinstance(parsed, list):
            return parsed
    raise ResolverTurnError("Resolver agent produced no usable output")


def _turn_failure_label(code: int, output: str) -> str:
    """Name why a resolver turn failed: a usage limit the agent reported, else its exit code."""
    label = f"exited with code {code}"
    return f"{label}: usage limit reached" if _USAGE_LIMIT.search(output) else label


@dataclasses.dataclass(frozen=True)
class CommitOptions:
    """Options governing the resolution commit; the host pushes the committed head after the container returns."""

    dry_run: bool = False
    pre_resolver_head: str | None = None
    intent_data: dict[str, Any] = dataclasses.field(default_factory=dict)


def _commit_resolution(
    repo_dir: str,
    head_branch: str,
    iteration: int,
    dispositions: tuple[FindingDisposition, ...],
    options: CommitOptions | None = None,
    **kwargs: Any,
) -> tuple[tuple[FindingDisposition, ...], str, bool, bool, str | None]:
    """Verify via lint and test, and commit applied fixes with finding IDs; the host pushes the new head."""
    opts = options or CommitOptions()
    pre_resolver_head: str | None = kwargs.get("pre_resolver_head", opts.pre_resolver_head)
    current_head = (_checked(["git", "rev-parse", "HEAD"], repo_dir, "resolve").stdout or "").strip()
    if pre_resolver_head and current_head != pre_resolver_head:
        reset_cmd = ["git", "reset", "--soft", pre_resolver_head]
        _assert_safe_git_args(reset_cmd)
        run_cmd(reset_cmd, cwd=repo_dir, check=True)
    initial_head = pre_resolver_head or current_head
    budget_refusal = None if opts.dry_run else _restore_budget_config(repo_dir, opts.intent_data)
    status_proc = run_cmd(["git", "status", "--porcelain", *_RESOLVER_PATHSPEC], cwd=repo_dir, check=False)
    has_worktree_changes = bool((status_proc.stdout or "").strip())
    applied_items = [d for d in dispositions if d.disposition == "applied"]

    if not applied_items or not has_worktree_changes:
        return dispositions, initial_head, False, False, budget_refusal

    if opts.dry_run:
        return dispositions, initial_head, False, False, None

    lint_ok, tests_ok, verify_err = _verify_before_push(repo_dir, iteration)
    # A verification failure, a missing verification tool included, never stages, commits or pushes anything.
    if not lint_ok or not tests_ok:
        return dispositions, initial_head, lint_ok, tests_ok, verify_err

    run_cmd(["git", "add", "-A", *_RESOLVER_PATHSPEC], cwd=repo_dir, check=True)
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
    commit_res = run_cmd(commit_cmd, cwd=repo_dir, env=review_git_env(os.environ), check=False)
    if commit_res.returncode != 0:
        err = _bounded_error_context((commit_res.stderr or commit_res.stdout or "").strip() or "git commit failed")
        return dispositions, initial_head, True, True, f"Resolver commit failed: {err}"

    new_head = (_checked(["git", "rev-parse", "HEAD"], repo_dir, "resolve").stdout or "").strip()
    updated = tuple(
        dataclasses.replace(d, commit_sha=new_head) if d.disposition == "applied" else d for d in dispositions
    )

    return updated, new_head, True, True, None


def _intent_row(repo_dir: str, head_branch: str) -> dict[str, Any]:
    """Return the last ``intents.jsonl`` row whose branch is ``head_branch`` or a ``/``-bounded prefix of it, or {}."""
    intent_data: dict[str, Any] = {}
    try:
        for d in read_merged_rows(os.path.join(repo_dir, "holon-knowledge/ledger/intents.jsonl")):
            b = d.get("branch", "")
            if b and (head_branch == b or head_branch.startswith(f"{b}/")):
                intent_data = d
    except Exception as exc:
        logger.debug("Non-fatal error reading intents ledger: %s", exc)
    return intent_data


def _restore_budget_config(repo_dir: str, intent_data: dict[str, Any]) -> str | None:
    """Undo an unauthorised change to the frozen module budget configuration; return the refusal, or ``None``.

    Only the one configuration path is restored from ``HEAD`` (removed when ``HEAD`` has none); every other change of
    the resolver stays in the working tree.
    """
    if module_budget_change_authorised(intent_data):
        return None
    status = run_cmd(["git", "status", "--porcelain", "--", MODULE_BUDGET_CONFIG], cwd=repo_dir, check=False)
    if not (status.stdout or "").strip():
        return None
    tracked = run_cmd(["git", "cat-file", "-e", f"HEAD:{MODULE_BUDGET_CONFIG}"], cwd=repo_dir, check=False)
    if tracked.returncode == 0:
        restore = ["git", "restore", "--source=HEAD", "--staged", "--worktree", "--", MODULE_BUDGET_CONFIG]
        run_cmd(restore, cwd=repo_dir, check=True)
    else:
        run_cmd(
            ["git", "rm", "-q", "--cached", "--ignore-unmatch", "--", MODULE_BUDGET_CONFIG], cwd=repo_dir, check=True
        )
        with contextlib.suppress(FileNotFoundError):
            os.remove(os.path.join(repo_dir, MODULE_BUDGET_CONFIG))
    refusal = refusal_message()
    _log_stderr(f"Resolver: {refusal}")
    return refusal


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
    output_tail = ""
    raw_dispositions: list[dict[str, Any]] = []
    pre_resolver_head = (_checked(["git", "rev-parse", "HEAD"], repo_dir, "resolve").stdout or "").strip()
    intent_data = _intent_row(repo_dir, head_branch)
    with_module_budget_flag(os.environ, intent_data)
    if findings and not args.dry_run:
        prompt = _build_resolver_prompt(repo_dir, head_branch, args.iteration, findings, dispositions_path)
        prompt += previous_failure_section(repo_dir, pre_resolver_head)
        label = f"resolver turn (iteration {args.iteration})"
        try:
            with head_guard(repo_dir, head_branch, label, _log_stderr, run=_run_git_cmd) as guard:
                raw_dispositions = _run_resolver_agent(repo_dir, agent_name, model_name, prompt, dispositions_path)
        except Exception as exc:
            error = f"Resolver turn failed: {_bounded_error_context(str(exc))}"
            output_tail = getattr(exc, "output_tail", "")
        finally:
            if os.path.exists(dispositions_path):
                with contextlib.suppress(OSError):
                    os.remove(dispositions_path)
        if error is None and guard.refused and guard.outcome:  # a commit off the branch would never be pushed
            error = f"Resolver turn moved HEAD off '{head_branch}': {guard.outcome.message}"

    dispositions = deserialize_finding_dispositions(raw_dispositions, findings, action_ids)
    head_sha = pre_resolver_head
    lint_ok = False
    tests_ok = False
    try:
        if error is None and findings:
            dispositions, head_sha, lint_ok, tests_ok, error = _commit_resolution(
                repo_dir,
                head_branch,
                args.iteration,
                dispositions,
                CommitOptions(
                    dry_run=args.dry_run,
                    pre_resolver_head=pre_resolver_head,
                    intent_data=intent_data,
                ),
                pre_resolver_head=pre_resolver_head,
            )
    except Exception as exc:
        # Whatever escapes verification or commit, the host still gets a result with the error preserved.
        error = f"Resolver verification or commit failed: {_bounded_error_context(str(exc))}"
    exit_code = 0 if error is None else 1
    rejected = load_rejected_attempt(repo_dir, args.iteration) if findings and not tests_ok and lint_ok else None

    outcome = {
        RESOLVER_RESULT_MARKER: True,
        "target": args.target,
        "head_branch": head_branch,
        "iteration": args.iteration,
        "dispositions": [serialize_finding_disposition(item) for item in dispositions],
        "head_sha": head_sha,
        "lint_passed": lint_ok,
        "tests_passed": tests_ok,
        "error": error,
        "exit_code": exit_code,
        "output_tail": bounded_output_tail(output_tail or error),
        "rejected_patch": rejected.patch_path if rejected else None,
        "rejected_patch_text": read_saved_patch(repo_dir, rejected) if rejected else "",
        "failed_tests": list(rejected.failed_tests) if rejected else [],
        "test_summary": rejected.summary if rejected else None,
    }
    if args.result_file:
        try:
            with open(args.result_file, "w", encoding="utf-8") as f:
                json.dump(outcome, f, indent=2)
        except OSError as exc:
            print(f"Warning: could not write result file '{args.result_file}': {exc}", file=sys.stderr)
    if args.json:
        print(json.dumps(outcome, indent=2))
    sys.exit(exit_code)


def main() -> None:
    """Run the review loop; its git identity reaches only the resolver commit, never the process environment."""
    _run_main()


def _run_main() -> None:  # noqa: C901, PLR0915
    """Parse CLI arguments and execute containerised review loop."""
    parser = build_parser()
    args = parser.parse_args()
    if args.iteration < 1:
        parser.error(f"--iteration must be at least 1, got {args.iteration}.")

    # The host CLI always passes resolved values; no harness detection inside the container.
    agent_name = args.agent or args.pos_agent or FALLBACK_AGENT
    model_name = args.model or args.pos_model or default_model_for(agent_name)
    repo_dir = args.target_worktree or get_workspace_dir()
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

    # Proactive conflict detection and resolution against target upstream branch
    target_branch = "main"
    check_and_resolve_stage_conflicts(repo_dir=repo_dir, target_branch=target_branch, stage_name="review")

    if args.resolve:
        _run_resolver_mode(args, repo_dir, head_branch, agent_name, model_name)
        return

    # Read intent and plan metadata from ledger if available
    intent_data = _intent_row(repo_dir, head_branch)
    with_module_budget_flag(os.environ, intent_data)
    plan_branch: str | None = None

    try:
        # Keyed on the intent and plan id together, never the plan id alone.
        if any(is_row_of_plan(d, head_branch) for d in read_merged_rows(os.path.join(repo_dir, _PLANS_LEDGER_REL))):
            plan_branch = plan_branch_of(head_branch) + INTENT_SUFFIX
    except Exception as exc:
        logger.debug("Non-fatal error reading plans ledger: %s", exc)

    review_scope_paths = [p.strip() for raw in (args.review_paths or []) for p in raw.split(",") if p.strip()]
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
        review_scope_paths=review_scope_paths,
    )
    _load_delta_request(context, repo_dir, args.delta_request)

    # A reviewer turn runs in the mounted worktree; the guard names and repairs one that leaves HEAD off the branch.
    with head_guard(
        repo_dir, head_branch, f"reviewer ensemble (iteration {args.iteration})", _log_stderr, _run_git_cmd
    ):
        loop_res = run_review_loop(
            context=context,
            target=loop_target,
            max_iterations=args.max_iterations,
            ensemble_size=args.ensemble_size,
            auto_pr=args.auto_pr,
            dry_run=args.dry_run,
            skip_post=args.skip_post,
            first_iteration=args.iteration,
            ledger_file=_SCRATCH_LEDGER if args.result_file else None,
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
            print(
                "[REVIEW APPROVED] Review Loop reached consensus approval; "
                "the PR is ready for the maintainer to merge (the flow never merges to main)."
            )
            print()

    sys.exit(0 if approved else 1)


if __name__ == "__main__":
    main()
