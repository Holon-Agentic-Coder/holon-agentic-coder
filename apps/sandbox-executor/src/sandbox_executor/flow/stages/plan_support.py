from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from typing import Any

from sandbox_executor.budgets import (
    ENV_REPLAN_FEEDBACK,
    ENV_REPLAN_FEEDBACK_FILE,
    REPLAN_FEEDBACK_INLINE_MAX_BYTES,
)
from sandbox_executor.entrypoint.planner import load_metrics_config, parse_metrics
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult
from sandbox_executor.flow.stages.stage_support import stage_success
from sandbox_executor.launcher import env_files
from sandbox_executor.plan_paths import plan_file_rel

_ROW_METRIC_KEYS = ("p_success", "entropy", "impact", "cost", "learning_value", "ev")
_EV_INPUT_KEYS = ("p_success", "impact", "learning_value", "entropy", "cost")


def plan_id_of(plan_branch: str) -> str:
    return plan_branch.removesuffix("/_").rsplit("/", 1)[-1]


def plan_stage_payload(
    context: FlowContext,
    plan_branch: str,
    plan_file: str,
    metrics: dict[str, Any],
    consulted_ids: list[str],
) -> dict[str, Any]:
    ev_text = f"{metrics['ev']:.2f}" if isinstance(metrics.get("ev"), int | float) else "n/a"
    context.log(f"Stage 2 completed: planner agent produced '{plan_file}' on '{plan_branch}' (EV={ev_text}).")
    return {
        "plan_id": plan_id_of(plan_branch),
        "plan_branch": plan_branch,
        "plan_file": plan_file,
        "metrics": metrics,
        "consulted_knowledge_ids": consulted_ids,
    }


def dry_run_plan_result(
    context: FlowContext, start_time: str, plan_branch: str, consulted_ids: list[str]
) -> StageResult:
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


def _restore_env_var(key: str, previous: str | None) -> None:
    if previous is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = previous


@contextlib.contextmanager
def replan_feedback_env(feedback: str, target_merged_notice: str = "") -> Iterator[None]:
    # why: reaches docker run and planner/executor container via env or file, never a ledger; restored on exit.
    combined = "\n\n".join(part for part in (feedback, target_merged_notice) if part)
    if not combined:
        yield
        return
    previous_inline = os.environ.get(ENV_REPLAN_FEEDBACK)
    previous_file = os.environ.get(ENV_REPLAN_FEEDBACK_FILE)
    temp_path: str | None = None
    if len(combined.encode("utf-8")) > REPLAN_FEEDBACK_INLINE_MAX_BYTES:
        temp_path = env_files.write_replan_feedback_file(combined)
        os.environ[ENV_REPLAN_FEEDBACK_FILE] = temp_path
        os.environ.pop(ENV_REPLAN_FEEDBACK, None)
    else:
        os.environ[ENV_REPLAN_FEEDBACK] = combined
        os.environ.pop(ENV_REPLAN_FEEDBACK_FILE, None)
    try:
        yield
    finally:
        _restore_env_var(ENV_REPLAN_FEEDBACK, previous_inline)
        _restore_env_var(ENV_REPLAN_FEEDBACK_FILE, previous_file)
        if temp_path is not None:
            with contextlib.suppress(FileNotFoundError):
                os.remove(temp_path)
