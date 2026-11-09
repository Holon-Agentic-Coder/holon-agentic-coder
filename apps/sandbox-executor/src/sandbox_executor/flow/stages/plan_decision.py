"""Stage 2/3 boundary: the plan row's recorded decomposition decision.

The planner container records the decision on the plan row it appends (``decomposition_decision``, ``entropy``,
``entropy_budget`` and, when decomposed, ``sub_intents``; see ``sandbox_executor.decomposition_rule``). Stage 3 reads
that record before dispatching the executor and never re-decides it. Only a row written by an older planner image,
which lacks the record, is decided here with the same rule and back-filled once: one superseding plan row is appended
to the plan branch, so the branch is self-describing and the second read finds the record.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.decomposition_rule import DecompositionDecision, InvalidPlanDecisionError, should_decompose
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.git_refs import (
    EXECUTOR_GIT_ENV,
    _git_show_text,
    _read_ledger_rows,
    _resolve_branch_ref,
    _sync_local_execution_refs,
)
from sandbox_executor.flow.stages.plan import _format_entropy
from sandbox_executor.git import run_git
from sandbox_executor.ledger import LedgerRewriteError, atomic_ledger_append, validate_ledger_file_unique_ids
from sandbox_executor.redaction import redact_agent_secrets

PLANS_LEDGER_REL = "holon-knowledge/ledger/plans.jsonl"


class PlanDecisionError(RuntimeError):
    """The plan row records an invalid decomposition decision; Stage 3 fails before dispatch instead of re-deciding."""


def _plan_budget_decision(context: FlowContext) -> DecompositionDecision | None:
    """Return the decomposition decision the active plan row records, before the executor is dispatched.

    Reads the newest matching plan row from the plan branch (origin first, then local). A recorded decision is returned
    as is. A row from an older planner image is decided with ``should_decompose`` on the plan file and the intent
    payload, and that decision is back-filled as one superseding plan row (see :func:`_backfill_plan_decision`).
    Returns ``None`` when the plan row cannot be read, leaving the decision to the executor container.

    Raises:
        PlanDecisionError: The plan row records a decision ``should_decompose`` could not have produced.
    """
    if not context.plan_branch:
        return None
    plan_ref = _resolve_branch_ref(context.target_worktree, context.plan_branch)
    if not plan_ref:
        return None
    plan_id = context.plan_branch.removesuffix("/_").rsplit("/", 1)[-1]
    rows = [
        r for r in _read_ledger_rows(context.target_worktree, plan_ref, "plans.jsonl") if r.get("plan_id") == plan_id
    ]
    if not rows:
        return None
    plan_row = rows[-1]
    try:
        recorded = DecompositionDecision.from_plan_row(plan_row)
    except InvalidPlanDecisionError as exc:
        raise PlanDecisionError(
            f"Execute stage failed: the plan row of {plan_id} records an invalid decomposition decision: {exc}"
        ) from exc
    if recorded is not None:
        return recorded
    plan_md_rel = str(plan_row.get("plan_file") or f"plans/{plan_id}.md")
    if not re.fullmatch(r"plans/P-[^/]+\.md", plan_md_rel):
        plan_md_rel = f"plans/{plan_id}.md"
    plan_content = _git_show_text(context.target_worktree, plan_ref, plan_md_rel) or ""
    decision = should_decompose(plan_row, plan_content, context.intent_data)
    _backfill_plan_decision(context, plan_row, decision)
    return decision


def _backfill_plan_decision(context: FlowContext, plan_row: dict[str, Any], decision: DecompositionDecision) -> None:
    """Append one superseding plan row that records ``decision`` for a plan row from an older planner image.

    The superseding row is the planner's row plus the decision fields, ``ledger_revision`` + 1, a fresh
    ``created_at`` and ``decision_recorded_by: "flow"``; the planner's row is never rewritten. A failed write is only
    logged: the decision is still the one rule applied to the same inputs.
    """
    try:
        prior_revision = int(plan_row.get("ledger_revision") or 1)
    except (TypeError, ValueError):
        prior_revision = 1
    superseding = {
        **plan_row,
        **decision.to_plan_fields(),
        "ledger_revision": prior_revision + 1,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "decision_recorded_by": "flow",
    }
    plan_branch = str(context.plan_branch)
    write_error = _append_superseding_plan_row(context, plan_branch, superseding)
    if write_error:
        context.log(f"Warning: could not record the decomposition decision on '{plan_branch}': {write_error}")
    else:
        context.log(f"Recorded the decomposition decision '{decision.decision}' on '{plan_branch}'.")


def _append_superseding_plan_row(context: FlowContext, plan_branch: str, row: dict[str, Any]) -> str | None:
    """Append a superseding plans.jsonl row on the plan branch and push it to that branch only.

    Works in a temporary detached worktree on ``refs/remotes/origin/<plan_branch>`` so the operator's checkout is
    never disturbed; the append is guarded by ``atomic_ledger_append`` (earlier bytes must be unchanged) and the
    plan-id uniqueness check. After the push, origin/<branch> and the local branch are fast-forwarded to the pushed
    commit.

    Returns:
        str | None: None on success, otherwise a secret-redacted description of what failed.
    """
    repo_dir = context.target_worktree
    tmp_root = tempfile.mkdtemp(prefix="holon-flow-plan-decision-")
    worktree = os.path.join(tmp_root, "worktree")
    try:
        run_git(["worktree", "add", "--detach", worktree, f"refs/remotes/origin/{plan_branch}"], cwd=repo_dir)
        ledger_path = os.path.join(worktree, PLANS_LEDGER_REL)
        os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
        with atomic_ledger_append(ledger_path) as f:
            f.write(json.dumps(row) + "\n")
        validate_ledger_file_unique_ids(ledger_path, id_key="plan_id", parent_key="intent_branch")
        run_git(["add", PLANS_LEDGER_REL], cwd=worktree)
        commit_env = os.environ.copy()
        commit_env["DOCKER_CLI_HINTS"] = "false"
        for key, value in EXECUTOR_GIT_ENV.items():
            commit_env.setdefault(key, value)
        run_git(
            ["commit", "-m", f"plan: record the decomposition decision of {row.get('plan_id')}"],
            cwd=worktree,
            env=commit_env,
        )
        pushed_sha = run_git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip()
        run_git(["push", "origin", f"HEAD:refs/heads/{plan_branch}"], cwd=worktree, timeout=300)
    except LedgerRewriteError:
        raise
    except (RuntimeError, TimeoutError, OSError, ValueError) as exc:
        return redact_agent_secrets(str(exc))
    finally:
        run_git(["worktree", "remove", "--force", worktree], cwd=repo_dir, check=False)
        run_git(["worktree", "prune"], cwd=repo_dir, check=False)
        shutil.rmtree(tmp_root, ignore_errors=True)
    # The row is on origin now: aligning local refs is best-effort and never reported as a failed write.
    _sync_local_execution_refs(context, plan_branch, pushed_sha)
    return None


def _decomposition_record(context: FlowContext, decision: DecompositionDecision) -> dict[str, Any]:
    """Return the plan row's decision record in the shape ``_run_decomposed_plan`` reads (its contract is unchanged)."""
    return {
        "sub_intents": decision.sub_intents,
        "entropy_budget": decision.entropy_budget,
        "plan_branch": context.plan_branch,
    }


def _over_budget_failure(
    context: FlowContext, start_time: str, entropy: Any, entropy_budget: Any, *, dispatched: bool, **payload: Any
) -> StageResult:
    """Fail Stage 3 for a plan above its entropy budget that lists no sub-intent list (re-planned by the engine)."""
    payload.update(
        {"over_budget": True, "entropy": entropy, "entropy_budget": entropy_budget, "dispatched": dispatched}
    )
    return _stage_failure(
        context,
        FlowStage.EXECUTE,
        start_time,
        f"Execute stage failed: plan entropy {_format_entropy(entropy)} exceeds the entropy budget "
        f"{_format_entropy(entropy_budget)} and the plan lists no sub-intent list.",
        **payload,
    )
