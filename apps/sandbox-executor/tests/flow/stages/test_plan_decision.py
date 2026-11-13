"""Tests for the Stage 2/3 boundary: the plan row's recorded decomposition decision and its one-time back-fill."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import patch

import pytest
from sandbox_executor import flow as flow_module
from sandbox_executor.budgets import MAX_SUB_INTENT_DEPTH
from sandbox_executor.decomposition_rule import DecompositionDecision, InvalidPlanDecisionError, should_decompose
from sandbox_executor.flow import FlowContext, StageStatus, run_execute_stage, run_intent_stage, run_plan_stage
from sandbox_executor.flow.stages import plan_decision
from sandbox_executor.git import run_git

from tests.flow.decomposition_fakes import decomposed_flow
from tests.flow.helpers import SUB_INTENT_LIST, BudgetRoleAgents, _budget_context, _git, _ledger_rows

PLANS_LEDGER = "holon-knowledge/ledger/plans.jsonl"


def _planned(host: Path, origin: Path, tmp_path: Path, plan: tuple[float, str], *, records: bool) -> FlowContext:
    """Run Stages 1 and 2 against the fixture origin; the fake planner records the decision unless ``records`` is
    False (an older planner image)."""
    agents = BudgetRoleAgents(tmp_path, origin, plans=[plan])
    agents.planner_records_decision = records
    ctx = _budget_context(host, agents, 5.0)
    assert run_intent_stage(ctx).status == StageStatus.SUCCESS
    assert run_plan_stage(ctx).status == StageStatus.SUCCESS
    return ctx


def _origin_tip(host: Path, branch: str) -> str:
    _git(host, "fetch", "-q", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
    return _git(host, "rev-parse", f"refs/remotes/origin/{branch}")


def _origin_plan_rows(host: Path, ctx: FlowContext) -> list[dict[str, Any]]:
    _origin_tip(host, str(ctx.plan_branch))
    plan_id = str(ctx.plan_branch).removesuffix("/_").rsplit("/", 1)[-1]
    rows = _ledger_rows(host, f"refs/remotes/origin/{ctx.plan_branch}", "plans.jsonl")
    return [row for row in rows if row.get("plan_id") == plan_id]


def _origin_ledger_bytes(host: Path, ctx: FlowContext) -> str:
    _origin_tip(host, str(ctx.plan_branch))
    return _git(host, "show", f"refs/remotes/origin/{ctx.plan_branch}:{PLANS_LEDGER}")


class TestRecordedPlanDecision:
    """Goal 8 (a), flow side: a recorded decision is read as is and nothing is written."""

    def test_recorded_decomposed_row_is_returned_without_a_write(self, flow_repos, tmp_path):
        host, origin = flow_repos
        ctx = _planned(host, origin, tmp_path, (9.0, SUB_INTENT_LIST), records=True)
        tip = _origin_tip(host, str(ctx.plan_branch))
        (row,) = _origin_plan_rows(host, ctx)

        decision = plan_decision._plan_budget_decision(ctx)

        assert decision == DecompositionDecision.from_plan_row(row)
        assert decision is not None and decision.decision == "decomposed"
        assert [item["slug"] for item in decision.sub_intents] == ["add-the-parser", "add-the-cli-flag"]
        assert _origin_tip(host, str(ctx.plan_branch)) == tip

    def test_recorded_decision_is_never_re_decided_from_the_plan_file(self, flow_repos, tmp_path):
        """A row recording ``execute`` is executed even though its plan file lists sub-intents."""
        host, origin = flow_repos
        agents_ctx = _planned(host, origin, tmp_path, (2.0, ""), records=True)
        with patch.object(plan_decision, "_git_show_text", return_value="# Plan\n" + SUB_INTENT_LIST) as show:
            decision = plan_decision._plan_budget_decision(agents_ctx)
        assert decision is not None and decision.decision == "execute"
        show.assert_not_called()

    def test_corrupt_recorded_decision_raises_and_writes_nothing(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(2.0, "")])
        recorded = agents._recorded
        agents._recorded = lambda work, row, content: {
            **recorded(work, row, content),
            "decomposition_decision": "split",
        }
        ctx = _budget_context(host, agents, 5.0)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS
        tip = _origin_tip(host, str(ctx.plan_branch))

        with pytest.raises(InvalidPlanDecisionError, match="records an invalid decomposition decision"):
            plan_decision._plan_budget_decision(ctx)
        result = run_execute_stage(ctx)

        assert result.status == StageStatus.FAILED
        assert result.error is not None and result.error.startswith("Execute stage failed: the plan row of P-")
        assert result.payload["dispatched"] is False
        assert agents.roles() == ["intent-creator", "planner"]
        assert _origin_tip(host, str(ctx.plan_branch)) == tip


class TestPlanDecisionBackfill:
    """An older planner image's row is decided by the same rule and back-filled exactly once."""

    def test_older_row_gains_exactly_one_superseding_row(self, flow_repos, tmp_path):
        host, origin = flow_repos
        ctx = _planned(host, origin, tmp_path, (9.0, SUB_INTENT_LIST), records=False)
        planner_ledger = _origin_ledger_bytes(host, ctx)
        (planner_row,) = _origin_plan_rows(host, ctx)
        assert "decomposition_decision" not in planner_row
        plan_content = _git(host, "show", f"refs/remotes/origin/{ctx.plan_branch}:{planner_row['plan_file']}")
        expected = should_decompose(planner_row, plan_content + "\n", ctx.intent_data)

        first = plan_decision._plan_budget_decision(ctx)
        tip = _origin_tip(host, str(ctx.plan_branch))
        second = plan_decision._plan_budget_decision(ctx)

        assert first == second == expected
        assert first is not None and first.decision == "decomposed"
        rows = _origin_plan_rows(host, ctx)
        assert len(rows) == 2
        superseding = rows[1]
        assert superseding["ledger_revision"] == 2
        assert superseding["decision_recorded_by"] == "flow"
        assert DecompositionDecision.from_plan_row(superseding) == expected
        # Every field of the planner's row is kept unchanged; the decision fields, revision and timestamp are added.
        assert {k: superseding[k] for k in planner_row} == planner_row
        # A pure append: the planner's line is a byte prefix of the new ledger, and the second read wrote nothing.
        assert _origin_ledger_bytes(host, ctx).startswith(planner_ledger)
        assert _origin_tip(host, str(ctx.plan_branch)) == tip
        # The local plan branch follows the pushed commit, and no temporary worktree is left behind.
        assert _git(host, "rev-parse", f"refs/heads/{ctx.plan_branch}") == tip
        assert len(_git(host, "worktree", "list").splitlines()) == 1

    def test_failed_back_fill_is_logged_and_the_decision_still_applies(self, flow_repos, tmp_path):
        host, origin = flow_repos
        ctx = _planned(host, origin, tmp_path, (12.6, ""), records=False)
        tip = _origin_tip(host, str(ctx.plan_branch))

        def refuse_push(args: list[str], **kwargs: Any) -> Any:
            if args[0] == "push":
                raise RuntimeError("push refused by the fixture")
            return run_git(args, **kwargs)

        with patch("sandbox_executor.flow.git_refs.run_git", side_effect=refuse_push):
            decision = plan_decision._plan_budget_decision(ctx)

        assert decision is not None and decision.decision == "over_budget"
        assert (decision.entropy, decision.entropy_budget) == (12.6, 5.0)
        assert any("could not record the decomposition decision" in line for line in ctx.logs)
        assert _origin_tip(host, str(ctx.plan_branch)) == tip
        assert len(_git(host, "worktree", "list").splitlines()) == 1

    def test_superseding_row_increments_a_recorded_revision(self, flow_repos, tmp_path):
        host, origin = flow_repos
        ctx = _planned(host, origin, tmp_path, (2.0, ""), records=False)
        (planner_row,) = _origin_plan_rows(host, ctx)
        decision = should_decompose(planner_row, "# Plan\n", ctx.intent_data)

        assert plan_decision._backfill_plan_row(ctx, {**planner_row, "ledger_revision": 4}, decision) is None

        superseding = _origin_plan_rows(host, ctx)[-1]
        assert superseding["ledger_revision"] == 5
        assert json.dumps(superseding["decomposition_decision"]) == '"execute"'
        assert "sub_intents" not in superseding


def _plan_rows_on_origin(host: Path, plan_branch: str) -> list[dict[str, Any]]:
    plan_id = plan_branch.removesuffix("/_").rsplit("/", 1)[-1]
    rows = _ledger_rows(host, f"refs/remotes/origin/{plan_branch}", "plans.jsonl")
    return [row for row in rows if row.get("plan_id") == plan_id]


def _summary_on_origin(host: Path, execution_branch: str) -> dict[str, Any]:
    # A decomposed execution also records superseding summary rows as its children merge; the first one is the row the
    # executor wrote when it acted on the plan row's decision.
    summaries = [
        row
        for row in _ledger_rows(host, f"refs/remotes/origin/{execution_branch}", "executions.jsonl")
        if row.get("entry_type") == "summary"
    ]
    assert summaries, "the execution branch carries no summary row"
    return summaries[0]


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
        "tests_passed",
        "tests_failed",
        "tests_errored",
        "test_exit_code",
        "test_run_error",
        "test_source",
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
        # The children run through their own consensus halt and merge into the plan branch, so review is not skipped.
        ctx.skip_review = False
        with (
            decomposed_flow(origin, tmp_path),
            patch("sandbox_executor.flow.stages.execute._run_decomposed_plan", side_effect=spy),
        ):
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

    @pytest.mark.parametrize("depth", [0, MAX_SUB_INTENT_DEPTH], ids=["top-level", "at-the-depth-limit"])
    def test_decomposed_report_without_a_readable_plan_row_runs_no_child(self, flow_repos, tmp_path, depth):
        host, origin = flow_repos
        agents = BudgetRoleAgents(tmp_path, origin, plans=[(2.0, SUB_INTENT_LIST)])
        ctx = _budget_context(host, agents, 5.0, sub_intent_depth=depth)
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
