"""Stage 2 steps without patched lookups: plan metrics read-back, replan feedback environment and the dry-run result."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sandbox_executor.budgets import ENV_REPLAN_FEEDBACK
from sandbox_executor.flow.context import FlowContext, StageStatus
from sandbox_executor.flow.stages.plan_support import (
    dry_run_plan_result,
    plan_id_of,
    plan_metrics,
    replan_feedback_env,
)

PLAN = """# Plan

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.5   |
| entropy_pred        | 2.0   |
| impact_pred         | 10.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 2.0   |
"""


def test_plan_metrics_compute_ev_with_the_default_weights(tmp_path: Path):
    metrics = plan_metrics(str(tmp_path), PLAN, {})
    assert metrics["ev"] == pytest.approx(0.5 * 10.0 + 0.5 * 2.0 - 0.3 * 2.0 - 1.0)


def test_plan_row_numbers_override_the_plan_file_and_a_recorded_ev_is_kept(tmp_path: Path):
    metrics = plan_metrics(str(tmp_path), PLAN, {"p_success": 1, "ev": 42, "cost": "n/a"})
    assert metrics["p_success"] == 1.0
    assert metrics["cost"] == 1.0
    assert metrics["ev"] == 42.0


def test_plan_metrics_without_all_ev_inputs_have_no_ev(tmp_path: Path):
    assert "ev" not in plan_metrics(str(tmp_path), "# Plan without metrics\n", {"p_success": 0.5})


@pytest.mark.parametrize("previous", [None, "earlier feedback"])
def test_replan_feedback_is_set_for_the_dispatch_and_restored_afterwards(monkeypatch, previous):
    if previous is None:
        monkeypatch.delenv(ENV_REPLAN_FEEDBACK, raising=False)
    else:
        monkeypatch.setenv(ENV_REPLAN_FEEDBACK, previous)
    with replan_feedback_env("split the plan"):
        assert os.environ[ENV_REPLAN_FEEDBACK] == "split the plan"
    assert os.environ.get(ENV_REPLAN_FEEDBACK) == previous


@pytest.mark.parametrize("previous", [None, "earlier feedback"])
def test_replan_feedback_is_restored_when_the_dispatch_raises(monkeypatch, previous):
    if previous is None:
        monkeypatch.delenv(ENV_REPLAN_FEEDBACK, raising=False)
    else:
        monkeypatch.setenv(ENV_REPLAN_FEEDBACK, previous)
    with pytest.raises(RuntimeError), replan_feedback_env("split the plan"):
        raise RuntimeError("dispatch failed")
    assert os.environ.get(ENV_REPLAN_FEEDBACK) == previous


def test_empty_replan_feedback_leaves_the_environment_alone(monkeypatch):
    monkeypatch.setenv(ENV_REPLAN_FEEDBACK, "set by someone else")
    with replan_feedback_env(""):
        os.environ[ENV_REPLAN_FEEDBACK] = "changed during the dispatch"
    assert os.environ[ENV_REPLAN_FEEDBACK] == "changed during the dispatch"


def test_the_dry_run_result_names_the_plan_branch_without_metrics():
    context = FlowContext()
    result = dry_run_plan_result(context, "2026-11-21T00:00:00Z", "I-1-x/P-2-CLA-CO5.5/_", ["K-1"])
    assert result.status == StageStatus.SUCCESS
    assert context.plan_branch == "I-1-x/P-2-CLA-CO5.5/_"
    assert context.stage_results["plan"] is result
    assert result.payload == {
        "plan_id": "P-2-CLA-CO5.5",
        "plan_branch": "I-1-x/P-2-CLA-CO5.5/_",
        "plan_file": "plans/I-1-x/P-2-CLA-CO5.5.md",
        "metrics": {},
        "consulted_knowledge_ids": ["K-1"],
        "dry_run": True,
    }


def test_plan_id_of_reads_the_last_segment_of_a_plan_branch():
    assert plan_id_of("I-1-x/I-3-y/P-2-CLA-CO5.5/_") == "P-2-CLA-CO5.5"
