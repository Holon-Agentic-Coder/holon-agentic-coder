"""The mergeability gate in front of an approved consensus post and in front of Stage 5.

The loop probes mergeability once per iteration, so the target branch can move before the post or Stage 5. A head
probed ``CONFLICTING`` or ``DIRTY`` (``UNKNOWN`` and ``MISSING_REF`` keep the loop's handling) is never approved or
calibrated as it stands: the target branch is merged first, in a temporary detached worktree on ``origin/<branch>``.
Ledger-only conflicts are resolved without a test run (target rows stay a byte prefix, head-only rows follow in
``created_at`` order) and pushed; the approval must then be re-earned. A code conflict aborts the merge and blocks.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.flow import stage_conflicts
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.host_remote import push_commit
from sandbox_executor.flow.ledger_reconcile import reconcile_ledger_file
from sandbox_executor.git import run_git
from sandbox_executor.ledger import LedgerRewriteError, generate_action_id
from sandbox_executor.redaction import redact_agent_secrets

MergeabilityProbe = Callable[..., dict[str, Any]]
MERGED_REASON = "target branch merged; approval must be re-earned on the new head"


@dataclass(frozen=True)
class _MergeOutcome:
    """How merging the target branch went: ``ledger_only`` when only ledgers conflicted; tests are never run."""

    merged: bool
    ledger_only: bool = False
    conflicting_paths: tuple[str, ...] = ()
    error: str = ""


@dataclass(frozen=True)
class _ConflictGateResult:
    """The gate's decision: ``blocked_reason`` is None only when the head may be approved or calibrated."""

    conflicting: bool
    merged: bool = False
    ledger_only: bool = False
    blocked_reason: str | None = None


def _is_conflicting(mergeability: Mapping[str, Any] | None) -> bool:
    """True for an explicit ``CONFLICTING`` or ``DIRTY`` probe; ``UNKNOWN`` and ``MISSING_REF`` are not conflicts."""
    probe = mergeability or {}
    state = str(probe.get("merge_state_status") or "").upper()
    if state == "MISSING_REF":
        return False
    return str(probe.get("mergeable") or "").upper() == "CONFLICTING" or state == "DIRTY" or bool(probe.get("is_dirty"))


def _is_ledger_path(path: str) -> bool:
    """True for an append-only JSONL ledger under ``holon-knowledge/ledger/``."""
    return path.startswith("holon-knowledge/ledger/") and path.endswith(".jsonl")


def _merge_target_ledger_only(worktree: str, target_ref: str) -> _MergeOutcome:
    """Merge ``target_ref`` into the worktree's HEAD; resolve a ledger-only conflict, abort on any code conflict.

    The ledgers are resolved with the target side (``theirs`` in a merge) as the byte prefix. No test command runs.
    """
    from sandbox_executor.flow.stages.review import REVIEW_GIT_ENV  # Lazy: stages.review imports the review loop.

    env = {**os.environ, **REVIEW_GIT_ENV}
    merge = run_git(["merge", "--no-ff", "--no-edit", target_ref], cwd=worktree, check=False, env=env)
    if merge.returncode == 0:
        return _MergeOutcome(merged=True)
    unmerged = run_git(["diff", "--name-only", "--diff-filter=U"], cwd=worktree, check=False).stdout or ""
    paths = tuple(line.strip() for line in unmerged.splitlines() if line.strip())
    if not paths or not all(_is_ledger_path(path) for path in paths):
        run_git(["merge", "--abort"], cwd=worktree, check=False)
        error = "" if paths else redact_agent_secrets((merge.stderr or merge.stdout or "").strip())
        return _MergeOutcome(merged=False, conflicting_paths=paths, error=error)
    for path in paths:
        if not reconcile_ledger_file(os.path.join(worktree, path), prefix_side="theirs"):
            run_git(["merge", "--abort"], cwd=worktree, check=False)
            return _MergeOutcome(merged=False, conflicting_paths=paths, error=f"could not reconcile {path}")
        run_git(["add", path], cwd=worktree)
    run_git(["commit", "--no-edit"], cwd=worktree, env=env)
    return _MergeOutcome(merged=True, ledger_only=True, conflicting_paths=paths)


def _merge_and_push(repo_dir: str, branch: str, target_branch: str) -> _MergeOutcome:
    """Merge ``origin/<target_branch>`` into ``origin/<branch>`` in a temporary worktree and push the result."""
    tmp_root = tempfile.mkdtemp(prefix="holon-conflict-gate-")
    worktree = os.path.join(tmp_root, "worktree")
    try:
        run_git(["fetch", "origin", target_branch, branch], cwd=repo_dir, check=False, timeout=300)
        run_git(["worktree", "add", "--detach", worktree, f"refs/remotes/origin/{branch}"], cwd=repo_dir)
        outcome = _merge_target_ledger_only(worktree, f"refs/remotes/origin/{target_branch}")
        if outcome.merged:
            merged_sha = run_git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip()
            push_commit(repo_dir, merged_sha, branch)
            run_git(["update-ref", f"refs/remotes/origin/{branch}", merged_sha], cwd=repo_dir, check=False)
        return outcome
    except LedgerRewriteError:
        raise
    except (RuntimeError, TimeoutError, OSError, ValueError) as exc:
        return _MergeOutcome(merged=False, error=redact_agent_secrets(str(exc)))
    finally:
        run_git(["worktree", "remove", "--force", worktree], cwd=repo_dir, check=False)
        run_git(["worktree", "prune"], cwd=repo_dir, check=False)
        shutil.rmtree(tmp_root, ignore_errors=True)


def _blocked_reason(outcome: _MergeOutcome, target_branch: str) -> str:
    """Why the head stays blocked after the merge attempt."""
    if outcome.merged:
        return MERGED_REASON
    if outcome.conflicting_paths:
        return f"code conflicts with {target_branch} in: {', '.join(outcome.conflicting_paths)}"
    return f"merging {target_branch} failed: {outcome.error or 'unknown error'}"


def _gate_conflicting_head(
    context: FlowContext, branch: str | None, target_branch: str, mergeability: Mapping[str, Any], merge: bool = True
) -> _ConflictGateResult:
    """Decide on a probed head: pass a clean one, otherwise merge the target branch (when ``merge``) and block."""
    if not _is_conflicting(mergeability):
        return _ConflictGateResult(conflicting=False)
    if not merge or not branch:
        why = "dry run, nothing merged" if branch else "the head branch is unknown, nothing merged"
        reason = f"the head conflicts with {target_branch} ({why})"
        context.log(f"[Review Loop] Conflict gate: {reason}.")
        return _ConflictGateResult(conflicting=True, blocked_reason=reason)
    outcome = _merge_and_push(context.target_worktree, branch, target_branch)
    reason = _blocked_reason(outcome, target_branch)
    context.log(f"[Review Loop] Conflict gate on '{branch}': {reason} (no test suite was run).")
    return _ConflictGateResult(True, outcome.merged, outcome.ledger_only, reason)


def _gate_record(review_target: str, gate: _ConflictGateResult) -> dict[str, Any]:
    """The ACT row recording a blocked approval; its ``error_context`` is redacted."""
    return {
        "action_id": generate_action_id("ACT-GATE"),
        "execution_id": f"REV-GATE-{int(datetime.now(UTC).timestamp())}",
        "action": f"Approved consensus not posted for {review_target}: the head conflicts with the target branch",
        "category": "code",
        "target": review_target,
        "outcome": "failure",
        "entry_type": "action",
        "action_origin": "pr_review",
        "labels": ["review", "conflict-gate"],
        "error_context": redact_agent_secrets(str(gate.blocked_reason)),
        "created_at": datetime.now(UTC).isoformat(),
    }


def _approved_post_gate(
    context: FlowContext,
    exec_file: str,
    review_target: str,
    consensus_data: Mapping[str, Any],
    probe: MergeabilityProbe,
) -> tuple[str, dict[str, Any]] | None:
    """Gate an approved post: None lets it proceed, else the post error and the ``posting_res`` to return.

    ``probe`` is the loop's mergeability probe already bound to the repository and the review target.
    """
    from sandbox_executor.flow.review.records import record_review_action  # Lazy: records imports review_report.

    target_branch = str(consensus_data.get("base_branch") or "main")
    mergeability = probe(target_branch=target_branch)
    branch = context.execution_branch or (review_target if not review_target.isdigit() else None)
    gate = _gate_conflicting_head(context, branch, target_branch, mergeability)
    if gate.blocked_reason is None:
        return None
    record_review_action(context, exec_file, _gate_record(review_target, gate))
    return f"conflict gate: {gate.blocked_reason}", {"posted": False, "conflict_gate": asdict(gate)}


def _stage5_conflict_gate(context: FlowContext) -> StageResult | None:
    """Fail Stage 5 on a conflicting execution head; None lets it start.

    Once the target branch is merged and pushed, Stage 5 proceeds on the new head (its sync levels the local ref with
    origin). A dry run probes without merging.
    """
    if not context.execution_branch:
        return None
    target_branch = str(context.intent_data.get("target_branch") or "main")
    start_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    mergeability = stage_conflicts.check_pr_mergeability(
        context.target_worktree, branch=context.execution_branch, target_branch=target_branch
    )
    gate = _gate_conflicting_head(context, context.execution_branch, target_branch, mergeability, not context.dry_run)
    if gate.blocked_reason is None or gate.merged:
        return None
    reason = f"Calibrate stage refused: {gate.blocked_reason}"
    return _stage_failure(context, FlowStage.CALIBRATE, start_time, reason, conflict_gate=asdict(gate))
