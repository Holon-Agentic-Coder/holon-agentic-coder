"""Stage 2 (Plan Generation): dispatch the planner and adopt its plan, and the entropy budget decision
that Stage 3 applies.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.budgets import ENV_REPLAN_FEEDBACK
from sandbox_executor.entrypoint.executor import DecompositionDecision, should_decompose
from sandbox_executor.entrypoint.planner import _sanitize_model_name, load_metrics_config, parse_metrics
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.dispatch import _dispatch_role, _stage_failure
from sandbox_executor.flow.git_refs import (
    _discover_child_branch,
    _fetch_branch_prefix_error,
    _git_show_text,
    _read_ledger_rows,
    _resolve_branch_ref,
    _snapshot_child_branches,
    _track_local_branch,
)
from sandbox_executor.flow.stage_conflicts import check_and_resolve_stage_conflicts
from sandbox_executor.flow_guard import SKIP_PUSH_DISPATCH_ERROR
from sandbox_executor.knowledge import ConsultEngine
from sandbox_executor.ledger import build_plan_id

logger = logging.getLogger(__name__)


def run_plan_stage(context: FlowContext) -> StageResult:  # noqa: C901, PLR0911, PLR0912, PLR0915
    """Execute Stage 2: dispatch the planner agent and adopt the plan branch it pushed.

    The planner container (the same one 'holon plan' launches) authors plans/P-*.md, appends
    plans.jsonl and pushes. The flow never writes a plan itself: if no planner produced a plan file,
    the stage fails. Downstream stages operate unattended and never stall on interactive questions.
    """
    start_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    context.log("Starting Stage 2: Plan Generation")

    if not context.intent_branch:
        err = "Cannot run Plan stage without an active intent_branch in FlowContext."
        context.log(err)
        return StageResult(stage=FlowStage.PLAN, status=StageStatus.FAILED, start_time=start_time, error=err)

    intent_prefix = context.intent_branch.removesuffix("/_").rstrip("/")
    safe_model = _sanitize_model_name(context.model)
    repo_dir = context.target_worktree

    # Pre-planning wisdom consult
    consulted_ids: list[str] = []
    try:
        db_path = os.path.join(repo_dir, "holon-knowledge/.index/action_index.db")
        consult_engine = ConsultEngine(project_db_path=db_path)
        intent_labels = context.intent_data.get("labels", [])
        if isinstance(intent_labels, str):
            intent_labels = [lbl.strip() for lbl in intent_labels.split(",") if lbl.strip()]
        consult_res = consult_engine.consult_records(any_labels=intent_labels, top_k=6, max_tokens=1200)
        consulted_ids = consult_res.consulted_ids
        if consulted_ids:
            context.log(f"Consulted {len(consulted_ids)} knowledge records for plan: {consulted_ids}")
    except Exception as e:
        logger.debug("Non-fatal consult failure during plan stage: %s", e)

    if context.dry_run:
        # Validation only: name the branch the planner would create, without dispatching or writing anything.
        plan_id = build_plan_id(int(time.time()), intent_prefix, context.agent, safe_model)
        plan_branch = f"{intent_prefix}/{plan_id}/_"
        context.plan_branch = plan_branch
        context.log(f"Stage 2 (dry run): planner dispatch skipped; plan branch would be '{plan_branch}'.")
        result = StageResult(
            stage=FlowStage.PLAN,
            status=StageStatus.SUCCESS,
            start_time=start_time,
            end_time=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            payload={
                "plan_id": plan_id,
                "plan_branch": plan_branch,
                "plan_file": f"plans/{plan_id}.md",
                "metrics": {},
                "consulted_knowledge_ids": consulted_ids,
                "dry_run": True,
            },
        )
        context.stage_results["plan"] = result
        return result

    no_plan_error = (
        f"Plan stage failed: no planner agent produced plans/P-*.md on a plan branch under "
        f"{context.intent_branch}; refusing to fabricate a plan."
    )
    if context.skip_push:
        return _stage_failure(context, FlowStage.PLAN, start_time, SKIP_PUSH_DISPATCH_ERROR)

    target_branch = context.intent_data.get("target_branch", "main")
    check_and_resolve_stage_conflicts(
        repo_dir, target_branch=target_branch, stage_name="plan", branch=context.intent_branch
    )

    existing_plans = _snapshot_child_branches(repo_dir, intent_prefix, "P")
    if existing_plans is None:
        return _stage_failure(
            context,
            FlowStage.PLAN,
            start_time,
            f"Plan stage failed: could not fetch origin branches under {intent_prefix} before dispatching the planner.",
        )
    replan_feedback = str(context.intent_data.get("replan_feedback") or "").strip()
    previous_feedback_env = os.environ.get(ENV_REPLAN_FEEDBACK)
    if replan_feedback:
        # Forwarded into the planner container like every HOLON_* variable; never written to a ledger.
        os.environ[ENV_REPLAN_FEEDBACK] = replan_feedback
    try:
        exit_code = _dispatch_role(context, "planner", [context.intent_branch, context.agent, context.model])
    finally:
        if replan_feedback:
            if previous_feedback_env is None:
                os.environ.pop(ENV_REPLAN_FEEDBACK, None)
            else:
                os.environ[ENV_REPLAN_FEEDBACK] = previous_feedback_env
    if exit_code != 0:
        return _stage_failure(
            context, FlowStage.PLAN, start_time, f"{no_plan_error} (planner agent exited with code {exit_code})"
        )

    fetch_error = _fetch_branch_prefix_error(repo_dir, intent_prefix)
    if fetch_error is not None:
        return _stage_failure(
            context,
            FlowStage.PLAN,
            start_time,
            f"Plan stage failed: the planner agent exited 0 but origin branches under {intent_prefix} "
            f"could not be fetched to adopt its plan branch: {fetch_error}",
        )
    plan_branch, ambiguity = _discover_child_branch(
        repo_dir,
        intent_prefix,
        "P",
        before=existing_plans,
        preferred_suffix=f"-{context.agent}-{safe_model}",
    )
    if ambiguity:
        return _stage_failure(context, FlowStage.PLAN, start_time, f"Plan stage failed: {ambiguity}.")
    if not plan_branch:
        return _stage_failure(context, FlowStage.PLAN, start_time, no_plan_error)

    plan_id = plan_branch.removesuffix("/_").rsplit("/", 1)[-1]
    plan_ref = f"refs/remotes/origin/{plan_branch}"
    plan_rows = [r for r in _read_ledger_rows(repo_dir, plan_ref, "plans.jsonl") if r.get("plan_id") == plan_id]
    plan_row = plan_rows[-1] if plan_rows else {}
    plan_md_rel = str(plan_row.get("plan_file") or f"plans/{plan_id}.md")
    if not re.fullmatch(r"plans/P-[^/]+\.md", plan_md_rel):
        plan_md_rel = f"plans/{plan_id}.md"
    plan_content = _git_show_text(repo_dir, plan_ref, plan_md_rel)
    if plan_content is None:
        return _stage_failure(context, FlowStage.PLAN, start_time, no_plan_error, plan_branch=plan_branch)

    # Metrics are read back for the stage payload only; the planner's plans.jsonl row is authoritative.
    metrics: dict[str, Any] = dict(parse_metrics(plan_content))
    for key in ("p_success", "entropy", "impact", "cost", "learning_value", "ev"):
        if isinstance(plan_row.get(key), int | float):
            metrics[key] = float(plan_row[key])
    if "ev" not in metrics and all(k in metrics for k in ("p_success", "impact", "learning_value", "entropy", "cost")):
        ev_cfg = load_metrics_config(repo_dir)
        metrics["ev"] = (
            metrics["p_success"] * metrics["impact"]
            + ev_cfg.get("mu", 0.5) * metrics["learning_value"]
            - ev_cfg.get("lambda", 0.3) * metrics["entropy"]
            - metrics["cost"]
        )

    context.plan_branch = plan_branch
    _track_local_branch(context, plan_branch)

    end_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    ev_text = f"{metrics['ev']:.2f}" if isinstance(metrics.get("ev"), int | float) else "n/a"
    context.log(f"Stage 2 completed: planner agent produced '{plan_md_rel}' on '{plan_branch}' (EV={ev_text}).")
    result = StageResult(
        stage=FlowStage.PLAN,
        status=StageStatus.SUCCESS,
        start_time=start_time,
        end_time=end_time,
        payload={
            "plan_id": plan_id,
            "plan_branch": plan_branch,
            "plan_file": plan_md_rel,
            "metrics": metrics,
            "consulted_knowledge_ids": consulted_ids,
        },
    )
    context.stage_results["plan"] = result
    return result


def _plan_budget_decision(context: FlowContext) -> DecompositionDecision | None:
    """Judge the active plan against its entropy budget before the executor is dispatched.

    Reads the plan row and plan file from the plan branch (origin first, then local) and calls the executor's own
    ``should_decompose`` with the intent payload, so the flow and the container apply one rule. Returns ``None`` when
    the plan row cannot be read, leaving the decision to the executor container.
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
    plan_md_rel = str(plan_row.get("plan_file") or f"plans/{plan_id}.md")
    if not re.fullmatch(r"plans/P-[^/]+\.md", plan_md_rel):
        plan_md_rel = f"plans/{plan_id}.md"
    plan_content = _git_show_text(context.target_worktree, plan_ref, plan_md_rel) or ""
    return should_decompose(plan_row, plan_content, context.intent_data)


def _format_entropy(value: Any) -> str:
    """Render an entropy value for messages, tolerating missing or non-numeric values."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return f"{float(value):g}"
    return str(value) if value is not None else "unknown"


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
