"""Tests for the entropy budget handoff, bounded re-planning and sequential child flows of Stage 3."""

import dataclasses
import json
import os
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor import flow as flow_module
from sandbox_executor.budgets import MAX_REPLAN_ATTEMPTS, MAX_SUB_INTENT_DEPTH
from sandbox_executor.decomposition_rule import DecompositionDecision
from sandbox_executor.entrypoint.executor import OVER_BUDGET_EXIT_CODE
from sandbox_executor.flow import (
    FlowContext,
    FlowStage,
    PipelineEngine,
    StageResult,
    StageStatus,
    run_execute_stage,
    run_intent_stage,
    run_plan_stage,
)

from tests.flow.helpers import SUB_INTENT_LIST, BudgetRoleAgents, _all_intent_rows, _budget_context, _ledger_rows
from tests.flow.lookup_patch import patch_lookup_sites


class TestEntropyBudgetHandoffInFlow:
    """Intent goal 4: no Stage 3 halt, budget handoff, bounded re-planning and sequential child pipelines."""

    @pytest.mark.parametrize(
        ("entropy", "extra", "expected_status"),
        [
            (2.0, "", StageStatus.SUCCESS),
            (12.6, "", StageStatus.FAILED),
            (2.0, SUB_INTENT_LIST, StageStatus.SUCCESS),
        ],
        ids=["normal", "over-budget", "decomposed"],
    )
    @pytest.mark.parametrize("precheck", [True, False], ids=["precheck", "container-row"])
    def test_stage3_never_halts_for_a_human(self, flow_repos, tmp_path, entropy, extra, expected_status, precheck):  # noqa: PLR0913
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(entropy, extra), (2.0, "")])
        ctx = _budget_context(host, agents, 5.0)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS

        if precheck:
            res = run_execute_stage(ctx)
        else:
            # Model a plan the flow could not read before dispatch: the executor's own row decides.
            with patch("sandbox_executor.flow.stages.execute._plan_budget_decision", return_value=None):
                res = run_execute_stage(ctx)

        assert res.status != StageStatus.HALTED_FOR_HUMAN
        if extra and not precheck:
            # The children come only from the plan row's record: with none read, a decomposed report is an error.
            assert res.status == StageStatus.FAILED
            assert "the plan row records 'no readable decision'" in (res.error or "")
            assert agents.roles() == ["intent-creator", "planner", "executor"]
            return
        assert res.status == expected_status
        if expected_status == StageStatus.FAILED:
            assert res.payload["over_budget"] is True
            assert res.payload["dispatched"] is (not precheck)
            assert (res.payload["entropy"], res.payload["entropy_budget"]) == (entropy, 5.0)
            assert res.error == (
                "Execute stage failed: plan entropy 12.6 exceeds the entropy budget 5 and the plan lists no sub-intent "
                "list."
            )
            if not precheck:
                # The executor's row also carries the execution it recorded.
                assert res.payload["execution_branch"] == ctx.execution_branch is not None

    def test_intent_budget_16_with_plan_entropy_12_6_executes_normally(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(12.6, "")])
        ctx = _budget_context(host, agents, 16.0)

        results = PipelineEngine(ctx).run()

        assert results["execute"].status == StageStatus.SUCCESS
        assert agents.roles() == ["intent-creator", "planner", "executor"]
        assert ctx.replan_attempts == 0

    def test_budget_persisted_on_plan_row_is_the_one_enforced(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(12.6, "")], row_budget=True)
        ctx = _budget_context(host, agents, 16.0)

        results = PipelineEngine(ctx).run()

        assert results["execute"].status == StageStatus.SUCCESS
        plan_rows = _ledger_rows(host, f"origin/{ctx.plan_branch}", "plans.jsonl")
        assert plan_rows[-1]["entropy_budget"] == 16.0

    def test_over_budget_plan_replans_then_fails_without_intent_rows(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(12.6, "")])
        ctx = _budget_context(host, agents, 5.0)

        results = PipelineEngine(ctx).run()

        assert agents.planner_calls == 1 + MAX_REPLAN_ATTEMPTS
        assert "executor" not in agents.roles()
        assert ctx.replan_attempts == MAX_REPLAN_ATTEMPTS
        execute = results["execute"]
        assert execute.status == StageStatus.FAILED
        assert execute.error == (
            f"Execute stage failed: plan entropy 12.6 exceeds the entropy budget 5 after {MAX_REPLAN_ATTEMPTS} "
            "re-plan attempts and the plan lists no sub-intent list."
        )
        assert "review" not in results
        assert execute.payload["replan_attempts"] == MAX_REPLAN_ATTEMPTS
        # The feedback reached the planner on each re-plan only, and never lingers in the payload or environment.
        assert agents.replan_feedback_seen[0] is None
        assert all("entropy 12.6" in (fb or "") for fb in agents.replan_feedback_seen[1:])
        assert "replan_feedback" not in ctx.intent_data
        assert "HOLON_REPLAN_FEEDBACK" not in os.environ
        assert [r["slug"] for r in _all_intent_rows(host)] == ["budget-test"]

    def test_over_budget_plan_recovers_after_one_replan(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(12.6, ""), (4.0, "")])
        ctx = _budget_context(host, agents, 5.0)

        results = PipelineEngine(ctx).run()

        assert results["execute"].status == StageStatus.SUCCESS
        assert ctx.replan_attempts == 1
        assert agents.roles() == ["intent-creator", "planner", "planner", "executor"]

    def test_explicit_sub_intent_list_runs_child_pipelines_in_order(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(9.0, SUB_INTENT_LIST), (2.0, "")])
        ctx = _budget_context(host, agents, 5.0)

        results = PipelineEngine(ctx).run()

        assert agents.overlaps == 0
        assert agents.roles() == [
            "intent-creator",
            "planner",
            "executor",
            *(["intent-creator", "planner", "executor"] * 2),
        ]
        children = [p["slug"] for p in agents.intent_payloads[1:]]
        assert children == ["add-the-parser", "add-the-cli-flag"]
        for payload in agents.intent_payloads[1:]:
            assert payload["target_branch"] == ctx.intent_branch
            assert payload["parent_intent_id"] == ctx.intent_branch
            assert payload["entropy_budget"] == 5.0
            assert payload["sub_intent_depth"] == 1
            # Labels drive the PR title scope, so each child PR is scoped like its parent's.
            assert payload["labels"] == ["flow"]
        execute = results["execute"]
        assert execute.status == StageStatus.SUCCESS
        assert execute.payload["decomposed"] is True
        assert [c["slug"] for c in execute.payload["children"]] == children
        assert execute.payload["parent_branch"] == ctx.intent_branch
        assert execute.payload["parent_target_branch"] == "main"
        for child in execute.payload["children"]:
            assert child["execution_branch"].startswith(child["intent_branch"].removesuffix("/_") + "/P-")
            assert {"pr_url", "pr_number"} <= child.keys()
        summary = flow_module.format_decomposition_summary(execute)
        assert summary[0] == "Decomposed into 2 sub-intent(s); merge each child PR into the parent intent branch:"
        assert summary[1].startswith("  1. add-the-parser: ")
        assert summary[-1] == (
            f"Pending (not yet automated): after the child PRs merge, raise a PR from '{ctx.intent_branch}' to 'main'."
        )
        assert all(c["status"] in ("success", "skipped", "halted_for_human") for c in execute.payload["children"])
        assert results["review"].status == StageStatus.SKIPPED
        assert results["review"].payload == {"reason": "decomposed"}
        assert results["calibrate"].status == StageStatus.SKIPPED
        # Skipped because the plan was decomposed, not only by the test context's skip flag.
        assert results["calibrate"].payload == {"reason": "decomposed"}
        assert any("[sub-intent add-the-parser]" in line for line in ctx.logs)
        # The executor wrote no intent rows: only the parent and the two children created through Stage 1 exist.
        assert sorted(r["slug"] for r in _all_intent_rows(host)) == sorted(["budget-test", *children])

    def test_child_that_itself_decomposes_lists_its_own_children_in_the_summary(self, flow_repos, tmp_path):
        host, origin = flow_repos
        # Parent and first child both list sub-intents; the two grandchildren and the second child execute.
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(9.0, SUB_INTENT_LIST), (9.0, SUB_INTENT_LIST), (2.0, "")])
        ctx = _budget_context(host, agents, 5.0)

        results = PipelineEngine(ctx).run()

        execute = results["execute"]
        assert execute.status == StageStatus.SUCCESS
        first, second = execute.payload["children"]
        assert first["decomposed"] is True
        assert "decomposed" not in second
        grandchildren = first["children"]
        assert [g["slug"] for g in grandchildren] == ["add-the-parser", "add-the-cli-flag"]
        # The first grandchild shares its parent's slug yet runs on its own branch, however fast the run.
        assert [g["intent_branch"] for g in grandchildren] == [
            first["intent_branch"].removesuffix("/_") + suffix
            for suffix in ("--1-add-the-parser/_", "--2-add-the-cli-flag/_")
        ]
        assert [p["sub_intent_depth"] for p in agents.intent_payloads[1:]] == [1, 2, 2, 1]
        assert [p["target_branch"] for p in agents.intent_payloads[2:4]] == [first["intent_branch"]] * 2
        summary = flow_module.format_decomposition_summary(execute)
        assert summary == [
            "Decomposed into 2 sub-intent(s); merge each child PR into the parent intent branch:",
            f"  1. add-the-parser: decomposed into 2 sub-intent(s), so it has no PR of its own; merge its child PRs "
            f"below into '{first['intent_branch']}', then raise a PR from '{first['intent_branch']}' to "
            f"'{ctx.intent_branch}' (not yet automated):",
            f"     1. add-the-parser: {grandchildren[0]['execution_branch']} ({grandchildren[0]['status']})",
            f"     2. add-the-cli-flag: {grandchildren[1]['execution_branch']} ({grandchildren[1]['status']})",
            f"  2. add-the-cli-flag: {second['execution_branch']} ({second['status']})",
            f"Pending (not yet automated): after the child PRs merge, raise a PR from '{ctx.intent_branch}' to 'main'.",
        ]
        # The decomposed child's ledger-only execution branch is never offered as a PR to merge.
        assert not any(first["execution_branch"] in line for line in summary)

    @pytest.mark.parametrize(
        ("parent", "index", "slug", "expected"),
        [
            ("I-1-root/_", 1, "add-the-parser", "I-1-root--1-add-the-parser"),
            ("I-1-root--1-a/_", 2, "add-the-parser", "I-1-root--1-a--2-add-the-parser"),
            ("I-1-root/_", 3, " Add the--CLI flag! ", "I-1-root--3-add-the-cli-flag"),
            ("I-1-root/_", 4, "--", "I-1-root--4-sub-intent-4"),
        ],
        ids=["child", "grandchild", "unsanitised-slug", "empty-slug"],
    )
    def test_child_intent_branch_is_derived_from_parent_and_position(self, parent, index, slug, expected):
        assert flow_module._child_intent_branch(parent, index, slug) == expected

    def test_same_slug_child_created_in_the_same_second_gets_its_own_intent_branch(self, flow_repos, tmp_path):
        host, origin = flow_repos
        # The first grandchild shares its parent's slug, and a frozen clock creates every intent in the same second.
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(9.0, SUB_INTENT_LIST), (9.0, SUB_INTENT_LIST), (2.0, "")])
        ctx = _budget_context(host, agents, 5.0)

        with patch("sandbox_executor.flow.stages.intent.time.time", return_value=1793098938.0):
            results = PipelineEngine(ctx).run()

        # Every intent was dispatched to the intent creator on a branch of its own: none reused another's.
        branches = [p["branch"] for p in agents.intent_payloads]
        assert len(branches) == len(set(branches)) == 5
        root = ctx.intent_branch.removesuffix("/_")
        assert branches == [
            "I-1793098938-budget-test",
            f"{root}--1-add-the-parser",
            f"{root}--1-add-the-parser--1-add-the-parser",
            f"{root}--1-add-the-parser--2-add-the-cli-flag",
            f"{root}--2-add-the-cli-flag",
        ]
        assert [p["sub_intent_depth"] for p in agents.intent_payloads[1:]] == [1, 2, 2, 1]
        assert results["execute"].status == StageStatus.SUCCESS
        first = results["execute"].payload["children"][0]
        assert [g["intent_branch"] for g in first["children"]] == [f"{b}/_" for b in branches[2:4]]

    def test_child_halting_at_its_own_review_is_a_success_and_records_its_pr(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(9.0, SUB_INTENT_LIST), (2.0, "")])
        ctx = _budget_context(host, agents, 5.0)
        ctx.skip_review = False
        reviewed: list[str | None] = []

        def fake_review(child_ctx: FlowContext) -> StageResult:
            # The payload shape run_review_stage returns at its consensus halt: the PR result sits under "pr".
            number = 101 + len(reviewed)
            reviewed.append(child_ctx.execution_branch)
            return StageResult(
                stage=FlowStage.REVIEW,
                status=StageStatus.HALTED_FOR_HUMAN,
                payload={
                    "consensus": {"approved": True, "score": 1.0, "iterations": 1, "status": "approved"},
                    "halted_for_human": True,
                    "pr": {
                        "attempted": True,
                        "success": True,
                        "action": "create",
                        "pr_number": number,
                        "output": f"https://github.com/example/app/pull/{number}",
                    },
                },
            )

        with patch("sandbox_executor.flow.pipeline.run_review_stage", side_effect=fake_review):
            results = PipelineEngine(ctx).run()

        execute = results["execute"]
        assert execute.status == StageStatus.SUCCESS
        children = execute.payload["children"]
        # Both children ran although the first stopped at its own merge boundary.
        assert [c["slug"] for c in children] == ["add-the-parser", "add-the-cli-flag"]
        assert reviewed == [c["execution_branch"] for c in children]
        assert [(c["pr_url"], c["pr_number"]) for c in children] == [
            ("https://github.com/example/app/pull/101", 101),
            ("https://github.com/example/app/pull/102", 102),
        ]
        summary = flow_module.format_decomposition_summary(execute)
        assert summary[1].startswith("  1. add-the-parser: https://github.com/example/app/pull/101 (")
        assert results["review"].payload == {"reason": "decomposed"}

    def test_replan_whose_stage_2_fails_ends_the_run_in_stage_2(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(12.6, "")])
        ctx = _budget_context(host, agents, 5.0)
        real_plan_stage = flow_module.run_plan_stage
        calls: list[int] = []

        def plan_then_fail(plan_ctx: FlowContext) -> StageResult:
            calls.append(1)
            if len(calls) == 1:
                return real_plan_stage(plan_ctx)
            return StageResult(stage=FlowStage.PLAN, status=StageStatus.FAILED, error="Plan stage failed: no plan")

        with patch("sandbox_executor.flow.pipeline.run_plan_stage", side_effect=plan_then_fail):
            results = PipelineEngine(ctx).run()

        assert ctx.replan_attempts == 1
        assert results["plan"].status == StageStatus.FAILED
        assert results["plan"].error == "Plan stage failed: no plan"
        # The rejected plan is dropped before re-planning, so the checkpoint never resumes from the over-budget plan.
        assert ctx.plan_branch is None
        assert results["execute"].payload["over_budget"] is True
        assert "review" not in results
        assert ctx.logs[-1].endswith("Pipeline halted due to failure in stage 'plan'.")
        assert "replan_feedback" not in ctx.intent_data
        assert "HOLON_REPLAN_FEEDBACK" not in os.environ

    def test_first_failing_child_stops_the_loop(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(
            tmp_path, origin, plans=[(9.0, SUB_INTENT_LIST), (2.0, "")], fail_planning_for="add-the-parser"
        )
        ctx = _budget_context(host, agents, 5.0)

        results = PipelineEngine(ctx).run()

        execute = results["execute"]
        assert execute.status == StageStatus.FAILED
        assert "sub-intent 1/2 'add-the-parser' failed at stage 'plan'" in (execute.error or "")
        assert [p["slug"] for p in agents.intent_payloads[1:]] == ["add-the-parser"]
        assert "review" not in results
        summary = flow_module.format_decomposition_summary(execute)
        assert summary[0] == (
            "Decomposed into 2 sub-intent(s); 1 of 2 ran before Stage 3 failed, so no child PR is ready to merge:"
        )
        assert len(summary) == 2
        assert not any("merge each child PR" in line or line.startswith("Pending") for line in summary)

    def test_sub_intent_depth_is_bounded(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(2.0, SUB_INTENT_LIST)])
        ctx = _budget_context(host, agents, 5.0, sub_intent_depth=MAX_SUB_INTENT_DEPTH)

        results = PipelineEngine(ctx).run()

        assert results["execute"].status == StageStatus.FAILED
        assert "deeper than the limit" in (results["execute"].error or "")
        # The pre-dispatch check refuses the over-deep list, so no executor container runs.
        assert results["execute"].payload["dispatched"] is False
        assert agents.roles() == ["intent-creator", "planner"]

    def test_sub_intent_depth_is_bounded_when_the_precheck_cannot_read_the_plan(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(2.0, SUB_INTENT_LIST)])
        ctx = _budget_context(host, agents, 5.0, sub_intent_depth=MAX_SUB_INTENT_DEPTH)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS

        # With no pre-dispatch decision the executor's decomposed row carries no list (the plan row is the only
        # record), so the stage fails without running any child: the depth bound holds trivially.
        with patch("sandbox_executor.flow.stages.execute._plan_budget_decision", return_value=None):
            res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "the plan row records 'no readable decision'" in (res.error or "")
        assert "children" not in res.payload
        assert flow_module.format_decomposition_summary(res) == []
        # The executor ran once and no child intent was created.
        assert agents.roles() == ["intent-creator", "planner", "executor"]
        assert len(agents.intent_payloads) == 1

    @pytest.mark.parametrize("sub_intents", [[], None, ["not-a-dict"]], ids=["empty", "missing", "no-dict-items"])
    def test_decomposed_row_without_sub_intent_items_fails_stage(self, sub_intents):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent", "goal": "Change the application"})
        row: dict[str, Any] = {"status": "decomposed", "plan_branch": "I-1-parent/P-2-agent-model/_"}
        if sub_intents is not None:
            row["sub_intents"] = sub_intents

        with patch_lookup_sites(
            "sandbox_executor.flow.review.ensemble._dispatch_role",
            "sandbox_executor.flow.review.resolver._dispatch_role",
            "sandbox_executor.flow.stages.execute._dispatch_role",
            "sandbox_executor.flow.stages.intent._dispatch_role",
            "sandbox_executor.flow.stages.plan._dispatch_role",
        ) as dispatch:
            res = flow_module._run_decomposed_plan(ctx, "2026-10-26T00:00:00Z", row, {"execution_id": "E-1"})

        assert res.status == StageStatus.FAILED
        assert "without any sub-intent list items" in (res.error or "")
        assert res.payload["sub_intents"] == []
        assert res.payload["children"] == []
        dispatch.assert_not_called()

    def test_format_decomposition_summary_is_empty_for_undecomposed_execute(self):
        plain = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"execution_id": "E-1"})
        assert flow_module.format_decomposition_summary(plain) == []
        assert flow_module.format_decomposition_summary(None) == []

    def test_replan_attempts_round_trip_through_checkpoints(self):
        ctx = FlowContext(token_reduce=False, replan_attempts=2)
        assert FlowContext.from_dict(ctx.to_dict()).replan_attempts == 2
        legacy = ctx.to_dict()
        del legacy["replan_attempts"]
        assert FlowContext.from_dict(legacy).replan_attempts == 0
        # A hand-edited or foreign checkpoint may carry null or a numeric string; both load as an int.
        for recorded, expected in ((None, 0), ("1", 1)):
            loaded = FlowContext.from_dict({**legacy, "replan_attempts": recorded}).replan_attempts
            assert (loaded, type(loaded)) == (expected, int)

    def test_replan_restores_a_pre_existing_replan_feedback_variable(self, flow_repos, tmp_path, monkeypatch):
        host, origin = flow_repos
        monkeypatch.setenv("HOLON_REPLAN_FEEDBACK", "operator value")
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(12.6, ""), (4.0, "")])
        ctx = _budget_context(host, agents, 5.0)

        results = PipelineEngine(ctx).run()

        assert results["execute"].status == StageStatus.SUCCESS
        assert "entropy 12.6" in (agents.replan_feedback_seen[1] or "")
        assert os.environ.get("HOLON_REPLAN_FEEDBACK") == "operator value"

    def test_failed_decomposed_execute_does_not_skip_review_or_calibration(self):
        ctx = FlowContext(token_reduce=False)
        engine = PipelineEngine(ctx)
        for status, expected in ((StageStatus.FAILED, False), (StageStatus.SUCCESS, True)):
            ctx.stage_results["execute"] = StageResult(
                stage=FlowStage.EXECUTE, status=status, payload={"decomposed": True}
            )
            assert engine._parent_was_decomposed() is expected

    @pytest.mark.parametrize(("recorded", "expected"), [(-3, 0), ("-1", 0), (None, 0), ("deep", 0), ("2", 2)], ids=str)
    def test_sub_intent_depth_is_never_negative(self, recorded, expected):
        # A negative or unreadable depth counts as a root intent, so children start at depth 1.
        assert flow_module._sub_intent_depth({"sub_intent_depth": recorded}) == expected

    def test_decomposed_summary_names_the_parent_intents_own_target_branch(self):
        # The pending parent PR targets the parent intent's target_branch, not always main.
        ctx = FlowContext(
            token_reduce=False,
            intent_data={"slug": "parent", "goal": "Change the application", "target_branch": "release/1.x"},
        )
        ctx.intent_branch = "I-1-parent/_"
        row = {
            "status": "decomposed",
            "plan_branch": "I-1-parent/P-2-agent-model/_",
            "sub_intents": [{"slug": "first", "goal": "Add the parser"}],
        }
        done = {"intent": StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS)}

        with patch.object(PipelineEngine, "run", return_value=done):
            res = flow_module._run_decomposed_plan(ctx, "2026-10-26T00:00:00Z", row, {"execution_id": "E-1"})

        assert res.status == StageStatus.SUCCESS
        assert res.payload["parent_target_branch"] == "release/1.x"
        assert flow_module.format_decomposition_summary(res)[-1] == (
            "Pending (not yet automated): after the child PRs merge, raise a PR from 'I-1-parent/_' to 'release/1.x'."
        )

    def test_child_pipeline_that_runs_no_stage_fails_the_parent(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent", "goal": "Change the application"})
        ctx.intent_branch = "I-1-parent/_"
        row = {
            "status": "decomposed",
            "plan_branch": "I-1-parent/P-2-agent-model/_",
            "sub_intents": [{"slug": "first", "goal": "Add the parser"}, {"slug": "second", "goal": "Add the flag"}],
        }

        with patch.object(PipelineEngine, "run", return_value={}) as run:
            res = flow_module._run_decomposed_plan(ctx, "2026-10-26T00:00:00Z", row, {"execution_id": "E-1"})

        assert res.status == StageStatus.FAILED
        assert res.error == "Execute stage failed: sub-intent 1/2 'first' failed at stage 'none': no stage ran"
        assert [c["slug"] for c in res.payload["children"]] == ["first"]
        assert run.call_count == 1

    def test_child_context_inherits_or_resets_every_field(self):
        """Table-driven: every FlowContext field is either inherited from the parent or reset for the new child."""
        dispatcher = MagicMock()
        parent_values: dict[str, Any] = {
            "target_worktree": "/srv/parent-repo",
            "agent": "codex",
            "model": "parent-only-model",
            "dry_run": True,
            "auto_calibrate": False,
            "token_reduce": True,
            "skip_push": True,
            "skip_review": True,
            "skip_calibrate": True,
            "max_review_iterations": 7,
            "review_ensemble_size": 5,
            "mitm_web": True,
            "dispatcher": dispatcher,
        }
        parent = FlowContext(
            intent_data={"slug": "parent", "entropy_budget": 5.0, "labels": ["parent-label"]},
            intent_branch="I-1-parent/_",
            plan_branch="I-1-parent/P-2-agent-model/_",
            execution_branch="I-1-parent/P-2-agent-model/E-3-agent-model/_",
            calibrated_branch="I-1-parent/P-2-agent-model/E-3-agent-model/calibrated",
            stage_results={"intent": StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS)},
            logs=["parent log line"],
            # A non-current version, as FlowContext.from_dict accepts from a malformed checkpoint (e.g. -1).
            schema_version=-1,
            replan_attempts=1,
            review_iterations_done=3,
            **parent_values,
        )
        child_intent = {"slug": "child", "goal": "Add the parser"}
        expected: dict[str, Any] = {
            **parent_values,
            "intent_data": child_intent,
            "intent_branch": None,
            "plan_branch": None,
            "execution_branch": None,
            "calibrated_branch": None,
            "stage_results": {},
            "logs": [],
            "schema_version": flow_module.CURRENT_CONTEXT_SCHEMA_VERSION,
            "replan_attempts": 0,
            # A child intent runs its own Stage 4, so its review count starts afresh.
            "review_iterations_done": 0,
        }
        defaults = FlowContext(token_reduce=False)
        for name, value in parent_values.items():
            assert getattr(defaults, name) != value, f"{name}: parent value must differ from the default"

        # Pin proxy detection to the opposite of the parent's token_reduce so a dropped copy cannot pass by detection.
        with patch("sandbox_executor.cli.detect_holon_coherence", return_value=False):
            child = flow_module._child_context(parent, child_intent)

        assert set(expected) == {f.name for f in dataclasses.fields(FlowContext)}, "classify every new field"
        for name, value in expected.items():
            assert getattr(child, name) == value, name
        assert child.dispatcher is dispatcher

    @staticmethod
    def _decide(rows: list[dict[str, Any]], files: dict[str, str]) -> Any:
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent", "entropy_budget": 5.0})
        ctx.plan_branch = "I-1-parent/P-2-agent-model/_"
        with (
            patch(
                "sandbox_executor.flow.stages.plan_decision._resolve_branch_ref",
                return_value="refs/remotes/origin/plan",
            ),
            patch("sandbox_executor.flow.stages.plan_decision._read_ledger_rows", return_value=rows),
            patch(
                "sandbox_executor.flow.stages.plan_decision._git_show_text",
                side_effect=lambda _repo, _ref, path: files.get(path),
            ),
            # These rows predate the recorded decision; the back-fill write is covered in test_plan_decision.py.
            patch("sandbox_executor.flow.stages.plan_decision._backfill_plan_row", return_value=None),
        ):
            return flow_module._plan_budget_decision(ctx)

    def test_budget_precheck_reads_the_newest_plan_row(self):
        rows = [
            {"plan_id": "P-2-agent-model", "entropy": 12.6, "plan_file": "plans/P-2-agent-model.md"},
            {"plan_id": "P-2-agent-model", "entropy": 2.0, "plan_file": "plans/P-2-agent-model.md"},
        ]
        decision = self._decide(rows, {"plans/P-2-agent-model.md": "# Plan\n"})
        assert (decision.decision, decision.entropy) == ("execute", 2.0)

    @pytest.mark.parametrize("plan_file", ["plans/P-x/nested.md", "plans/P-x/../../README.md"])
    def test_budget_precheck_ignores_a_plan_file_below_a_subdirectory_of_plans(self, plan_file):
        rows = [{"plan_id": "P-2-agent-model", "entropy": 9.0, "plan_file": plan_file}]
        files = {"plans/P-2-agent-model.md": "# Plan\n" + SUB_INTENT_LIST, plan_file: "# Plan\n"}
        assert self._decide(rows, files).decision == "decomposed"

    def test_budget_precheck_with_a_missing_plan_file_judges_the_row_alone(self):
        rows = [{"plan_id": "P-2-agent-model", "entropy": 9.0, "plan_file": "plans/P-2-agent-model.md"}]
        decision = self._decide(rows, {})
        assert (decision.decision, decision.entropy, decision.entropy_budget) == ("over_budget", 9.0, 5.0)

    def test_budget_precheck_ignores_a_plan_file_outside_plans(self):
        rows = [{"plan_id": "P-2-agent-model", "entropy": 9.0, "plan_file": "README.md"}]
        files = {"plans/P-2-agent-model.md": "# Plan\n" + SUB_INTENT_LIST, "README.md": "# Readme\n"}
        decision = self._decide(rows, files)
        assert decision.decision == "decomposed"
        assert [i["goal"] for i in decision.sub_intents] == ["Add the parser", "Add the CLI flag"]

    def test_only_the_review_stage_constructs_a_halt(self):
        package_dir = Path(flow_module.__file__).parent
        source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(package_dir.rglob("*.py")))
        execute_body = source.split("def run_execute_stage(", 1)[1].split("\ndef ", 1)[0]
        assert "HALTED_FOR_HUMAN" not in execute_body
        assert source.count("status=StageStatus.HALTED_FOR_HUMAN") == 1
        review_body = source.split("def run_review_stage(", 1)[1].split("\ndef ", 1)[0]
        assert "status=StageStatus.HALTED_FOR_HUMAN" in review_body

    # --- Depth cap boundary -------------------------------------------------------------------------------------

    def test_sub_intent_depth_error_allows_children_at_the_cap_and_refuses_one_deeper(self):
        at_cap = FlowContext(token_reduce=False, intent_data={"sub_intent_depth": MAX_SUB_INTENT_DEPTH - 1})
        past_cap = FlowContext(token_reduce=False, intent_data={"sub_intent_depth": MAX_SUB_INTENT_DEPTH})

        assert flow_module._sub_intent_depth_error(at_cap) is None
        assert flow_module._sub_intent_depth_error(past_cap) == (
            f"Execute stage failed: the plan lists a sub-intent list at depth {MAX_SUB_INTENT_DEPTH + 1}, deeper than "
            f"the limit of {MAX_SUB_INTENT_DEPTH}; refusing to recurse further."
        )

    @staticmethod
    def _run_children(
        intent_data: dict[str, Any], row: dict[str, Any], child_results: dict[str, StageResult] | None = None, **ctx_kw
    ) -> tuple[StageResult, FlowContext, list[FlowContext]]:
        """Run _run_decomposed_plan with each child pipeline replaced by a stub; return result, parent, children."""
        ctx = FlowContext(token_reduce=False, intent_data=intent_data, **ctx_kw)
        children: list[FlowContext] = []
        done = child_results or {"intent": StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS)}

        def fake_run(engine: PipelineEngine) -> dict[str, StageResult]:
            children.append(engine.context)
            return done

        with patch.object(PipelineEngine, "run", autospec=True, side_effect=fake_run):
            res = flow_module._run_decomposed_plan(ctx, "2026-10-26T00:00:00Z", row, {"execution_id": "E-1"})
        return res, ctx, children

    _ROW: ClassVar[dict[str, Any]] = {
        "status": "decomposed",
        "plan_branch": "I-1-parent/P-2-agent-model/_",
        "entropy_budget": 5.0,
        "sub_intents": [{"slug": "first", "description": "Add the parser\n- with tests", "goal": "Add the parser"}],
    }

    def test_child_at_exactly_the_depth_cap_runs(self):
        res, ctx, children = self._run_children(
            {"slug": "parent", "sub_intent_depth": MAX_SUB_INTENT_DEPTH - 1},
            self._ROW,
            intent_branch="I-1-parent/_",
            plan_branch="I-1-parent/P-2-agent-model/_",
        )

        assert res.status == StageStatus.SUCCESS
        assert [c.intent_data["sub_intent_depth"] for c in children] == [MAX_SUB_INTENT_DEPTH]
        assert ctx.logs[-1].endswith(
            "] Stage 3 completed: all 1 sub-intent(s) of 'I-1-parent/P-2-agent-model/_' ran through the pipeline."
        )

    def test_child_one_deeper_than_the_cap_is_refused(self):
        res, _ctx, children = self._run_children(
            {"slug": "parent", "sub_intent_depth": MAX_SUB_INTENT_DEPTH}, self._ROW, intent_branch="I-1-parent/_"
        )

        assert res.status == StageStatus.FAILED
        assert "deeper than the limit" in (res.error or "")
        assert children == []

    # --- Child intent payloads ----------------------------------------------------------------------------------

    def test_child_payload_fallbacks_and_copies(self):
        labels = ["flow"]
        row = {
            "status": "decomposed",
            "plan_branch": "I-1-parent/P-2-agent-model/_",
            "sub_intents": [{"goal": "Add the parser"}, {"description": "Add the flag\n- detail"}, "not-an-item"],
        }
        res, ctx, children = self._run_children({"slug": "parent", "labels": labels}, row)

        assert res.status == StageStatus.SUCCESS
        # A checkpoint without intent_branch still names the parent from the plan branch.
        assert res.payload["parent_branch"] == "I-1-parent/_"
        assert [c.intent_data["target_branch"] for c in children] == ["I-1-parent/_"] * 2
        first, second = (c.intent_data for c in children)
        assert (first["slug"], first["description"], first["goal"]) == (
            "sub-intent-1",
            "Add the parser",
            "Add the parser",
        )
        assert (second["slug"], second["goal"]) == ("sub-intent-2", "Add the flag\n- detail")
        assert second["description"] == "Add the flag\n- detail"
        # A row without entropy_budget hands no budget down, so each child resolves its own.
        assert "entropy_budget" not in first
        # Labels are copied, not shared, so a child cannot change its parent's labels.
        assert first["labels"] == labels and first["labels"] is not labels
        assert res.payload["sub_intents"] == row["sub_intents"][:2]
        assert ctx.stage_results["execute"] is res
        assert any(
            line.endswith("] Stage 3: running sub-intent 1/2 'sub-intent-1' through the full pipeline.")
            for line in ctx.logs
        )

    def test_child_payload_prefers_the_intent_branch_and_hands_down_the_budget(self):
        row = {k: v for k, v in self._ROW.items() if k != "plan_branch"}
        res, _ctx, children = self._run_children(
            {"slug": "parent", "labels": "flow"}, row, intent_branch="I-9-parent/_"
        )

        assert res.payload["parent_branch"] == "I-9-parent/_"
        (child,) = (c.intent_data for c in children)
        assert child["entropy_budget"] == 5.0
        assert (child["description"], child["goal"]) == ("Add the parser\n- with tests", "Add the parser")
        # Labels that are not a list are not handed down.
        assert "labels" not in child

    @pytest.mark.parametrize("execute_failed_too", [False, True], ids=["plan-only", "plan-and-execute"])
    def test_failed_child_names_its_own_error_and_final_stage(self, execute_failed_too):
        failed = {
            "intent": StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS),
            "plan": StageResult(stage=FlowStage.PLAN, status=StageStatus.FAILED, error="Plan stage failed: no plan"),
        }
        if execute_failed_too:
            # A child whose re-plan Stage 2 failed holds a failed plan and a failed execute: the first one is named.
            failed["execute"] = StageResult(
                stage=FlowStage.EXECUTE, status=StageStatus.FAILED, error="Execute stage failed: over budget"
            )
        res, _ctx, _children = self._run_children(
            {"slug": "parent"}, self._ROW, child_results=failed, intent_branch="I-1-parent/_"
        )

        assert res.error == (
            "Execute stage failed: sub-intent 1/1 'first' failed at stage 'plan': Plan stage failed: no plan"
        )
        (child,) = res.payload["children"]
        assert child["final_stage"] == ("execute" if execute_failed_too else "plan")
        assert child["status"] == "failed"

    @pytest.mark.parametrize("sub_intents", [5, "abc"], ids=["number", "string"])
    def test_decomposed_row_with_a_non_list_sub_intent_value_fails_stage(self, sub_intents):
        row = {"status": "decomposed", "plan_branch": "I-1-parent/P-2-agent-model/_", "sub_intents": sub_intents}
        res, _ctx, children = self._run_children({"slug": "parent"}, row, intent_branch="I-1-parent/_")

        assert res.status == StageStatus.FAILED
        assert "without any sub-intent list items" in (res.error or "")
        assert children == []

    # --- Child PR reporting and the flow summary ------------------------------------------------------------------

    @pytest.mark.parametrize(
        ("pr", "expected"),
        [
            (
                {"pr_url": "https://github.com/o/r/pull/7", "pr_number": 7, "output": ""},
                ("https://github.com/o/r/pull/7", 7),
            ),
            (
                {
                    "pr_number": 5,
                    "output": "see https://github.com/o/r/compare/x then https://github.com/o/r/pull/5 ok",
                },
                ("https://github.com/o/r/pull/5", 5),
            ),
            ("not a dict", (None, None)),
            (None, (None, None)),
        ],
        ids=["explicit-url", "url-from-output", "not-a-dict", "missing"],
    )
    def test_child_review_pr(self, pr, expected):
        payload = {} if pr is None else {"pr": pr}
        results = {"review": StageResult(stage=FlowStage.REVIEW, status=StageStatus.HALTED_FOR_HUMAN, payload=payload)}

        assert flow_module._child_review_pr(results) == expected
        assert flow_module._child_review_pr({}) == (None, None)

    def test_failed_decomposition_summary_counts_the_listed_items_not_only_the_children_that_ran(self):
        items = [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}, "not-an-item"]
        ran_one = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.FAILED,
            payload={
                "decomposed": True,
                "sub_intents": items,
                "children": [{"slug": "a", "execution_branch": "E-a", "status": "failed"}],
            },
        )
        assert flow_module.format_decomposition_summary(ran_one) == [
            "Decomposed into 3 sub-intent(s); 1 of 3 ran before Stage 3 failed, so no child PR is ready to merge:",
            "  1. a: E-a (failed)",
        ]
        # A depth refusal inside the decomposed run fails before any child runs.
        ran_none = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.FAILED,
            payload={"decomposed": True, "sub_intents": items, "children": []},
        )
        assert flow_module.format_decomposition_summary(ran_none) == [
            "Decomposed into 3 sub-intent(s); 0 of 3 ran before Stage 3 failed, so no child PR is ready to merge:",
        ]
        succeeded = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={
                "decomposed": True,
                "sub_intents": items[:2],
                "children": [{"slug": "a", "status": "s"}, {"slug": "b", "status": "s"}],
            },
        )
        assert flow_module.format_decomposition_summary(succeeded)[0] == (
            "Decomposed into 2 sub-intent(s); merge each child PR into the parent intent branch:"
        )

    def test_format_decomposition_summary_fallbacks(self):
        children = [
            {
                "slug": "a",
                "pr_url": "https://x/pull/1",
                "execution_branch": "E-a",
                "intent_branch": "I-a",
                "status": "s",
            },
            {"slug": "b", "execution_branch": "E-b", "intent_branch": "I-b", "status": "s"},
            {"slug": "c", "intent_branch": "I-c", "status": "s"},
            {"slug": "d", "status": "s"},
            "not-a-child",
        ]
        result = StageResult(
            stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"decomposed": True, "children": children}
        )

        assert flow_module.format_decomposition_summary(result) == [
            "Decomposed into 4 sub-intent(s); merge each child PR into the parent intent branch:",
            "  1. a: https://x/pull/1 (s)",
            "  2. b: E-b (s)",
            "  3. c: I-c (s)",
            "  4. d: no branch (s)",
            "Pending (not yet automated): after the child PRs merge, raise a PR from 'the parent intent branch' to "
            "'its target branch'.",
        ]

    def test_failed_decomposition_summary_nests_a_decomposed_child_without_merge_guidance(self):
        children = [
            {
                "slug": "a",
                "intent_branch": "I-a/_",
                "execution_branch": "E-a",
                "status": "skipped",
                "decomposed": True,
                "children": [{"slug": "x", "pr_url": "https://x/pull/3", "status": "s"}, "not-a-child"],
            },
            {"slug": "b", "execution_branch": "E-b", "status": "failed"},
        ]
        result = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.FAILED,
            payload={"decomposed": True, "sub_intents": [{"slug": "a"}, {"slug": "b"}], "children": children},
        )

        assert flow_module.format_decomposition_summary(result) == [
            "Decomposed into 2 sub-intent(s); 2 of 2 ran before Stage 3 failed, so no child PR is ready to merge:",
            "  1. a: decomposed into 1 sub-intent(s) (skipped):",
            "     1. x: https://x/pull/3 (s)",
            "  2. b: E-b (failed)",
        ]

    def test_decomposition_summary_names_each_decomposed_level_after_its_own_parent(self):
        # The rendering recurses, so a decomposed grandchild points at its own parent, not at the root intent branch.
        grandchild = {
            "slug": "g",
            "intent_branch": "I-g/_",
            "decomposed": True,
            "children": [{"slug": "x", "pr_url": "https://x/pull/9", "status": "s"}],
        }
        child = {"slug": "a", "intent_branch": "I-a/_", "decomposed": True, "children": [grandchild]}
        result = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={"decomposed": True, "sub_intents": [{"slug": "a"}], "children": [child], "parent_branch": "I-p/_"},
        )

        assert flow_module.format_decomposition_summary(result)[1:4] == [
            "  1. a: decomposed into 1 sub-intent(s), so it has no PR of its own; merge its child PRs below into "
            "'I-a/_', then raise a PR from 'I-a/_' to 'I-p/_' (not yet automated):",
            "     1. g: decomposed into 1 sub-intent(s), so it has no PR of its own; merge its child PRs below into "
            "'I-g/_', then raise a PR from 'I-g/_' to 'I-a/_' (not yet automated):",
            "        1. x: https://x/pull/9 (s)",
        ]

    def test_successful_decomposition_summary_header_counts_the_listed_items(self):
        # The header counts the dict items the plan listed, independently of how many child summaries are present.
        result = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={
                "decomposed": True,
                "sub_intents": [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}, "not-an-item"],
                "children": [{"slug": "a", "status": "s"}],
            },
        )
        assert flow_module.format_decomposition_summary(result)[0] == (
            "Decomposed into 3 sub-intent(s); merge each child PR into the parent intent branch:"
        )

    @pytest.mark.parametrize("sub_intents", ["abc", 5], ids=["string", "number"])
    def test_decomposition_summary_with_a_non_list_sub_intent_value_counts_the_children(self, sub_intents):
        result = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={"decomposed": True, "sub_intents": sub_intents, "children": [{"slug": "a", "status": "s"}]},
        )
        assert flow_module.format_decomposition_summary(result)[0] == (
            "Decomposed into 1 sub-intent(s); merge each child PR into the parent intent branch:"
        )

    # --- Pre-dispatch budget decision -----------------------------------------------------------------------------

    def test_budget_precheck_without_a_plan_branch_reads_nothing(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"})
        with patch_lookup_sites(
            "sandbox_executor.flow.git_refs._resolve_branch_ref",
            "sandbox_executor.flow.review.ensemble._resolve_branch_ref",
            "sandbox_executor.flow.stage_conflicts._resolve_branch_ref",
            "sandbox_executor.flow.stages.execute._resolve_branch_ref",
            "sandbox_executor.flow.stages.plan_decision._resolve_branch_ref",
        ) as resolve:
            assert flow_module._plan_budget_decision(ctx) is None
        resolve.assert_not_called()

    def test_budget_precheck_with_an_unresolvable_plan_ref_reads_no_ledger(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"})
        ctx.plan_branch = "I-1-parent/P-2-agent-model/_"
        with (
            patch("sandbox_executor.flow.stages.plan_decision._resolve_branch_ref", return_value=None),
            patch_lookup_sites(
                "sandbox_executor.flow.stages.execute._read_ledger_rows",
                "sandbox_executor.flow.stages.intent._read_ledger_rows",
                "sandbox_executor.flow.stages.plan._read_ledger_rows",
                "sandbox_executor.flow.stages.plan_decision._read_ledger_rows",
            ) as read_rows,
        ):
            assert flow_module._plan_budget_decision(ctx) is None
        read_rows.assert_not_called()

    def test_budget_precheck_ignores_rows_of_other_plans(self):
        rows = [
            {"plan_id": "P-2-agent-model", "entropy": 2.0, "plan_file": "plans/P-2-agent-model.md"},
            {"plan_id": "P-3-agent-model", "entropy": 12.6, "plan_file": "plans/P-3-agent-model.md"},
        ]
        files = {"plans/P-2-agent-model.md": "# Plan\n", "plans/P-3-agent-model.md": "# Plan\n"}
        assert self._decide(rows, files).decision == "execute"
        assert self._decide(rows[1:], files) is None

    def test_budget_precheck_reads_the_recorded_plan_file_and_the_intent_budget(self):
        rows = [{"plan_id": "P-2-agent-model", "entropy": 9.0, "plan_file": "plans/P-2-agent-model-v2.md"}]
        decision = self._decide(rows, {"plans/P-2-agent-model-v2.md": "# Plan\n" + SUB_INTENT_LIST})
        assert decision.decision == "decomposed"
        # No list and no budget on the row: the intent's budget (5.0, not the default) decides.
        decision = self._decide(rows, {"plans/P-2-agent-model-v2.md": "# Plan\n"})
        assert (decision.decision, decision.entropy_budget) == ("over_budget", 5.0)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(12.6, "12.6"), (5, "5"), (5.0, "5"), (True, "True"), (None, "unknown"), ("lots", "lots")],
        ids=str,
    )
    def test_format_entropy(self, value, expected):
        assert flow_module._format_entropy(value) == expected

    # --- Re-plan loop checkpoints ---------------------------------------------------------------------------------

    @staticmethod
    def _engine_run(
        ctx: FlowContext, plans: list[StageResult | BaseException], executes: list[StageResult]
    ) -> tuple[dict[str, StageResult], list[dict[str, Any]]]:
        """Run the engine with stub stages; return the results and every checkpoint the engine saved, in order."""
        plan_iter, exec_iter = iter(plans), iter(executes)
        saved: list[dict[str, Any]] = []

        def plan(context: FlowContext) -> StageResult:
            result = next(plan_iter)
            if isinstance(result, BaseException):
                raise result
            if result.status == StageStatus.SUCCESS:
                context.plan_branch = f"I-1-parent/P-{len(saved)}-agent-model/_"
            return result

        def snapshot(context: FlowContext, filepath: str | None = None) -> str:
            saved.append(json.loads(json.dumps(context.to_dict())))
            return filepath or "checkpoint.json"

        intent = StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS)
        with (
            patch("sandbox_executor.flow.pipeline.run_intent_stage", return_value=intent),
            patch("sandbox_executor.flow.pipeline.run_plan_stage", side_effect=plan),
            patch("sandbox_executor.flow.pipeline.run_execute_stage", side_effect=lambda _c: next(exec_iter)),
            patch("sandbox_executor.flow.pipeline.save_checkpoint", side_effect=snapshot),
        ):
            results = PipelineEngine(ctx).run()
        return results, saved

    @staticmethod
    def _over_budget() -> StageResult:
        return StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.FAILED,
            error="Execute stage failed: over budget",
            payload={"over_budget": True, "entropy": 12.6, "entropy_budget": 5.0},
        )

    def test_replan_checkpoints_the_failed_replan(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, skip_review=True, skip_calibrate=True)
        ok = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
        no_plan = StageResult(stage=FlowStage.PLAN, status=StageStatus.FAILED, error="Plan stage failed: no plan")

        _results, saved = self._engine_run(ctx, [ok, no_plan], [self._over_budget()])

        last = saved[-1]
        assert last["replan_attempts"] == 1
        assert last["plan_branch"] is None
        assert last["stage_results"]["plan"]["status"] == "failed"
        assert sum("Entering stage: PLAN" in line for line in ctx.logs) == 2

    def test_replan_checkpoints_the_new_execute_result(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, skip_review=True, skip_calibrate=True)
        ok = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
        other = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.FAILED, error="Execute stage failed: push")

        _results, saved = self._engine_run(ctx, [ok, ok], [self._over_budget(), other])

        last = saved[-1]
        assert last["replan_attempts"] == 1
        assert last["stage_results"]["execute"]["error"] == "Execute stage failed: push"
        assert last["plan_branch"] == ctx.plan_branch is not None
        assert sum("Entering stage: EXECUTE" in line for line in ctx.logs) == 2

    def test_exhausted_replans_checkpoint_the_final_error(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, skip_review=True, skip_calibrate=True)
        ok = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
        attempts = MAX_REPLAN_ATTEMPTS + 1

        results, saved = self._engine_run(ctx, [ok] * attempts, [self._over_budget() for _ in range(attempts)])

        error = results["execute"].error
        assert error == (
            f"Execute stage failed: plan entropy 12.6 exceeds the entropy budget 5 after {MAX_REPLAN_ATTEMPTS} "
            "re-plan attempts and the plan lists no sub-intent list."
        )
        assert saved[-1]["stage_results"]["execute"]["error"] == error
        assert saved[-1]["stage_results"]["execute"]["payload"]["replan_attempts"] == MAX_REPLAN_ATTEMPTS
        assert any(line.endswith("] " + error) for line in ctx.logs)

    def test_decomposed_parent_checkpoints_its_skipped_review_and_calibration(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, skip_review=True, skip_calibrate=True)
        ok = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
        decomposed = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"decomposed": True})

        results, saved = self._engine_run(ctx, [ok], [decomposed])

        for stage in ("review", "calibrate"):
            assert any(
                line.endswith(f"] Stage '{stage}' skipped: the plan was decomposed into child pipelines.")
                for line in ctx.logs
            )
        assert results["calibrate"].payload == {"reason": "decomposed"}
        assert saved[-1]["stage_results"]["calibrate"]["payload"] == {"reason": "decomposed"}

    def test_undecomposed_success_runs_review_and_calibration_normally(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, skip_review=True, skip_calibrate=True)
        ok = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
        plain = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"execution_id": "E-1"})

        results, _saved = self._engine_run(ctx, [ok], [plain])

        assert results["review"].payload == {"skipped_by_flag": True}
        assert results["calibrate"].payload == {"skipped_by_flag": True}

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
            patch("sandbox_executor.flow.stages.plan.check_and_resolve_stage_conflicts"),
            patch("sandbox_executor.flow.stages.plan._snapshot_child_branches", return_value=set()),
            patch("sandbox_executor.flow.stages.plan._dispatch_role", side_effect=dispatch),
        ):
            res = run_plan_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert seen == [expected]
        assert "HOLON_REPLAN_FEEDBACK" not in os.environ

    # --- Re-plan request wording, exception paths and the executor's refusal exit code -------------------------------

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
    def test_replan_request_offers_a_sub_intent_list_only_below_the_depth_cap(self, flow_repos, tmp_path, depth, asked):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(12.6, ""), (2.0, "")])
        ctx = _budget_context(host, agents, 5.0, sub_intent_depth=depth)

        results = PipelineEngine(ctx).run()

        # The smaller re-plan runs normally, even for a leaf intent at the cap.
        assert results["execute"].status == StageStatus.SUCCESS
        assert agents.roles() == ["intent-creator", "planner", "planner", "executor"]
        assert (agents.replan_feedback_seen[1] or "").endswith(f"Re-plan attempt 1 of {MAX_REPLAN_ATTEMPTS}: {asked}")
        assert any(
            line.endswith(
                "] Stage 3: plan entropy 12.6 exceeds the entropy budget 5; returning to Stage 2 "
                f"(re-plan attempt 1 of {MAX_REPLAN_ATTEMPTS})."
            )
            for line in ctx.logs
        )

    def test_child_logs_reach_the_parent_when_a_child_pipeline_raises(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, intent_branch="I-1-parent/_")

        def boom(engine: PipelineEngine) -> dict[str, StageResult]:
            engine.context.log("child progress before the crash")
            raise RuntimeError("child crashed")

        with (
            patch.object(PipelineEngine, "run", autospec=True, side_effect=boom),
            pytest.raises(RuntimeError, match="child crashed"),
        ):
            flow_module._run_decomposed_plan(ctx, "2026-10-26T00:00:00Z", self._ROW, {"execution_id": "E-1"})

        assert any(
            line.startswith("[sub-intent first] ") and line.endswith("child progress before the crash")
            for line in ctx.logs
        )

    def test_replan_feedback_is_removed_when_the_replan_stage_is_interrupted(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, skip_review=True, skip_calibrate=True)
        ok = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)

        # An interrupt is not converted into a failed stage, so it leaves the re-plan loop by its finally clause.
        with pytest.raises(KeyboardInterrupt):
            self._engine_run(ctx, [ok, KeyboardInterrupt()], [self._over_budget()])

        assert ctx.replan_attempts == 1
        assert "replan_feedback" not in ctx.intent_data

    def test_a_replan_stage_that_does_not_succeed_ends_the_run_without_executing(self):
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, skip_review=True, skip_calibrate=True)
        ok = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
        skipped = StageResult(stage=FlowStage.PLAN, status=StageStatus.SKIPPED)

        self._engine_run(ctx, [ok, skipped], [self._over_budget()])

        assert sum("Entering stage: EXECUTE" in line for line in ctx.logs) == 1

    def test_replan_feedback_variable_is_restored_when_the_planner_dispatch_raises(
        self, flow_repos, tmp_path, monkeypatch
    ):
        host, origin = flow_repos
        monkeypatch.delenv("HOLON_REPLAN_FEEDBACK", raising=False)
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(2.0, "")])
        ctx = _budget_context(host, agents, 5.0)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        ctx.intent_data["replan_feedback"] = "smaller please"
        seen: list[str | None] = []

        def crash(*_args: Any, **_kwargs: Any) -> int:
            seen.append(os.environ.get("HOLON_REPLAN_FEEDBACK"))
            raise RuntimeError("docker crashed")

        with patch("sandbox_executor.flow.stages.plan._dispatch_role", side_effect=crash), pytest.raises(RuntimeError):
            run_plan_stage(ctx)

        assert seen == ["smaller please"]
        assert "HOLON_REPLAN_FEEDBACK" not in os.environ

    def test_refused_plan_exit_code_with_another_row_fails_the_stage(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(2.0, "")])
        ctx = _budget_context(host, agents, 5.0)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS
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

    def test_refused_plan_exit_code_names_itself_when_the_fetch_fails(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(12.6, "")])
        ctx = _budget_context(host, agents, 5.0)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS

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


def _plan_rows_on_origin(host: Path, plan_branch: str) -> list[dict[str, Any]]:
    plan_id = plan_branch.removesuffix("/_").rsplit("/", 1)[-1]
    rows = _ledger_rows(host, f"refs/remotes/origin/{plan_branch}", "plans.jsonl")
    return [row for row in rows if row.get("plan_id") == plan_id]


def _summary_on_origin(host: Path, execution_branch: str) -> dict[str, Any]:
    (summary,) = [
        row
        for row in _ledger_rows(host, f"refs/remotes/origin/{execution_branch}", "executions.jsonl")
        if row.get("entry_type") == "summary"
    ]
    return summary


class TestStage3ExecutesThePlanRowsDecision:
    """Goal 8 (a) and (g): the plan row is the single record of the decision, and in-budget plans are unchanged."""

    # The Stage 3 payload keys of an executed plan before the decision moved to the plan row.
    EXECUTED_PAYLOAD_KEYS: ClassVar[set[str]] = {
        "execution_id",
        "execution_branch",
        "status",
        "exit_code",
        "token_reduce",
        "changed_files",
        "test_pass_rate",
    }

    def test_decomposed_plan_runs_the_children_the_plan_row_records(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(9.0, SUB_INTENT_LIST), (2.0, "")])
        ctx = _budget_context(host, agents, 5.0)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS
        (plan_row,) = _plan_rows_on_origin(host, str(ctx.plan_branch))
        parent_execution = {}

        def spy(context, start_time, record, base_payload):
            parent_execution.update(branch=context.execution_branch, record=record)
            return run_decomposed(context, start_time, record, base_payload)

        run_decomposed = flow_module._run_decomposed_plan
        with patch("sandbox_executor.flow.stages.execute._run_decomposed_plan", side_effect=spy):
            res = run_execute_stage(ctx)

        assert res.status == StageStatus.SUCCESS, res.error
        assert plan_row["decomposition_decision"] == "decomposed"
        assert [c["slug"] for c in res.payload["children"]] == [i["slug"] for i in plan_row["sub_intents"]]
        assert parent_execution["record"]["sub_intents"] == plan_row["sub_intents"]
        assert parent_execution["record"]["plan_branch"] == ctx.plan_branch
        summary = _summary_on_origin(host, parent_execution["branch"])
        assert summary["status"] == "decomposed"
        assert "sub_intents" not in summary
        assert summary["plan_decision"]["sub_intent_count"] == len(plan_row["sub_intents"])
        # The planner's row already recorded the decision, so the flow wrote no plan row.
        assert len(_plan_rows_on_origin(host, str(ctx.plan_branch))) == 1

    def test_executor_disagreeing_with_the_plan_row_fails_without_children(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(2.0, "")])
        agents.executor_decision = DecompositionDecision(
            "decomposed", [{"slug": "rogue", "description": "Rogue", "goal": "Rogue"}], 2.0, 5.0
        )
        ctx = _budget_context(host, agents, 5.0)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS

        res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert res.error == (
            "Execute stage failed: the executor reported 'decomposed' but the plan row records 'execute'."
        )
        assert "children" not in res.payload
        assert agents.roles() == ["intent-creator", "planner", "executor"]
        assert len(agents.intent_payloads) == 1

    @pytest.mark.parametrize("planner_records_decision", [True, False], ids=["recorded", "older-planner-image"])
    def test_in_budget_plan_executes_exactly_as_before(self, flow_repos, tmp_path, planner_records_decision):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(5.0, "")])
        agents.planner_records_decision = planner_records_decision
        ctx = _budget_context(host, agents, 5.0)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS

        res = run_execute_stage(ctx)

        assert res.status == StageStatus.SUCCESS, res.error
        assert set(res.payload) == self.EXECUTED_PAYLOAD_KEYS
        assert agents.roles() == ["intent-creator", "planner", "executor"]
        summary = _summary_on_origin(host, str(ctx.execution_branch))
        assert summary["status"] == "success"
        assert "plan_decision" not in summary and "sub_intents" not in summary
        rows = _plan_rows_on_origin(host, str(ctx.plan_branch))
        assert rows[-1]["decomposition_decision"] == "execute"
        assert "sub_intents" not in rows[-1]
        # Entropy at the budget executes; the only difference for an older planner image is one back-filled row.
        if planner_records_decision:
            assert len(rows) == 1
        else:
            assert len(rows) == 2
            assert rows[1]["decision_recorded_by"] == "flow"
            assert {k: rows[1][k] for k in rows[0]} == rows[0]
