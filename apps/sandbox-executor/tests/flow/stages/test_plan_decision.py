"""Tests for the Stage 2/3 boundary: the plan row's recorded decomposition decision and its one-time back-fill."""

from __future__ import annotations

import json
from collections.abc import Callable
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

from tests.flow.helpers import SUB_INTENT_LIST, BudgetRoleAgents, _budget_context, _git, _ledger_rows
from tests.flow.stage_templates import AgentsSpec, StageStart

PLANS_LEDGER = "holon-knowledge/ledger/plans.jsonl"


def _planned(stage_start: Callable[..., StageStart], plan: tuple[float, str], *, records: bool) -> StageStart:
    """The flow after the real Stages 1 and 2 (a stage-start template); the fake planner records the decision unless
    ``records`` is False (an older planner image). These tests are about Stage 3's read of the plan row."""
    return stage_start("plan", AgentsSpec(plans=(plan,), planner_records_decision=records))


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

    def test_recorded_decomposed_row_is_returned_without_a_write(self, stage_start):
        start = _planned(stage_start, (9.0, SUB_INTENT_LIST), records=True)
        host, ctx = start.host, start.ctx
        tip = _origin_tip(host, str(ctx.plan_branch))
        (row,) = _origin_plan_rows(host, ctx)

        decision = plan_decision._plan_budget_decision(ctx)

        assert decision == DecompositionDecision.from_plan_row(row)
        assert decision is not None and decision.decision == "decomposed"
        assert [item["slug"] for item in decision.sub_intents] == ["add-the-parser", "add-the-cli-flag"]
        assert _origin_tip(host, str(ctx.plan_branch)) == tip

    def test_recorded_decision_is_never_re_decided_from_the_plan_file(self, stage_start):
        """A row recording ``execute`` is executed even though its plan file lists sub-intents."""
        start = _planned(stage_start, (2.0, ""), records=True)
        agents_ctx = start.ctx
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

    def test_older_row_gains_exactly_one_superseding_row(self, stage_start):
        start = _planned(stage_start, (9.0, SUB_INTENT_LIST), records=False)
        host, ctx = start.host, start.ctx
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

    def test_failed_back_fill_is_logged_and_the_decision_still_applies(self, stage_start):
        start = _planned(stage_start, (12.6, ""), records=False)
        host, ctx = start.host, start.ctx
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

    def test_superseding_row_increments_a_recorded_revision(self, stage_start):
        start = _planned(stage_start, (2.0, ""), records=False)
        host, ctx = start.host, start.ctx
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
    # An execution may also record superseding summary rows; the first one is the row the executor wrote when it acted
    # on the plan row's decision.
    summaries = [
        row
        for row in _ledger_rows(host, f"refs/remotes/origin/{execution_branch}", "executions.jsonl")
        if row.get("entry_type") == "summary"
    ]
    assert summaries, "the execution branch carries no summary row"
    return summaries[0]


class TestStage3ExecutesThePlanRowsDecision:
    """Goal 8 (a) and (g): the plan row is the single record of the decision, and in-budget plans are unchanged.

    The decomposed case runs the default decomposed scenario, so its assertions live in that scenario's single run:
    test_children.py's ``test_children_nest_under_the_plan_branch_...``.
    """

    # The Stage 3 payload keys of an executed plan before the decision moved to the plan row.
    EXECUTED_PAYLOAD_KEYS: ClassVar[set[str]] = {
        "execution_id",
        "execution_branch",
        "status",
        "exit_code",
        "token_reduce",
        "changed_files",
        "head_sha",
        "test_pass_rate",
        "tests_passed",
        "tests_failed",
        "tests_errored",
        "test_exit_code",
        "test_run_error",
        "test_source",
    }

    def test_executor_disagreeing_with_the_plan_row_fails_without_children(self, stage_start):
        start = stage_start("plan", AgentsSpec(plans=((2.0, ""),)))
        agents, ctx = start.agents, start.ctx
        agents.executor_decision = DecompositionDecision(
            "decomposed", [{"slug": "rogue", "description": "Rogue", "goal": "Rogue"}], 2.0, 5.0
        )

        res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert res.error == (
            "Execute stage failed: the executor reported 'decomposed' but the plan row records 'execute'."
        )
        assert "children" not in res.payload
        # The executor ran once (Stages 1 and 2 ran in the template) and no child intent was created.
        assert agents.roles() == ["executor"]
        assert agents.intent_payloads == []

    def test_decomposed_report_without_a_readable_plan_row_runs_no_child_at_the_depth_limit(self, stage_start):
        # The code does not branch on the depth here, so the top-level case (identical line and branch coverage) is
        # covered by this edge case.
        spec = AgentsSpec(plans=((2.0, SUB_INTENT_LIST),), intent_extra=(("sub_intent_depth", MAX_SUB_INTENT_DEPTH),))
        start = stage_start("plan", spec)
        agents, ctx = start.agents, start.ctx

        # With no pre-dispatch decision the executor's decomposed row carries no list (the plan row is the only
        # record), so the stage fails without running any child: the depth bound holds trivially.
        with patch("sandbox_executor.flow.stages.execute._plan_budget_decision", return_value=None):
            res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "the plan row records 'no readable decision'" in (res.error or "")
        assert "children" not in res.payload
        assert flow_module.format_decomposition_summary(res) == []
        # The executor ran once (Stages 1 and 2 ran in the template) and no child intent was created.
        assert agents.roles() == ["executor"]
        assert agents.intent_payloads == []

    @pytest.mark.parametrize("planner_records_decision", [True, False], ids=["recorded", "older-planner-image"])
    def test_in_budget_plan_executes_exactly_as_before(self, stage_start, planner_records_decision):
        start = _planned(stage_start, (5.0, ""), records=planner_records_decision)
        host, agents, ctx = start.host, start.agents, start.ctx

        res = run_execute_stage(ctx)

        assert res.status == StageStatus.SUCCESS, res.error
        assert set(res.payload) == self.EXECUTED_PAYLOAD_KEYS
        # Stages 1 and 2 ran in the template; Stage 3 dispatched the executor once.
        assert agents.roles() == ["executor"]
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


class TestPlanBudget:
    """PlanBudget owns locating the plan row, reading its decision and the one-time back-fill."""

    SITE = "sandbox_executor.flow.stages.plan_decision"
    PLAN_BRANCH = "I-1-parent/P-2-agent-model/_"

    def _context(self) -> FlowContext:
        ctx = FlowContext(token_reduce=False, intent_data={"slug": "parent", "entropy_budget": 5.0})
        ctx.plan_branch = self.PLAN_BRANCH
        return ctx

    def _row(self, **fields: Any) -> dict[str, Any]:
        return {"plan_id": "P-2-agent-model", "intent_branch": "I-1-parent/_", **fields}

    def test_locate_needs_a_plan_branch(self):
        with patch(f"{self.SITE}._resolve_branch_ref") as resolve:
            assert plan_decision.PlanBudget.locate(FlowContext(token_reduce=False)) is None
        resolve.assert_not_called()

    def test_locate_needs_a_resolvable_plan_ref(self):
        with patch(f"{self.SITE}._resolve_branch_ref", return_value=None):
            assert plan_decision.PlanBudget.locate(self._context()) is None

    def test_locate_names_the_plan_and_its_ref(self):
        with patch(f"{self.SITE}._resolve_branch_ref", return_value="refs/remotes/origin/plan"):
            budget = plan_decision.PlanBudget.locate(self._context())
        assert budget is not None
        assert (budget.plan_branch, budget.plan_ref, budget.plan_id) == (
            self.PLAN_BRANCH,
            "refs/remotes/origin/plan",
            "P-2-agent-model",
        )

    def test_latest_row_is_the_newest_row_of_this_plan(self):
        budget = plan_decision.PlanBudget(self._context(), self.PLAN_BRANCH, "refs/remotes/origin/plan")
        rows = [self._row(entropy=1.0), self._row(entropy=2.0), {**self._row(entropy=3.0), "plan_id": "P-9-other"}]
        with patch(f"{self.SITE}._read_ledger_rows", return_value=rows) as read:
            assert budget.latest_row() == self._row(entropy=2.0)
        read.assert_called_once_with(budget.context.target_worktree, "refs/remotes/origin/plan", "plans.jsonl")
        with patch(f"{self.SITE}._read_ledger_rows", return_value=[]):
            assert budget.latest_row() is None

    def test_a_recorded_decision_is_returned_without_a_back_fill(self):
        recorded = should_decompose(self._row(entropy=2.0), "# Plan\n", {"entropy_budget": 5.0})
        budget = plan_decision.PlanBudget(self._context(), self.PLAN_BRANCH, "refs/remotes/origin/plan")
        with (
            patch(f"{self.SITE}._read_ledger_rows", return_value=[self._row(**recorded.to_plan_fields())]),
            patch(f"{self.SITE}._backfill_plan_row") as backfill,
        ):
            assert budget.decision() == recorded
        backfill.assert_not_called()

    def test_an_undecided_row_is_decided_and_back_filled_once(self):
        budget = plan_decision.PlanBudget(self._context(), self.PLAN_BRANCH, "refs/remotes/origin/plan")
        row = self._row(entropy=2.0, plan_file="plans/P-2-agent-model.md")
        with (
            patch(f"{self.SITE}._read_ledger_rows", return_value=[row]),
            patch(f"{self.SITE}._git_show_text", return_value="# Plan\n"),
            patch(f"{self.SITE}._backfill_plan_row", return_value=None) as backfill,
        ):
            decision = budget.decision()
        assert decision is not None and decision.decision == "execute"
        backfill.assert_called_once_with(budget.context, row, decision)
