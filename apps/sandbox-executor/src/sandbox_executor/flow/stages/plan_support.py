"""Stage 2 steps that need no patched lookup: the dry-run result, the plan metrics read-back and the replan feedback.

The planner dispatch, the conflict check, the child-branch snapshot and the plan-row read stay in ``stages/plan.py``,
where tests patch them.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from typing import Any

from sandbox_executor.budgets import ENV_REPLAN_FEEDBACK
from sandbox_executor.entrypoint.planner import load_metrics_config, parse_metrics
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.stages.stage_support import stage_success
from sandbox_executor.flow.target_sync_hooks import ENV_TARGET_MERGED_NOTICE
from sandbox_executor.plan_paths import plan_file_rel

_ROW_METRIC_KEYS = ("p_success", "entropy", "impact", "cost", "learning_value", "ev")
_EV_INPUT_KEYS = ("p_success", "impact", "learning_value", "entropy", "cost")


def plan_id_of(plan_branch: str) -> str:
    """The plan id a plan branch ``<intent prefix>/<plan id>/_`` names."""
    return plan_branch.removesuffix("/_").rsplit("/", 1)[-1]


def dry_run_plan_result(
    context: FlowContext, start_time: str, plan_branch: str, consulted_ids: list[str]
) -> StageResult:
    """Record the dry-run Stage 2 result for the branch ``<intent prefix>/<plan id>/_`` the planner would create."""
    plan_id = plan_id_of(plan_branch)
    intent_prefix = plan_branch.removesuffix("/_").rsplit("/", 1)[0]
    context.plan_branch = plan_branch
    context.log(f"Stage 2 (dry run): planner dispatch skipped; plan branch would be '{plan_branch}'.")
    payload = {
        "plan_id": plan_id,
        "plan_branch": plan_branch,
        "plan_file": plan_file_rel(intent_prefix, plan_id),
        "metrics": {},
        "consulted_knowledge_ids": consulted_ids,
        "dry_run": True,
    }
    return stage_success(context, FlowStage.PLAN, "plan", start_time, payload)


def plan_metrics(repo_dir: str, plan_content: str, plan_row: dict[str, Any]) -> dict[str, Any]:
    """The plan's overall metrics for the stage payload: the plan file's table, overridden by the row's numbers.

    Metrics are read back for the stage payload only; the planner's plans.jsonl row is authoritative. Without an
    ``ev`` but with all its inputs, ``ev`` is computed with the repository's ``mu`` and ``lambda``.
    """
    metrics: dict[str, Any] = dict(parse_metrics(plan_content))
    for key in _ROW_METRIC_KEYS:
        if isinstance(plan_row.get(key), int | float):
            metrics[key] = float(plan_row[key])
    if "ev" not in metrics and all(k in metrics for k in _EV_INPUT_KEYS):
        ev_cfg = load_metrics_config(repo_dir)
        metrics["ev"] = (
            metrics["p_success"] * metrics["impact"]
            + ev_cfg.get("mu", 0.5) * metrics["learning_value"]
            - ev_cfg.get("lambda", 0.3) * metrics["entropy"]
            - metrics["cost"]
        )
    return metrics


@contextlib.contextmanager
def replan_feedback_env(feedback: str, target_merged_notice: str = "") -> Iterator[None]:
    """Set the replan feedback and target-merge notice for the planner dispatch when non-empty, then restore.

    Forwarded into the planner container like every HOLON_* variable; never written to a ledger.
    """
    updates = {ENV_REPLAN_FEEDBACK: feedback, ENV_TARGET_MERGED_NOTICE: target_merged_notice}
    previous = {key: os.environ.get(key) for key, val in updates.items() if val}
    for key, val in updates.items():
        if val:
            os.environ[key] = val
    try:
        yield
    finally:
        for key, prev in previous.items():
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev
