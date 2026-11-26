"""Tests for the entropy budget handoff, bounded re-planning and sequential child flows of Stage 3."""

import dataclasses
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor import flow as flow_module
from sandbox_executor.budgets import MAX_REPLAN_ATTEMPTS, MAX_SUB_INTENT_DEPTH
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
from sandbox_executor.launcher.coherence import FlowProxyChoice

from tests.flow.decomposition_fakes import ChildReviews, decomposed_flow, parent_ctx
from tests.flow.helpers import (
    SUB_INTENT_LIST,
    BudgetRoleAgents,
    _all_intent_rows,
    _budget_context,
    _ledger_rows,
    stage4_halts_at_consensus,
)
from tests.flow.lookup_patch import patch_lookup_sites

_NO_PROXY = FlowProxyChoice(spawn_sidecar=False, attach_running_proxy=False)
_OVER_BUDGET_ERROR = (
    "Execute stage failed: plan entropy 12.6 exceeds the entropy budget 5 and the plan lists no sub-intent list."
)


@pytest.mark.usefixtures("no_origin_reconcile")
class TestEntropyBudgetHandoffInFlow:
    """Intent goal 4: no Stage 3 halt, budget handoff, bounded re-planning and sequential child pipelines."""

    @pytest.mark.parametrize(
        ("entropy", "expected_status"),
        [(2.0, StageStatus.SUCCESS), (12.6, StageStatus.FAILED)],
        ids=["normal", "over-budget"],
    )
    def test_stage3_never_halts_for_a_human_when_the_executor_row_decides(
        self, flow_repos, tmp_path, entropy, expected_status
    ):
        """Stage 3 ends in success or failure, never a halt, when only the executor's own row decides.

        The same holds where the plan row decides before dispatch, which the tests named here prove with the status
        they assert: a normal plan in ``test_plan_decision.py::test_in_budget_plan_executes_exactly_as_before``, a
        refused one in ``test_replan_whose_stage_2_fails_ends_the_run_in_stage_2`` and a decomposed one in
        ``test_explicit_sub_intent_list_runs_child_pipelines_in_order_and_records_each_child_pr``. A decomposed row
        without a readable plan row fails in
        ``test_plan_decision.py::test_decomposed_report_without_a_readable_plan_row_runs_no_child``.
        """
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(entropy, "")])
        ctx = _budget_context(host, agents, 5.0)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS

        # Model a plan the flow could not read before dispatch: the executor's own row decides.
        with patch("sandbox_executor.flow.stages.execute._plan_budget_decision", return_value=None):
            res = run_execute_stage(ctx)

        assert res.status != StageStatus.HALTED_FOR_HUMAN
        assert res.status == expected_status
        if expected_status == StageStatus.FAILED:
            assert res.payload["over_budget"] is True
            assert res.payload["dispatched"] is True
            assert (res.payload["entropy"], res.payload["entropy_budget"]) == (entropy, 5.0)
            assert res.error == _OVER_BUDGET_ERROR
            # The executor's row also carries the execution it recorded.
            assert res.payload["execution_branch"] == ctx.execution_branch is not None

    def test_intent_budget_16_persisted_on_the_plan_row_lets_plan_entropy_12_6_execute_normally(
        self, flow_repos, tmp_path
    ):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(12.6, "")], row_budget=True)
        ctx = _budget_context(host, agents, 16.0)

        with stage4_halts_at_consensus():
            results = PipelineEngine(ctx).run()

        assert results["execute"].status == StageStatus.SUCCESS
        assert agents.roles() == ["intent-creator", "planner", "executor"]
        assert ctx.replan_attempts == 0
        # The budget persisted on the plan row is the one enforced.
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

    def test_explicit_sub_intent_list_runs_child_pipelines_in_order_and_records_each_child_pr(
        self, flow_repos, tmp_path
    ):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(9.0, SUB_INTENT_LIST), (2.0, "")])
        ctx = _budget_context(host, agents, 5.0)

        with decomposed_flow(origin, tmp_path) as reviews:
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
        plan_branch = ctx.plan_branch
        for payload in agents.intent_payloads[1:]:
            # Nested under the plan branch, never the intent branch or main.
            assert payload["target_branch"] == plan_branch
            assert payload["parent_intent_id"] == plan_branch
            assert payload["branch"].startswith(plan_branch.removesuffix("/_") + "--")
            assert payload["entropy_budget"] == 5.0
            assert payload["sub_intent_depth"] == 1
            # Labels drive the PR title scope, so each child PR is scoped like its parent's.
            assert payload["labels"] == ["flow"]
        execute = results["execute"]
        assert execute.status == StageStatus.SUCCESS
        assert execute.payload["decomposed"] is True and execute.payload["integrated"] is True
        assert [c["slug"] for c in execute.payload["children"]] == children
        assert execute.payload["plan_branch"] == plan_branch
        assert execute.payload["parent_target_branch"] == "main"
        assert [e["status"] for e in execute.payload["children_progress"]] == ["merged", "merged"]
        for child in execute.payload["children"]:
            assert child["execution_branch"].startswith(child["intent_branch"].removesuffix("/_") + "/P-")
            assert child["merged"] is True
        # Each child PR targets the plan branch; Stage 4 of the parent raises the combined PR into main.
        assert [(r["slug"], r["base"]) for r in reviews.reviewed] == [
            ("add-the-parser", plan_branch),
            ("add-the-cli-flag", plan_branch),
            ("budget-test", "main"),
        ]
        assert reviews.reviewed[-1]["head"] == ctx.execution_branch == execute.payload["execution_branch"]
        summary = flow_module.format_decomposition_summary(execute)
        assert summary[0] == f"Decomposed into 2 sub-intent(s); each child PR was merged into '{plan_branch}':"
        assert (
            summary[1] == f"  1. add-the-parser: https://github.com/example/app/pull/101 (merged into '{plan_branch}')"
        )
        assert summary[-1] == (
            f"Combined PR: Stage 4 raises and reviews '{ctx.execution_branch}' (with '{plan_branch}' merged in) into "
            "'main'."
        )
        assert results["review"].status == StageStatus.HALTED_FOR_HUMAN
        assert results["calibrate"].payload == {"skipped_by_flag": True}
        assert any("[sub-intent add-the-parser]" in line for line in ctx.logs)
        # The executor wrote no intent rows: only the parent and the two children created through Stage 1 exist.
        assert sorted(r["slug"] for r in _all_intent_rows(host)) == sorted(["budget-test", *children])
        # Each child halted at its own review, which is a success, and was merged with the PR it recorded.
        ran = execute.payload["children"]
        assert [r["head"] for r in reviews.reviewed[:2]] == [c["execution_branch"] for c in ran]
        assert [(c["pr_url"], c["pr_number"]) for c in ran] == [
            ("https://github.com/example/app/pull/101", 101),
            ("https://github.com/example/app/pull/102", 102),
        ]
        assert [e["pr_number"] for e in execute.payload["children_progress"]] == [101, 102]
        assert [reviews.gh.prs[n]["state"] for n in (101, 102, 103)] == ["MERGED", "MERGED", "OPEN"]
        # The parent's own Stage 4 raised the combined PR into main and halted there for the human.
        assert reviews.gh.prs[103]["base"] == "main"

    @staticmethod
    def _assert_decomposed_child_nests_its_own_children(
        ctx: FlowContext, agents: BudgetRoleAgents, reviews: ChildReviews, execute: StageResult
    ) -> None:
        plan = ctx.plan_branch
        first, second = execute.payload["children"]
        assert first["decomposed"] is True
        assert "decomposed" not in second
        child_plan = first["plan_branch"]
        assert child_plan.startswith(first["intent_branch"].removesuffix("/_") + "/P-")
        grandchildren = first["children"]
        assert [g["slug"] for g in grandchildren] == ["add-the-parser", "add-the-cli-flag"]
        # Grandchildren nest under the first child's own plan branch, however fast the run.
        assert [g["intent_branch"] for g in grandchildren] == [
            child_plan.removesuffix("/_") + suffix for suffix in ("--1-add-the-parser/_", "--2-add-the-cli-flag/_")
        ]
        assert [p["sub_intent_depth"] for p in agents.intent_payloads[1:]] == [1, 2, 2, 1]
        assert [p["target_branch"] for p in agents.intent_payloads[2:4]] == [child_plan] * 2
        # The decomposed child raises its own combined PR into the parent's plan branch, which is then merged.
        assert [(r["slug"], r["base"]) for r in reviews.reviewed] == [
            ("add-the-parser", child_plan),
            ("add-the-cli-flag", child_plan),
            ("add-the-parser", plan),
            ("add-the-cli-flag", plan),
            ("budget-test", "main"),
        ]
        assert reviews.reviewed[2]["head"] == first["execution_branch"]
        summary = flow_module.format_decomposition_summary(execute)
        assert summary == [
            f"Decomposed into 2 sub-intent(s); each child PR was merged into '{plan}':",
            f"  1. add-the-parser: decomposed into 2 sub-intent(s), combined in {first['pr_url']} (merged into "
            f"'{plan}'):",
            f"     1. add-the-parser: {grandchildren[0]['pr_url']} (merged into '{child_plan}')",
            f"     2. add-the-cli-flag: {grandchildren[1]['pr_url']} (merged into '{child_plan}')",
            f"  2. add-the-cli-flag: {second['pr_url']} (merged into '{plan}')",
            f"Combined PR: Stage 4 raises and reviews '{ctx.execution_branch}' (with '{plan}' merged in) into 'main'.",
        ]

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
        assert flow_module._child_intent_branch(parent, index, slug).name == expected

    def test_child_that_itself_decomposes_in_the_same_second_gets_its_own_branches_and_lists_its_children(
        self, flow_repos, tmp_path
    ):
        host, origin = flow_repos
        # Parent and first child both list sub-intents; the two grandchildren and the second child execute. The first
        # grandchild shares its parent's slug, and a frozen clock creates every intent in the same second.
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(9.0, SUB_INTENT_LIST), (9.0, SUB_INTENT_LIST), (2.0, "")])
        ctx = _budget_context(host, agents, 5.0)

        with (
            patch("sandbox_executor.flow.stages.intent.time.time", return_value=1793098938.0),
            decomposed_flow(origin, tmp_path) as reviews,
        ):
            results = PipelineEngine(ctx).run()

        # Every intent was dispatched to the intent creator on a branch of its own: none reused another's.
        branches = [p["branch"] for p in agents.intent_payloads]
        assert len(branches) == len(set(branches)) == 5
        assert results["execute"].status == StageStatus.SUCCESS
        plan = ctx.plan_branch.removesuffix("/_")
        first = results["execute"].payload["children"][0]
        child_plan = first["plan_branch"].removesuffix("/_")
        assert branches == [
            "I-1793098938-budget-test",
            f"{plan}--1-add-the-parser",
            f"{child_plan}--1-add-the-parser",
            f"{child_plan}--2-add-the-cli-flag",
            f"{plan}--2-add-the-cli-flag",
        ]
        assert [p["sub_intent_depth"] for p in agents.intent_payloads[1:]] == [1, 2, 2, 1]
        assert [g["intent_branch"] for g in first["children"]] == [f"{b}/_" for b in branches[2:4]]
        self._assert_decomposed_child_nests_its_own_children(ctx, agents, reviews, results["execute"])

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
        # The refusal came from the plan row before dispatch: no executor container ran.
        refused = results["execute"]
        assert refused.status == StageStatus.FAILED
        assert refused.payload["over_budget"] is True
        assert refused.payload["dispatched"] is False
        assert (refused.payload["entropy"], refused.payload["entropy_budget"]) == (12.6, 5.0)
        assert refused.error == _OVER_BUDGET_ERROR
        assert "executor" not in agents.roles()
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
        assert execute.payload["children_progress"][0]["status"] == "failed"
        assert execute.payload["children_progress"][1]["status"] == "pending"
        summary = flow_module.format_decomposition_summary(execute)
        assert summary[0] == (
            f"Decomposed into 2 sub-intent(s); 1 of 2 ran and 0 merged into '{ctx.plan_branch}' before Stage 3 failed:"
        )
        assert len(summary) == 2
        assert not any(line.startswith("Combined PR") for line in summary)

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

    @pytest.mark.parametrize("sub_intents", [[], None, ["not-a-dict"]], ids=["empty", "missing", "no-dict-items"])
    def test_decomposed_row_without_sub_intent_items_fails_stage(self, tmp_path, sub_intents):
        ctx = parent_ctx(tmp_path)
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

    def test_over_budget_plan_recovers_after_one_replan_and_restores_a_pre_existing_replan_feedback_variable(
        self, flow_repos, tmp_path, monkeypatch
    ):
        host, origin = flow_repos
        monkeypatch.setenv("HOLON_REPLAN_FEEDBACK", "operator value")
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(12.6, ""), (4.0, "")])
        ctx = _budget_context(host, agents, 5.0)

        with stage4_halts_at_consensus():
            results = PipelineEngine(ctx).run()

        assert results["execute"].status == StageStatus.SUCCESS
        assert ctx.replan_attempts == 1
        assert agents.roles() == ["intent-creator", "planner", "planner", "executor"]
        assert "entropy 12.6" in (agents.replan_feedback_seen[1] or "")
        assert os.environ.get("HOLON_REPLAN_FEEDBACK") == "operator value"

    def test_only_a_recorded_decomposition_of_the_current_plan_resumes_its_children(self):
        plan = "I-1-parent/P-2-agent-model/_"
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent"}, plan_branch=plan)
        progress = [{"index": 1, "slug": "a", "intent_branch": "I-1-parent/P-2-agent-model--1-a/_", "status": "merged"}]
        resumed = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"resumed": True})
        fresh = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"fresh": True})
        cases = [
            ({"decomposed": True, "plan_branch": plan, "children_progress": progress}, resumed),
            # A re-plan produced another plan branch: the old plan's children are not resumed.
            ({"decomposed": True, "plan_branch": "I-1-parent/P-9-agent-model/_", "children_progress": progress}, fresh),
            # A depth refusal recorded no progress, so nothing can be resumed.
            ({"decomposed": True, "plan_branch": plan, "children_progress": []}, fresh),
        ]
        for payload, expected in cases:
            ctx.stage_results["execute"] = StageResult(
                stage=FlowStage.EXECUTE, status=StageStatus.FAILED, payload=payload
            )
            with (
                patch("sandbox_executor.flow.pipeline.resume_children", return_value=resumed) as resume,
                patch("sandbox_executor.flow.pipeline.run_execute_stage", return_value=fresh) as dispatch,
            ):
                assert PipelineEngine(ctx).execute_stage(FlowStage.EXECUTE) is expected
            assert (resume.called, dispatch.called) == (expected is resumed, expected is fresh)

    @pytest.mark.parametrize(("recorded", "expected"), [(-3, 0), ("-1", 0), (None, 0), ("deep", 0), ("2", 2)], ids=str)
    def test_sub_intent_depth_is_never_negative(self, recorded, expected):
        # A negative or unreadable depth counts as a root intent, so children start at depth 1.
        assert flow_module._sub_intent_depth({"sub_intent_depth": recorded}) == expected

    def test_child_pipeline_that_runs_no_stage_fails_the_parent(self, tmp_path):
        ctx = parent_ctx(tmp_path)
        ctx.intent_branch = "I-1-parent/_"
        row = {
            "status": "decomposed",
            "plan_branch": "I-1-parent/P-2-agent-model/_",
            "sub_intents": [{"slug": "first", "goal": "Add the parser"}, {"slug": "second", "goal": "Add the flag"}],
        }

        with (
            patch.object(PipelineEngine, "run", return_value={}) as run,
            patch("sandbox_executor.flow.stages.children._fetch_refspecs_error", return_value=None),
        ):
            res = flow_module._run_decomposed_plan(ctx, "2026-10-26T00:00:00Z", row, {"execution_id": "E-1"})

        assert res.status == StageStatus.FAILED
        assert res.error == "Execute stage failed: sub-intent 1/2 'first' failed at stage 'none': no stage ran"
        assert [c["slug"] for c in res.payload["children"]] == ["first"]
        assert run.call_count == 1

    def test_plan_branch_refresh_failure_fails_the_child_before_it_is_cut(self, tmp_path):
        ctx = parent_ctx(tmp_path)
        ctx.intent_branch = "I-1-parent/_"
        row = {
            "status": "decomposed",
            "plan_branch": "I-1-parent/P-2-agent-model/_",
            "sub_intents": [{"slug": "first", "goal": "Add the parser"}],
        }

        with (
            patch.object(PipelineEngine, "run", return_value={}) as run,
            patch("sandbox_executor.flow.stages.children._fetch_refspecs_error", return_value="fetch timed out"),
        ):
            res = flow_module._run_decomposed_plan(ctx, "2026-10-26T00:00:00Z", row, {"execution_id": "E-1"})

        assert res.status == StageStatus.FAILED
        assert "could not refresh plan branch 'I-1-parent/P-2-agent-model/_' before cutting it: fetch timed out" in (
            res.error or ""
        )
        assert run.call_count == 0

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
            "proxy_attach": True,
            "skip_push": True,
            "skip_calibrate": True,
            "max_review_iterations": 7,
            "review_ensemble_size": 5,
            "mitm_web": True,
            "allow_overlap": True,
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
            admission={"state": "admitted"},
            carried_review_findings=[{"id": "R1-F1", "severity": "nit", "summary": "A parent nit."}],
            review_scope_paths=["src/parent.py"],
            review_state={"gates": {"diff": {"approved": True, "reviewed_head": "a" * 40}}},
            merged_head_restart_allowed=False,
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
            "starting_branch": None,
            "starting_detached": False,
            "stage_results": {},
            "logs": [],
            "schema_version": flow_module.CURRENT_CONTEXT_SCHEMA_VERSION,
            "replan_attempts": 0,
            "review_iterations_done": 0,  # A child intent runs its own Stage 4, so its review count starts afresh.
            "admission": None,  # A child intent runs its own Stage 3 admission.
            "carried_review_findings": [],  # The parent's open findings belong to the parent's review.
            "review_scope_paths": [],  # A child intent starts with a full-diff Stage 4 review scope.
            "review_state": {},  # The parent's approval gates belong to the parent's review.
            "checkpoint_path": None,  # A child intent checkpoints at its own default path.
            "merged_head_restart_allowed": True,  # A child intent's Stage 5 has its own restart budget.
        }
        defaults = FlowContext(token_reduce=False)
        for name, value in parent_values.items():
            assert getattr(defaults, name) != value, f"{name}: parent value must differ from the default"

        # Pin proxy detection to the opposite of the parent's token_reduce and proxy_attach so a dropped copy cannot
        # pass by detection.
        with patch("sandbox_executor.launcher.coherence.resolve_flow_proxy_choice", return_value=_NO_PROXY):
            child = flow_module._child_context(parent, child_intent)

        assert set(expected) == {f.name for f in dataclasses.fields(FlowContext)}, "classify every new field"
        for name, value in expected.items():
            assert getattr(child, name) == value, name
        assert child.dispatcher is dispatcher

    def test_only_the_review_stage_constructs_a_halt(self):
        package_dir = Path(flow_module.__file__).parent
        source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(package_dir.rglob("*.py")))
        execute_body = source.split("def run_execute_stage(", 1)[1].split("\ndef ", 1)[0]
        assert "HALTED_FOR_HUMAN" not in execute_body
        assert source.count("status=StageStatus.HALTED_FOR_HUMAN") == 1
        review_body = source.split("def build_review_stage_outcome(", 1)[1].split("\ndef ", 1)[0]
        assert "status=StageStatus.HALTED_FOR_HUMAN" in review_body

    def test_child_branch_that_cannot_fit_fails_stage_three_with_the_named_limit(self, tmp_path):
        ctx = parent_ctx(tmp_path)
        long_plan_branch = f"I-1-{'a' * 300}/P-2-agent-model/_"
        row: dict[str, Any] = {
            "status": "decomposed",
            "plan_branch": long_plan_branch,
            "sub_intents": [{"slug": "child", "goal": "Do the child"}],
        }

        res = flow_module._run_decomposed_plan(ctx, "2026-10-26T00:00:00Z", row, {"execution_id": "E-1"})

        assert res.status == StageStatus.FAILED
        assert (res.error or "").startswith("Execute stage failed: ")
        assert "255" in (res.error or "")
        assert res.payload["decomposed"] is True
        assert res.payload["plan_branch"] == long_plan_branch
        assert res.payload["children"] == []
