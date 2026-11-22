"""Each ExecuteStage step in isolation: a prepared ExecuteState, only that step's collaborators stubbed."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from sandbox_executor.decomposition_rule import DECISION_DECOMPOSED, DECISION_OVER_BUDGET, DecompositionDecision
from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.stages.execute import ExecuteStage
from sandbox_executor.git import EMPTY_DIFF_STATUS

from tests.flow.stages.test_execute import (
    EXEC_BRANCH,
    EXEC_ID,
    EXECUTE,
    PLAN,
    REMOTE_FAILURE,
    SUB_INTENT,
    SUPERSEDING_WRITER,
    host_remote_failure,
    stage3_context,
    summary_row,
)

PLAN_PREFIX = PLAN.removesuffix("/_")
EARLIER = f"{PLAN_PREFIX}/E-1-claude-agent-claude-opus-5-5/_"


def prepared_stage(context: FlowContext, resume_partial: bool = False, **state: Any) -> ExecuteStage:
    """An ExecuteStage whose state holds what the steps before the one under test would have set."""
    stage = ExecuteStage(context, resume_partial)
    row = state.pop("summary_row", summary_row())
    exit_code = state.pop("exit_code", 0)
    fields: dict[str, Any] = {
        "plan_prefix": PLAN_PREFIX,
        "repo_dir": context.target_worktree,
        "preferred_suffix": "-CLA-CO5.5",
        "target_branch": "main",
        "existing_executions": {EARLIER},
        "executor_args": [PLAN, "claude-agent", "claude-opus-5-5"],
        "exit_code": exit_code,
        "exec_branch": EXEC_BRANCH,
        "exec_id": EXEC_ID,
        "exec_ref": f"refs/remotes/origin/{EXEC_BRANCH}",
        "summary_row": row,
        "container_status": str(row["status"]) if row else None,
        "base_payload": {
            "execution_id": EXEC_ID,
            "execution_branch": EXEC_BRANCH,
            "status": str(row["status"]) if row else None,
            "exit_code": exit_code,
            "token_reduce": context.token_reduce,
        },
        "changed": ["src/app.py"],
        **state,
    }
    for name, value in fields.items():
        setattr(stage.state, name, value)
    return stage


@pytest.fixture
def ctx(tmp_path) -> FlowContext:
    return stage3_context(tmp_path)


def test_steps_run_in_the_documented_order(ctx):
    stage = ExecuteStage(ctx)
    assert [step.__name__ for step in stage.steps()] == [
        "preflight",
        "admit_and_refresh_plan",
        "resume_point",
        "salvage_partial",
        "dispatch_executor",
        "adopt_execution_branch",
        "check_container_outcome",
        "guard_empty_diff",
    ]


def test_run_stops_at_the_first_step_result_and_logs_the_start(ctx):
    stage = ExecuteStage(ctx)
    stop = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.FAILED, error="stop")
    with (
        patch.object(ExecuteStage, "admit_and_refresh_plan", return_value=stop),
        patch.object(ExecuteStage, "resume_point") as later,
    ):
        assert stage.run() is stop
    later.assert_not_called()
    assert "Starting Stage 3: Plan Execution" in ctx.logs[0]


class TestPreflight:
    def test_continues_and_records_the_plan_prefix_and_suffix(self, ctx):
        stage = ExecuteStage(ctx)
        assert stage.preflight() is None
        assert stage.state.plan_prefix == PLAN_PREFIX and stage.state.repo_dir == ctx.target_worktree
        assert stage.state.preferred_suffix.startswith("-")

    def test_stops_on_a_hard_veto_with_a_bare_result(self, ctx):
        with patch(f"{EXECUTE}.execution_veto_error", return_value="vetoed"):
            result = ExecuteStage(ctx).preflight()
        assert result is not None and result.error == "vetoed" and "execute" not in ctx.stage_results

    def test_stops_with_the_dry_run_result(self, tmp_path):
        ctx = stage3_context(tmp_path, dry_run=True)
        with (
            patch(f"{EXECUTE}.admission_stage_failure") as admission,
            patch(f"{EXECUTE}.plan_refresh_stage_failure") as refresh,
        ):
            result = ExecuteStage(ctx).preflight()
        assert result is not None and result.status == StageStatus.SUCCESS and result.payload["dry_run"] is True
        admission.assert_called_once()
        refresh.assert_called_once()


class TestAdmitAndRefreshPlan:
    @pytest.fixture(autouse=True)
    def _no_stage_conflicts(self):
        with patch(f"{EXECUTE}.check_and_resolve_stage_conflicts"):
            yield

    def test_continues_and_records_the_target_branch_and_decision(self, tmp_path):
        ctx = stage3_context(tmp_path, intent_data={"slug": "x", "target_branch": "develop"})
        decision = DecompositionDecision("execute")
        with (
            patch(f"{EXECUTE}._plan_budget_decision", return_value=decision),
            patch(f"{EXECUTE}.admission_stage_failure", return_value=None),
            patch(f"{EXECUTE}.plan_refresh_stage_failure", return_value=None),
        ):
            stage = prepared_stage(ctx, target_branch="")
            assert stage.admit_and_refresh_plan() is None
        assert stage.state.target_branch == "develop" and stage.state.precheck is decision

    def test_stops_on_an_over_budget_plan(self, ctx):
        decision = DecompositionDecision(DECISION_OVER_BUDGET, entropy=20.0, entropy_budget=15.0)
        with patch(f"{EXECUTE}._plan_budget_decision", return_value=decision):
            result = prepared_stage(ctx).admit_and_refresh_plan()
        assert result is not None and result.payload["over_budget"] is True
        assert result.payload["dispatched"] is False


class TestResumePoint:
    def test_stops_when_the_snapshot_cannot_be_fetched(self, ctx):
        with (
            patch(f"{EXECUTE}._snapshot_child_branches", return_value=None),
            patch(f"{EXECUTE}.fetch_base") as fetch,
        ):
            result = prepared_stage(ctx).resume_point()
        assert result is not None and "could not fetch origin branches under" in str(result.error)
        fetch.assert_not_called()

    def test_stops_when_the_base_cannot_be_fetched(self, ctx):
        with (
            patch(f"{EXECUTE}._snapshot_child_branches", return_value=set()),
            patch(f"{EXECUTE}.fetch_base", side_effect=host_remote_failure),
        ):
            result = prepared_stage(ctx).resume_point()
        assert result is not None and result.error == f"Execute stage failed: {REMOTE_FAILURE}"

    def test_continues_with_the_snapshot_and_the_executor_arguments(self, ctx):
        with (
            patch(f"{EXECUTE}._snapshot_child_branches", return_value={EARLIER}),
            patch(f"{EXECUTE}.fetch_base") as fetch,
        ):
            stage = prepared_stage(ctx, existing_executions=set(), executor_args=[])
            assert stage.resume_point() is None
        fetch.assert_called_once_with(ctx.target_worktree, "main")
        assert stage.state.existing_executions == {EARLIER}
        assert stage.state.executor_args == [PLAN, "claude-agent", "claude-opus-5-5"]


class TestSalvagePartial:
    def test_does_nothing_without_a_resume(self, ctx):
        with patch(f"{EXECUTE}.salvage_for_resume") as salvage:
            assert prepared_stage(ctx).salvage_partial() is None
        salvage.assert_not_called()

    def test_stops_undispatched_when_salvage_fails(self, ctx):
        with patch(f"{EXECUTE}.salvage_for_resume", return_value=(set(), "Execute stage failed: salvage")):
            result = prepared_stage(ctx, resume_partial=True).salvage_partial()
        assert result is not None and result.payload == {"dispatched": False}

    def test_appends_the_partial_branch_and_logs_the_resume(self, ctx):
        salvaged = {EARLIER, "salvaged"}
        with (
            patch(f"{EXECUTE}.salvage_for_resume", return_value=(salvaged, None)),
            patch(f"{EXECUTE}._resumable_partial_branch", return_value=EARLIER) as find,
        ):
            stage = prepared_stage(ctx, resume_partial=True)
            assert stage.salvage_partial() is None
        find.assert_called_once_with(ctx.target_worktree, salvaged)
        assert stage.state.existing_executions == salvaged and stage.state.executor_args[-1] == EARLIER
        assert any(f"Stage 3 resume: continuing partial work from {EARLIER}" in line for line in ctx.logs)


class TestDispatchExecutor:
    def _dispatch(self, ctx, exit_code: int = 0, held: set[str] | None = None, push_fails: bool = False):
        publish = {"side_effect": host_remote_failure} if push_fails else {"return_value": None}
        with (
            patch(f"{EXECUTE}._dispatch_role", return_value=exit_code) as dispatch,
            patch(f"{EXECUTE}.hold_unrecorded_executions", side_effect=lambda _c, _p, before: before | (held or set())),
            patch(f"{EXECUTE}.publish_new_child_branches", **publish),
        ):
            stage = prepared_stage(ctx, exit_code=-1)
            result = stage.dispatch_executor()
        dispatch.assert_called_once_with(ctx, "executor", [PLAN, "claude-agent", "claude-opus-5-5"])
        return stage, result

    def test_continues_after_a_clean_exit_and_push(self, ctx):
        stage, result = self._dispatch(ctx)
        assert result is None and stage.state.exit_code == 0 and stage.state.push_error is None

    def test_stops_on_a_failed_exit_before_the_push_error(self, ctx):
        stage, result = self._dispatch(ctx, exit_code=4, push_fails=True)
        assert result is not None and result.error == "Execute stage failed: executor agent exited with code 4."
        assert stage.state.push_error == REMOTE_FAILURE

    def test_stops_on_a_push_error(self, ctx):
        _, result = self._dispatch(ctx, push_fails=True)
        assert result is not None and result.error == f"Execute stage failed: {REMOTE_FAILURE}"
        assert result.payload == {"exit_code": 0}

    def test_stops_on_a_held_unrecorded_branch(self, ctx):
        _, result = self._dispatch(ctx, held={EXEC_BRANCH})
        assert result is not None and "kept local and not pushed" in str(result.error)


class TestAdoptExecutionBranch:
    def test_continues_with_the_branch_summary_row_and_base_payload(self, ctx):
        row = summary_row()
        with (
            patch(f"{EXECUTE}._fetch_branch_prefix_error", return_value=None),
            patch(f"{EXECUTE}._discover_child_branch", return_value=(EXEC_BRANCH, None)) as discover,
            patch(f"{EXECUTE}._track_local_branch") as track,
            patch(f"{EXECUTE}._read_ledger_rows", return_value=[row]),
        ):
            stage = prepared_stage(ctx, exec_branch="", summary_row=None, base_payload={}, exit_code=0)
            assert stage.adopt_execution_branch() is None
        assert discover.call_args.kwargs == {"before": {EARLIER}, "preferred_suffix": "-CLA-CO5.5"}
        track.assert_called_once_with(ctx, EXEC_BRANCH)
        state = stage.state
        assert (state.exec_branch, state.exec_id, state.summary_row) == (EXEC_BRANCH, EXEC_ID, row)
        assert state.exec_ref == f"refs/remotes/origin/{EXEC_BRANCH}" and ctx.execution_branch == EXEC_BRANCH
        assert state.base_payload == {
            "execution_id": EXEC_ID,
            "execution_branch": EXEC_BRANCH,
            "status": "success",
            "exit_code": 0,
            "token_reduce": ctx.token_reduce,
        }

    def test_stops_on_an_ambiguous_branch_without_an_exit_code(self, ctx):
        with (
            patch(f"{EXECUTE}._fetch_branch_prefix_error", return_value=None),
            patch(f"{EXECUTE}._discover_child_branch", return_value=(None, "two new branches")),
        ):
            result = prepared_stage(ctx).adopt_execution_branch()
        assert result is not None and result.error == "Execute stage failed: two new branches." and result.payload == {}


class TestCheckContainerOutcome:
    def test_continues_for_a_successful_container(self, ctx):
        assert prepared_stage(ctx).check_container_outcome() is None

    def test_stops_on_a_non_zero_exit(self, ctx):
        result = prepared_stage(ctx, exit_code=3).check_container_outcome()
        assert result is not None and result.error == "Execute stage failed: executor agent exited with code 3."
        assert result.payload["execution_branch"] == EXEC_BRANCH

    def test_stops_on_an_over_budget_row(self, ctx):
        row = summary_row(DECISION_OVER_BUDGET, entropy=20.0, entropy_budget=15.0)
        result = prepared_stage(ctx, summary_row=row).check_container_outcome()
        assert result is not None and result.payload["over_budget"] is True and result.payload["dispatched"] is True

    def test_runs_the_sub_intent_list_for_a_decomposed_plan(self, ctx):
        done = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS)
        decision = DecompositionDecision(DECISION_DECOMPOSED, sub_intents=[SUB_INTENT])
        stage = prepared_stage(ctx, summary_row=summary_row(DECISION_DECOMPOSED), precheck=decision)
        with patch(f"{EXECUTE}._run_decomposed_plan", return_value=done) as run_children:
            assert stage.check_container_outcome() is done
        assert run_children.call_args.args[2]["plan_branch"] == PLAN


class TestGuardEmptyDiff:
    def test_continues_with_the_changed_paths(self, ctx):
        with (
            patch(f"{EXECUTE}._resolve_branch_ref", return_value="plan-sha"),
            patch(f"{EXECUTE}.changed_paths", return_value=["a.py", "b.py"]) as changed,
        ):
            stage = prepared_stage(ctx, changed=[])
            assert stage.guard_empty_diff() is None
        changed.assert_called_once_with(ctx.target_worktree, "plan-sha", f"refs/remotes/origin/{EXEC_BRANCH}")
        assert stage.state.changed == ["a.py", "b.py"]

    def test_stops_when_the_plan_branch_cannot_be_resolved(self, ctx):
        with patch(f"{EXECUTE}._resolve_branch_ref", return_value=None):
            result = prepared_stage(ctx).guard_empty_diff()
        assert result is not None and "could not verify the execution diff" in str(result.error)

    def test_stops_and_supersedes_a_claimed_success_on_an_empty_diff(self, ctx):
        with (
            patch(f"{EXECUTE}._resolve_branch_ref", return_value="plan-sha"),
            patch(f"{EXECUTE}.changed_paths", return_value=[]),
            patch(SUPERSEDING_WRITER, return_value=None) as writer,
        ):
            result = prepared_stage(ctx).guard_empty_diff()
        assert result is not None and result.payload["status"] == EMPTY_DIFF_STATUS
        assert result.payload["superseding_row_recorded"] is True
        assert writer.call_args.args[1] == EXEC_BRANCH


class TestRecordExecution:
    def test_records_a_successful_stage(self, ctx):
        with patch(f"{EXECUTE}.touch_set_violation_result", return_value=None):
            result = prepared_stage(ctx).record_execution()
        assert result.status == StageStatus.SUCCESS and ctx.stage_results["execute"] is result
        assert result.payload["changed_files"] == 1 and result.payload["execution_id"] == EXEC_ID

    def test_stops_on_a_non_success_status(self, ctx):
        result = prepared_stage(ctx, summary_row=summary_row("failure")).record_execution()
        assert result.status == StageStatus.FAILED
        assert result.error == "Execute stage failed: executor agent recorded status 'failure': did it"

    def test_stops_on_a_missing_summary_row(self, ctx):
        result = prepared_stage(ctx, summary_row=None).record_execution()
        assert result.status == StageStatus.FAILED and "carries no executions.jsonl summary row" in str(result.error)
