"""Tests for the Stage 2 re-plan request: its wording, the feedback variable and the executor's refusal exit code."""

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sandbox_executor import flow as flow_module
from sandbox_executor.budgets import MAX_REPLAN_ATTEMPTS, MAX_SUB_INTENT_DEPTH
from sandbox_executor.entrypoint.executor import OVER_BUDGET_EXIT_CODE
from sandbox_executor.flow import (
    FlowContext,
    FlowStage,
    PipelineEngine,
    StageResult,
    StageStatus,
    run_execute_stage,
    run_plan_stage,
)

from tests.flow.helpers import BudgetRoleAgents, _budget_context, stage4_halts_at_consensus
from tests.flow.lookup_patch import patch_lookup_sites
from tests.flow.stage_templates import AgentsSpec


@pytest.mark.usefixtures("no_origin_reconcile")
class TestReplanRequestInFlow:
    """The re-plan request the engine words for Stage 2, on its exception paths and with the refusal exit code."""

    @pytest.mark.parametrize(
        ("feedback", "expected"),
        [(" \n\t ", None), ("  Re-plan: entropy 12.6  ", "Re-plan: entropy 12.6")],
        ids=["blank", "padded"],
    )
    def test_plan_stage_forwards_only_non_blank_stripped_feedback(self, feedback, expected, monkeypatch, tmp_path):
        monkeypatch.delenv("HOLON_REPLAN_FEEDBACK", raising=False)
        ctx = FlowContext(
            repo_dir=str(tmp_path), token_reduce=False, intent_data={"slug": "parent", "replan_feedback": feedback}
        )
        ctx.intent_branch = "I-1-parent/_"
        seen: list[str | None] = []

        def dispatch(_context: FlowContext, _role: str, _args: list[str]) -> int:
            seen.append(os.environ.get("HOLON_REPLAN_FEEDBACK"))
            return 1

        with (
            patch("sandbox_executor.flow.stages.plan.at_stage_boundary", return_value=None),
            patch("sandbox_executor.flow.stages.plan._snapshot_child_branches", return_value=set()),
            patch("sandbox_executor.flow.stages.plan._dispatch_role", side_effect=dispatch),
        ):
            res = run_plan_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert seen == [expected]
        assert "HOLON_REPLAN_FEEDBACK" not in os.environ

    @pytest.mark.parametrize(
        ("depth", "asked"),
        [
            (
                MAX_SUB_INTENT_DEPTH - 1,
                "write a smaller plan within the budget, or add an explicit sub-intent list.",
            ),
            (
                MAX_SUB_INTENT_DEPTH,
                "write a smaller single plan within the budget. Do not add a sub-intent list: this intent is at the "
                f"sub-intent depth limit of {MAX_SUB_INTENT_DEPTH}, so a sub-intent list would be refused.",
            ),
        ],
        ids=["below-the-depth-cap", "at-the-depth-cap"],
    )
    def test_replan_request_offers_a_sub_intent_list_only_below_the_depth_cap(self, depth, asked):
        plan = "I-1-parent/P-2-agent-model/_"
        ctx = FlowContext(
            token_reduce=False, intent_data={"sub_intent_depth": depth}, plan_branch=plan, replan_attempts=1
        )
        refused = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.FAILED,
            payload={"over_budget": True, "entropy": 12.6, "entropy_budget": 5.0},
        )

        feedback = PipelineEngine(ctx)._replan_feedback(refused)

        assert feedback == (
            f"The previous plan '{plan}' has entropy 12.6, above the entropy budget 5, and lists no sub-intent list. "
            f"Re-plan attempt 1 of {MAX_REPLAN_ATTEMPTS}: {asked}"
        )
        assert ctx.logs[-1].endswith(
            "] Stage 3: plan entropy 12.6 exceeds the entropy budget 5; returning to Stage 2 "
            f"(re-plan attempt 1 of {MAX_REPLAN_ATTEMPTS})."
        )

    def test_leaf_intent_at_the_depth_cap_is_asked_for_a_smaller_single_plan_and_runs_it(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(12.6, ""), (2.0, "")])
        ctx = _budget_context(host, agents, 5.0, sub_intent_depth=MAX_SUB_INTENT_DEPTH)

        with stage4_halts_at_consensus():
            results = PipelineEngine(ctx).run()

        # The smaller re-plan runs normally, even for a leaf intent at the cap.
        assert results["execute"].status == StageStatus.SUCCESS
        assert agents.roles() == ["intent-creator", "planner", "planner", "executor"]
        # The planner saw the request the engine worded for the cap, and the engine logged the return to Stage 2.
        assert (agents.replan_feedback_seen[1] or "").endswith(
            f"Re-plan attempt 1 of {MAX_REPLAN_ATTEMPTS}: write a smaller single plan within the budget. Do not add a "
            f"sub-intent list: this intent is at the sub-intent depth limit of {MAX_SUB_INTENT_DEPTH}, so a "
            "sub-intent list would be refused."
        )
        assert any(
            line.endswith(
                "] Stage 3: plan entropy 12.6 exceeds the entropy budget 5; returning to Stage 2 "
                f"(re-plan attempt 1 of {MAX_REPLAN_ATTEMPTS})."
            )
            for line in ctx.logs
        )

    def test_replan_feedback_variable_is_restored_when_the_planner_dispatch_raises(self, stage_start, monkeypatch):
        monkeypatch.delenv("HOLON_REPLAN_FEEDBACK", raising=False)
        # Stage 1 comes from its stage-start template; this test is about Stage 2's environment variable.
        ctx = stage_start("intent", AgentsSpec(plans=((2.0, ""),))).ctx
        ctx.intent_data["replan_feedback"] = "smaller please"
        seen: list[str | None] = []

        def crash(*_args: Any, **_kwargs: Any) -> int:
            seen.append(os.environ.get("HOLON_REPLAN_FEEDBACK"))
            raise RuntimeError("docker crashed")

        with patch("sandbox_executor.flow.stages.plan._dispatch_role", side_effect=crash), pytest.raises(RuntimeError):
            run_plan_stage(ctx)

        assert seen == ["smaller please"]
        assert "HOLON_REPLAN_FEEDBACK" not in os.environ

    def test_refused_plan_exit_code_with_another_row_fails_the_stage(self, stage_start):
        # Stages 1 and 2 come from their stage-start template; this test is about Stage 3's exit-code reading.
        start = stage_start("plan", AgentsSpec(plans=((2.0, ""),)))
        agents, ctx = start.agents, start.ctx
        real_execute = agents._execute

        def execute_then_exit_refused(work: Path, args: list[str], kwargs: dict[str, Any]) -> int:
            real_execute(work, args, kwargs)
            return OVER_BUDGET_EXIT_CODE

        # Only an over_budget row explains the refusal exit code; any other row is an executor failure.
        with patch.object(agents, "_execute", side_effect=execute_then_exit_refused):
            res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert res.error == f"Execute stage failed: executor agent exited with code {OVER_BUDGET_EXIT_CODE}."
        assert res.payload["exit_code"] == OVER_BUDGET_EXIT_CODE
        assert res.payload["execution_branch"] == ctx.execution_branch is not None
        assert "over_budget" not in res.payload

    def test_refused_plan_exit_code_names_itself_when_the_fetch_fails(self, stage_start):
        # Stages 1 and 2 come from their stage-start template; this test is about Stage 3's exit-code reading.
        start = stage_start("plan", AgentsSpec(plans=((12.6, ""),)))
        agents, ctx = start.agents, start.ctx

        real_fetch = flow_module._fetch_branch_prefix_error

        def fetch_fails_after_dispatch(repo_dir: str, prefix: str) -> str | None:
            return "network down" if "executor" in agents.roles() else real_fetch(repo_dir, prefix)

        with (
            patch("sandbox_executor.flow.stages.execute._plan_budget_decision", return_value=None),
            patch_lookup_sites(
                "sandbox_executor.flow.git_refs._fetch_branch_prefix_error",
                "sandbox_executor.flow.stages.execute._fetch_branch_prefix_error",
                side_effect=fetch_fails_after_dispatch,
            ),
        ):
            res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert res.error is not None
        assert res.error.startswith(f"Execute stage failed: the executor agent exited {OVER_BUDGET_EXIT_CODE} but ")
        assert res.error.endswith("could not be fetched to adopt its execution branch: network down")
