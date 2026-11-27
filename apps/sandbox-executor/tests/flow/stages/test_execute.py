"""Tests for Stage 3 resumption from a failed partial execution branch."""

import subprocess
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sandbox_executor import flow as flow_module
from sandbox_executor.decomposition_rule import (
    DECISION_DECOMPOSED,
    DECISION_OVER_BUDGET,
    DecompositionDecision,
    InvalidPlanDecisionError,
)
from sandbox_executor.entrypoint.executor import OVER_BUDGET_EXIT_CODE
from sandbox_executor.flow import FlowContext, FlowStage, PipelineEngine, StageResult, StageStatus, run_execute_stage
from sandbox_executor.flow.host_remote import HostRemoteError
from sandbox_executor.flow_guard import SKIP_PUSH_DISPATCH_ERROR
from sandbox_executor.git import EMPTY_DIFF_FAILURE_REASON, EMPTY_DIFF_STATUS
from sandbox_executor.knowledge import ActionIndexDatabase, compute_context_fingerprint


class TestStage3PartialResumeDispatch:
    """A Stage 3 resume passes the newest failed execution branch carrying a partial_head as a fourth argument."""

    PLAN = "I-1-x/P-2-claude-agent-claude-opus-5-5/_"
    FAILED = "I-1-x/P-2-claude-agent-claude-opus-5-5/E-100-claude-agent-claude-opus-5-5/_"
    OLDER = "I-1-x/P-2-claude-agent-claude-opus-5-5/E-50-claude-agent-claude-opus-5-5/_"

    def _rows(self, with_partial_head: bool):
        def read_rows(_repo_dir, ref, _ledger):
            exec_id = ref.removesuffix("/_").rsplit("/", 1)[-1]
            row: dict[str, Any] = {"entry_type": "summary", "execution_id": exec_id, "status": "failure"}
            if with_partial_head:
                row["partial_head"] = "a" * 40
            return [row]

        return read_rows

    def _dispatched_args(self, tmp_path, *, with_partial_head: bool, from_stage=FlowStage.EXECUTE) -> list[str]:
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            plan_branch=self.PLAN,
            intent_data={"slug": "x"},
            agent="claude-agent",
            model="claude-opus-5-5",
        )
        ctx.stage_results["intent"] = StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS)
        ctx.stage_results["plan"] = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
        dispatched: list[list[str]] = []

        def dispatch(_ctx, role, args):
            dispatched.append(list(args))
            return 1  # ends the stage before any new branch must be adopted

        with (
            patch("sandbox_executor.flow.stages.execute.at_stage_boundary", return_value=None),
            patch("sandbox_executor.flow.stages.execute.fetch_base"),
            patch(
                "sandbox_executor.flow.stages.execute.salvage_for_resume",
                side_effect=lambda _ctx, _prefix, existing: (existing, None),
            ),
            patch("sandbox_executor.flow.stages.execute.publish_new_child_branches", return_value=[]),
            patch("sandbox_executor.flow.stages.execute._plan_budget_decision", return_value=None),
            patch(
                "sandbox_executor.flow.stages.execute._snapshot_child_branches", return_value={self.FAILED, self.OLDER}
            ),
            patch("sandbox_executor.flow.partial_head._read_ledger_rows", side_effect=self._rows(with_partial_head)),
            patch("sandbox_executor.flow.stages.execute._dispatch_role", side_effect=dispatch),
        ):
            if from_stage is None:
                run_execute_stage(ctx)
            else:
                PipelineEngine(ctx, from_stage=from_stage).run()
        assert len(dispatched) == 1
        return dispatched[0]

    def test_resume_with_partial_head_dispatches_four_arguments(self, tmp_path):
        args = self._dispatched_args(tmp_path, with_partial_head=True)
        assert args == [self.PLAN, "claude-agent", "claude-opus-5-5", self.FAILED]

    def test_resume_without_partial_head_dispatches_three_arguments(self, tmp_path):
        args = self._dispatched_args(tmp_path, with_partial_head=False)
        assert args == [self.PLAN, "claude-agent", "claude-opus-5-5"]

    def test_non_resumed_execute_dispatches_three_arguments_even_with_partial_head(self, tmp_path):
        args = self._dispatched_args(tmp_path, with_partial_head=True, from_stage=None)
        assert args == [self.PLAN, "claude-agent", "claude-opus-5-5"]

    def test_resumable_partial_branch_skips_non_failure_rows(self, tmp_path):
        def read_rows(_repo_dir, ref, _ledger):
            exec_id = ref.removesuffix("/_").rsplit("/", 1)[-1]
            status = "success" if exec_id.startswith("E-100") else "failure"
            return [{"entry_type": "summary", "execution_id": exec_id, "status": status, "partial_head": "b" * 40}]

        with patch("sandbox_executor.flow.partial_head._read_ledger_rows", side_effect=read_rows):
            branch = flow_module._resumable_partial_branch(str(tmp_path), {self.FAILED, self.OLDER})
        assert branch == self.OLDER

    @pytest.mark.parametrize("status", ["failure", EMPTY_DIFF_STATUS])
    def test_failed_partial_head_accepts_both_failure_statuses(self, tmp_path, status):
        row = {"entry_type": "summary", "execution_id": "E-100", "status": status, "partial_head": "c" * 40}
        with patch("sandbox_executor.flow.partial_head._read_ledger_rows", return_value=[row]):
            assert flow_module.failed_partial_head(str(tmp_path), "refs/remotes/origin/x", "E-100") == "c" * 40

    def test_resumable_partial_branch_resumes_an_empty_diff_failed_row(self, tmp_path):
        def read_rows(_repo_dir, ref, _ledger):
            exec_id = ref.removesuffix("/_").rsplit("/", 1)[-1]
            return [
                {
                    "entry_type": "summary",
                    "execution_id": exec_id,
                    "status": EMPTY_DIFF_STATUS,
                    "partial_head": "d" * 40,
                }
            ]

        with patch("sandbox_executor.flow.partial_head._read_ledger_rows", side_effect=read_rows):
            branch = flow_module._resumable_partial_branch(str(tmp_path), {self.FAILED, self.OLDER})
        assert branch == self.FAILED


# Characterisation of every Stage 3 exit: each test stubs Stage 3's collaborators on the execute module (the targets
# the pipeline tests patch too), overrides the one that drives the exit, and pins the message, status, exit code and
# payload.
EXECUTE = "sandbox_executor.flow.stages.execute"
# Where the empty-diff guard looks up its ledger writer; one place to update when that call site moves.
SUPERSEDING_WRITER = "sandbox_executor.flow.stages.empty_diff_outcome._append_superseding_execution_row"
PLAN = "I-1-x/P-2-claude-agent-claude-opus-5-5/_"
EXEC_ID = "E-3-claude-agent-claude-opus-5-5"
EXEC_BRANCH = f"I-1-x/P-2-claude-agent-claude-opus-5-5/{EXEC_ID}/_"
FAILED_PREFIX = "Execute stage failed:"
REMOTE_FAILURE = "host fetch of 'main' through remote 'origin' failed: origin unreachable"
NOTHING_KEPT = "No new local execution branch was found to keep."
SUB_INTENT = {"slug": "a", "description": "part a", "goal": "do a"}


def summary_row(status: str = "success", **extra: Any) -> dict[str, Any]:
    return {"entry_type": "summary", "execution_id": EXEC_ID, "status": status, "summary": "did it", **extra}


def stage3_context(tmp_path, **kwargs: Any) -> FlowContext:
    fields: dict[str, Any] = {
        "repo_dir": str(tmp_path),
        "plan_branch": PLAN,
        "intent_data": {"slug": "x"},
        "agent": "claude-agent",
        "model": "claude-opus-5-5",
        **kwargs,
    }
    return FlowContext(**fields)


def stage3_stubs(**overrides: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Patch keyword arguments per execute-module name: a successful run that changes one file, unless overridden."""
    stubs: dict[str, dict[str, Any]] = {
        "at_stage_boundary": {"return_value": None},
        "_plan_budget_decision": {"return_value": None},
        "admission_stage_failure": {"return_value": None},
        "plan_refresh_stage_failure": {"return_value": None},
        "_snapshot_child_branches": {"return_value": set()},
        "fetch_base": {"return_value": None},
        "salvage_for_resume": {"side_effect": lambda _ctx, _prefix, existing: (existing, None)},
        "_dispatch_role": {"return_value": 0},
        "hold_unrecorded_executions": {"side_effect": lambda _ctx, _prefix, before: set(before)},
        "publish_new_child_branches": {"return_value": None},
        "_fetch_branch_prefix_error": {"return_value": None},
        "_discover_child_branch": {"return_value": (EXEC_BRANCH, None)},
        "_track_local_branch": {"return_value": None},
        "_read_ledger_rows": {"return_value": [summary_row()]},
        "_resolve_branch_ref": {"return_value": "plan-sha"},
        "changed_paths": {"return_value": ["src/app.py"]},
        "touch_set_violation_result": {"return_value": None},
        "_run_decomposed_plan": {"return_value": None},
    }
    stubs.update(overrides)
    return stubs


def run_stage3(
    context: FlowContext, resume_partial: bool = False, write_error: str | None = None, **overrides: dict[str, Any]
):
    """Run Stage 3 under ``stage3_stubs(**overrides)``, the superseding-row writer returning ``write_error``.

    Returns the result and the mocks by name.
    """
    with ExitStack() as stack:
        mocks = {
            name: stack.enter_context(patch(f"{EXECUTE}.{name}", **kw))
            for name, kw in stage3_stubs(**overrides).items()
        }
        writer = stack.enter_context(patch(SUPERSEDING_WRITER, return_value=write_error))
        mocks["_append_superseding_execution_row"] = writer
        result = run_execute_stage(context, resume_partial=resume_partial)
    return result, mocks


def assert_failed(result: StageResult, *fragments: str) -> None:
    assert result.status == StageStatus.FAILED
    assert result.error is not None and result.error.startswith(FAILED_PREFIX), result.error
    for fragment in fragments:
        assert fragment in result.error


def host_remote_failure(*_args: Any) -> None:
    raise HostRemoteError("fetch", "main", "origin", "origin unreachable")


class TestStage3ExitsBeforeDispatch:
    """Exits taken before the executor container is dispatched."""

    def test_missing_plan_branch_fails_without_a_failed_prefix(self, tmp_path):
        ctx = stage3_context(tmp_path, plan_branch=None)
        result, mocks = run_stage3(ctx)
        assert result.status == StageStatus.FAILED
        assert result.error == "Cannot run Execute stage without an active plan_branch in FlowContext."
        assert result.payload == {} and "execute" not in ctx.stage_results
        mocks["_dispatch_role"].assert_not_called()

    def test_a_runner_without_a_ticker_fails(self, tmp_path):
        result, mocks = run_stage3(stage3_context(tmp_path, agent="no-such-agent"))
        assert_failed(result)
        mocks["_dispatch_role"].assert_not_called()

    @pytest.mark.parametrize("is_shadow", [False, True])
    def test_a_deterministic_failure_veto_blocks_unless_shadow(self, tmp_path, is_shadow):
        db_path = tmp_path / "holon-knowledge" / ".index" / "action_index.db"
        db_path.parent.mkdir(parents=True)
        fingerprint = compute_context_fingerprint(target=PLAN, tool="claude-agent", command_prefix="pytest")
        conn = ActionIndexDatabase(db_path=str(db_path)).get_connection()
        with conn:
            conn.execute(
                "INSERT INTO active_vetoes (context_fingerprint, signature_hash, reproduction_count, state,"
                " decay_weight, is_shadow, created_at, updated_at)"
                " VALUES (?, 'sig', 3, 'active', 1.0, ?, 'now', 'now')",
                (fingerprint, int(is_shadow)),
            )
        conn.close()
        ctx = stage3_context(tmp_path)
        result, mocks = run_stage3(ctx)
        if is_shadow:
            assert result.status == StageStatus.SUCCESS
            assert any("[VETO SHADOW WARNING]" in line for line in ctx.logs)
            return
        assert result.status == StageStatus.FAILED and result.payload == {}
        assert result.error == f"Execution blocked by deterministic failure veto for context fingerprint: {fingerprint}"
        assert "execute" not in ctx.stage_results
        mocks["_dispatch_role"].assert_not_called()

    def test_dry_run_names_the_branch_and_never_dispatches(self, tmp_path):
        ctx = stage3_context(tmp_path, dry_run=True, token_reduce=True)
        result, mocks = run_stage3(ctx)
        assert result.status == StageStatus.SUCCESS
        assert set(result.payload) == {"execution_id", "execution_branch", "token_reduce", "dry_run"}
        assert result.payload["dry_run"] is True and result.payload["token_reduce"] is True
        assert (
            result.payload["execution_branch"]
            == f"I-1-x/P-2-claude-agent-claude-opus-5-5/{result.payload['execution_id']}/_"
        )
        assert ctx.execution_branch == result.payload["execution_branch"]
        assert ctx.stage_results["execute"] is result
        assert any("Stage 3 (dry run): executor dispatch skipped" in line for line in ctx.logs)
        mocks["admission_stage_failure"].assert_called_once()
        mocks["plan_refresh_stage_failure"].assert_called_once()
        mocks["_dispatch_role"].assert_not_called()

    def test_skip_push_is_refused(self, tmp_path):
        result, mocks = run_stage3(stage3_context(tmp_path, skip_push=True))
        assert result.status == StageStatus.FAILED and result.error == SKIP_PUSH_DISPATCH_ERROR
        mocks["at_stage_boundary"].assert_not_called()

    def test_an_invalid_plan_decision_fails_undispatched(self, tmp_path):
        error = "Execute stage failed: the plan row records 'bogus'."
        result, _ = run_stage3(
            stage3_context(tmp_path), _plan_budget_decision={"side_effect": InvalidPlanDecisionError(error)}
        )
        assert result.error == error and result.payload == {"dispatched": False}

    def test_an_over_budget_plan_fails_undispatched(self, tmp_path):
        decision = DecompositionDecision(DECISION_OVER_BUDGET, entropy=20.0, entropy_budget=15.0)
        result, mocks = run_stage3(stage3_context(tmp_path), _plan_budget_decision={"return_value": decision})
        assert_failed(result, "exceeds the entropy budget")
        assert result.payload["over_budget"] is True and result.payload["dispatched"] is False
        mocks["_dispatch_role"].assert_not_called()

    def test_an_over_deep_sub_intent_list_fails_undispatched(self, tmp_path):
        decision = DecompositionDecision(DECISION_DECOMPOSED, sub_intents=[SUB_INTENT])
        with patch(f"{EXECUTE}._sub_intent_depth_error", return_value="Execute stage failed: too deep."):
            result, mocks = run_stage3(stage3_context(tmp_path), _plan_budget_decision={"return_value": decision})
        assert result.error == "Execute stage failed: too deep." and result.payload == {"dispatched": False}
        mocks["_dispatch_role"].assert_not_called()

    @pytest.mark.parametrize("refusing", ["admission_stage_failure", "plan_refresh_stage_failure"])
    def test_an_admission_or_plan_refresh_refusal_is_returned(self, tmp_path, refusing):
        refusal = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.FAILED, error="refused")
        result, mocks = run_stage3(stage3_context(tmp_path), **{refusing: {"return_value": refusal}})
        assert result is refusal
        mocks["_snapshot_child_branches"].assert_not_called()

    def test_an_unreadable_origin_snapshot_fails(self, tmp_path):
        result, mocks = run_stage3(stage3_context(tmp_path), _snapshot_child_branches={"return_value": None})
        assert_failed(result, "could not fetch origin branches under I-1-x/P-2-claude-agent-claude-opus-5-5 before")
        assert result.payload == {}
        mocks["fetch_base"].assert_not_called()

    def test_a_base_fetch_failure_fails(self, tmp_path):
        result, mocks = run_stage3(stage3_context(tmp_path), fetch_base={"side_effect": host_remote_failure})
        assert result.error == f"Execute stage failed: {REMOTE_FAILURE}" and result.payload == {}
        mocks["_dispatch_role"].assert_not_called()

    def test_a_salvage_failure_on_resume_fails_undispatched(self, tmp_path):
        result, mocks = run_stage3(
            stage3_context(tmp_path),
            resume_partial=True,
            salvage_for_resume={"return_value": (set(), "Execute stage failed: salvage broke.")},
        )
        assert result.error == "Execute stage failed: salvage broke." and result.payload == {"dispatched": False}
        mocks["_dispatch_role"].assert_not_called()

    def test_a_resume_without_partial_work_logs_and_dispatches_three_arguments(self, tmp_path):
        ctx = stage3_context(tmp_path)
        with patch(f"{EXECUTE}._resumable_partial_branch", return_value=None):
            result, mocks = run_stage3(ctx, resume_partial=True)
        assert result.status == StageStatus.SUCCESS
        assert mocks["_dispatch_role"].call_args.args[2] == [PLAN, "claude-agent", "claude-opus-5-5"]
        assert any("no failed execution branch carries partial work" in line for line in ctx.logs)


class TestStage3ExitsAfterDispatch:
    """Exits taken once the executor container has run."""

    def test_a_held_unrecorded_branch_fails(self, tmp_path):
        result, mocks = run_stage3(stage3_context(tmp_path), hold_unrecorded_executions={"return_value": {EXEC_BRANCH}})
        assert_failed(result, f"'{EXEC_BRANCH}' carries no executions.jsonl summary row", "kept local and not pushed")
        assert result.payload["execution_branch"] == EXEC_BRANCH
        mocks["_fetch_branch_prefix_error"].assert_not_called()

    def test_a_failed_exit_is_reported_before_a_push_error(self, tmp_path):
        result, _ = run_stage3(
            stage3_context(tmp_path),
            _dispatch_role={"return_value": 2},
            publish_new_child_branches={"side_effect": host_remote_failure},
        )
        assert result.error == "Execute stage failed: executor agent exited with code 2."
        assert result.payload == {"exit_code": 2}

    def test_a_push_error_fails(self, tmp_path):
        result, mocks = run_stage3(
            stage3_context(tmp_path), publish_new_child_branches={"side_effect": host_remote_failure}
        )
        assert result.error == f"Execute stage failed: {REMOTE_FAILURE}. {NOTHING_KEPT}"
        assert result.payload == {"exit_code": 0, "recovery_refs": [], "recovery_pushed": False}
        mocks["_fetch_branch_prefix_error"].assert_not_called()

    def test_an_unfetchable_prefix_fails(self, tmp_path):
        result, _ = run_stage3(stage3_context(tmp_path), _fetch_branch_prefix_error={"return_value": "boom"})
        assert_failed(result, "the executor agent exited 0 but origin branches under", "could not be fetched", "boom")
        assert result.payload == {"exit_code": 0}

    def test_an_ambiguous_execution_branch_fails_without_an_exit_code(self, tmp_path):
        result, _ = run_stage3(stage3_context(tmp_path), _discover_child_branch={"return_value": (None, "two found")})
        assert result.error == "Execute stage failed: two found." and result.payload == {}

    def test_a_missing_execution_branch_fails(self, tmp_path):
        result, _ = run_stage3(stage3_context(tmp_path), _discover_child_branch={"return_value": (None, None)})
        assert_failed(result, f"no execution branch under {PLAN} was found on origin after the executor agent exited.")
        assert result.payload == {"exit_code": 0}

    def test_an_over_budget_container_row_fails_dispatched(self, tmp_path):
        row = summary_row(DECISION_OVER_BUDGET, entropy=20.0, entropy_budget=15.0)
        result, _ = run_stage3(
            stage3_context(tmp_path),
            _dispatch_role={"return_value": OVER_BUDGET_EXIT_CODE},
            _read_ledger_rows={"return_value": [row]},
        )
        assert_failed(result, "exceeds the entropy budget")
        assert result.payload["dispatched"] is True and result.payload["over_budget"] is True
        assert result.payload["execution_id"] == EXEC_ID and result.payload["exit_code"] == OVER_BUDGET_EXIT_CODE

    def test_an_over_budget_exit_without_its_row_fails_with_the_base_payload(self, tmp_path):
        ctx = stage3_context(tmp_path)
        result, _ = run_stage3(ctx, _dispatch_role={"return_value": OVER_BUDGET_EXIT_CODE})
        assert result.error == f"Execute stage failed: executor agent exited with code {OVER_BUDGET_EXIT_CODE}."
        assert result.payload == {
            "execution_id": EXEC_ID,
            "execution_branch": EXEC_BRANCH,
            "status": "success",
            "exit_code": OVER_BUDGET_EXIT_CODE,
            "token_reduce": ctx.token_reduce,
        }

    def test_a_decomposed_report_the_plan_row_disagrees_with_fails(self, tmp_path):
        result, mocks = run_stage3(
            stage3_context(tmp_path), _read_ledger_rows={"return_value": [summary_row(DECISION_DECOMPOSED)]}
        )
        assert_failed(result, "the executor reported 'decomposed' but the plan row records 'no readable decision'.")
        assert result.payload["execution_branch"] == EXEC_BRANCH
        mocks["_run_decomposed_plan"].assert_not_called()

    def test_a_decomposed_report_runs_the_sub_intent_list(self, tmp_path):
        decision = DecompositionDecision(DECISION_DECOMPOSED, sub_intents=[SUB_INTENT])
        done = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS)
        with patch(f"{EXECUTE}._sub_intent_depth_error", return_value=None):
            result, mocks = run_stage3(
                stage3_context(tmp_path),
                _plan_budget_decision={"return_value": decision},
                _read_ledger_rows={"return_value": [summary_row(DECISION_DECOMPOSED)]},
                _run_decomposed_plan={"return_value": done},
            )
        assert result is done
        record = mocks["_run_decomposed_plan"].call_args.args[2]
        assert record["plan_branch"] == PLAN and record["decomposition_decision"] == DECISION_DECOMPOSED

    def test_a_sync_conflict_row_resolves_and_completes_for_review(self, tmp_path):
        row = summary_row("sync_conflict", conflicted_paths=["src/app.py"])
        result, mocks = run_stage3(stage3_context(tmp_path), _read_ledger_rows={"return_value": [row]})
        assert result.status == StageStatus.SUCCESS
        assert result.payload["sync_conflict_resolved"] is True
        assert result.payload["conflicted_paths"] == ["src/app.py"]
        mocks["changed_paths"].assert_called_once()

    def test_a_blocked_sync_conflict_row_fails_closed(self, tmp_path):
        row = summary_row("sync_conflict", conflicted_paths=["src/app.py"])
        blocked = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.FAILED, error="Execute stage failed: blocked")
        with patch("sandbox_executor.flow.stages.sync_conflict.at_stage_boundary", return_value=blocked):
            result, mocks = run_stage3(stage3_context(tmp_path), _read_ledger_rows={"return_value": [row]})
        assert result.status == StageStatus.FAILED
        assert result.payload["sync_conflict"] is True
        assert result.payload["conflicted_paths"] == ["src/app.py"]
        mocks["changed_paths"].assert_not_called()

    def test_an_unresolvable_plan_branch_fails_the_diff_check(self, tmp_path):
        result, mocks = run_stage3(stage3_context(tmp_path), _resolve_branch_ref={"return_value": None})
        assert_failed(result, "could not verify the execution diff, so it is treated as a failure:", f"'{PLAN}'")
        assert result.payload["execution_branch"] == EXEC_BRANCH
        mocks["changed_paths"].assert_not_called()

    def test_a_failed_diff_fails_the_diff_check(self, tmp_path):
        result, _ = run_stage3(stage3_context(tmp_path), changed_paths={"side_effect": TimeoutError("git timed out")})
        assert_failed(result, "could not verify the execution diff", "git timed out")

    def test_a_non_empty_diff_without_a_summary_row_fails(self, tmp_path):
        result, _ = run_stage3(stage3_context(tmp_path), _read_ledger_rows={"return_value": []})
        assert (
            result.error
            == f"Execute stage failed: '{EXEC_BRANCH}' carries no executions.jsonl summary row for {EXEC_ID}."
        )
        assert result.payload["status"] is None

    def test_a_non_success_status_fails(self, tmp_path):
        result, _ = run_stage3(stage3_context(tmp_path), _read_ledger_rows={"return_value": [summary_row("failure")]})
        assert result.error == "Execute stage failed: executor agent recorded status 'failure': did it"
        assert result.payload["status"] == "failure"

    def test_a_touch_set_violation_is_returned(self, tmp_path):
        violation = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.FAILED, error="outside")
        result, mocks = run_stage3(stage3_context(tmp_path), touch_set_violation_result={"return_value": violation})
        assert result is violation
        assert mocks["touch_set_violation_result"].call_args.args[3] == ["src/app.py"]

    def test_success_records_the_stage_result(self, tmp_path):
        ctx = stage3_context(tmp_path, admission={})
        row = summary_row(tests_passed=3, tests_failed=0)
        result, _ = run_stage3(ctx, _read_ledger_rows={"return_value": [row]})
        assert result.status == StageStatus.SUCCESS and result.error is None
        assert ctx.stage_results["execute"] is result and ctx.execution_branch == EXEC_BRANCH
        assert result.payload["changed_files"] == 1 and result.payload["execution_id"] == EXEC_ID
        assert result.payload["exit_code"] == 0 and result.payload["status"] == "success"
        assert "plan_refreshed_to" not in result.payload
        assert any(f"Stage 3 completed: Execution branch '{EXEC_BRANCH}' changes 1 path(s)." in x for x in ctx.logs)


class TestStage3EmptyDiffGuard:
    """An empty diff fails the stage; a superseding row is recorded only over a container-claimed success."""

    def test_without_a_summary_row_no_row_is_recorded(self, tmp_path):
        result, mocks = run_stage3(
            stage3_context(tmp_path), _read_ledger_rows={"return_value": []}, changed_paths={"return_value": []}
        )
        assert_failed(
            result, f"carries no executions.jsonl summary row for {EXEC_ID}, so no superseding row was recorded"
        )
        assert result.error.endswith("; no PR will be created or updated.")
        assert result.payload["status"] == EMPTY_DIFF_STATUS and result.payload["container_status"] is None
        assert result.payload["superseding_row_recorded"] is False
        mocks["_append_superseding_execution_row"].assert_not_called()

    def test_an_already_failed_container_row_is_not_superseded(self, tmp_path):
        result, mocks = run_stage3(
            stage3_context(tmp_path),
            _read_ledger_rows={"return_value": [summary_row("failure")]},
            changed_paths={"return_value": []},
        )
        assert_failed(result, "the container already recorded status 'failure': did it; no superseding row was written")
        assert result.payload["container_status"] == "failure" and result.payload["superseding_row_recorded"] is False
        mocks["_append_superseding_execution_row"].assert_not_called()

    @pytest.mark.parametrize("write_error", [None, "push rejected"])
    def test_a_claimed_success_is_superseded(self, tmp_path, write_error):
        ctx = stage3_context(tmp_path)
        result, mocks = run_stage3(ctx, write_error=write_error, changed_paths={"return_value": []})
        assert result.status == StageStatus.FAILED
        assert result.payload["failure_reason"] == EMPTY_DIFF_FAILURE_REASON
        assert result.payload["superseding_row_recorded"] is (write_error is None)
        assert result.payload["container_status"] == "success" and result.payload["exit_code"] == 0
        row = mocks["_append_superseding_execution_row"].call_args.args[2]
        assert row["status"] == EMPTY_DIFF_STATUS and row["failure_reason"] == EMPTY_DIFF_FAILURE_REASON
        if write_error is None:
            assert_failed(result, f"recorded a superseding status '{EMPTY_DIFF_STATUS}' row")
        else:
            assert_failed(result, "failed, so the ledger still shows 'success': push rejected")
            assert any("could not record the empty-diff failure row" in line for line in ctx.logs)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _make_stage3_refresh_repo(
    tmp_path: Path, *, plan_files: dict[str, str] | None = None, main_files: dict[str, str] | None = None
) -> tuple[Path, str]:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = tmp_path / "host"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True, capture_output=True)
    for key, value in (("user.email", "t@holon.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(repo, "config", key, value)
    _git(repo, "checkout", "-q", "-b", "main")
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src/app.py").write_text("APP = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "checkout", "-q", "-b", PLAN, "origin/main")
    for rel, text in (plan_files or {"plans/P-2.md": "# plan\n"}).items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", "plan commit")
    _git(repo, "push", "-q", "origin", PLAN)
    _git(repo, "checkout", "-q", "main")
    for rel, text in (main_files or {"docs/new.md": "advanced\n"}).items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", "main advances")
    _git(repo, "push", "-q", "origin", "main")
    return repo, _git(repo, "rev-parse", "origin/main")


def _stage3_engine_context(repo: Path, prior_execution_branch: str | None = None) -> FlowContext:
    ctx = FlowContext(
        target_worktree=str(repo),
        plan_branch=PLAN,
        intent_branch="I-1-x/_",
        execution_branch=prior_execution_branch,
        intent_data={"slug": "x", "target_branch": "main"},
        agent="claude-agent",
        model="claude-opus-5-5",
        admission=None,
    )
    ctx.stage_results["intent"] = StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS)
    ctx.stage_results["plan"] = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
    return ctx


class TestStage3FromStagePlanRefresh:
    @pytest.mark.parametrize("with_prior_execution", [False, True])
    def test_from_stage_execute_refreshes_plan_branch_before_dispatch(self, tmp_path: Path, with_prior_execution: bool):
        repo, main_sha = _make_stage3_refresh_repo(tmp_path)
        prior_branch: str | None = None
        if with_prior_execution:
            prior_branch = "I-1-x/P-2-claude-agent-claude-opus-5-5/E-1-claude-agent-claude-opus-5-5/_"
            _git(repo, "push", "-q", "origin", f"refs/remotes/origin/{PLAN}:refs/heads/{prior_branch}")
            _git(repo, "fetch", "-q", "origin")
        ctx = _stage3_engine_context(repo, prior_execution_branch=prior_branch)
        seen_tips: list[str] = []

        def dispatch(_ctx, _role, _args):
            seen_tips.append(_git(repo, "rev-parse", f"origin/{PLAN}"))
            return 1

        with (
            patch(f"{EXECUTE}._plan_budget_decision", return_value=None),
            patch(f"{EXECUTE}.salvage_for_resume", side_effect=lambda _c, _p, existing: (existing, None)),
            patch(f"{EXECUTE}.publish_new_child_branches", return_value=[]),
            patch(f"{EXECUTE}._dispatch_role", side_effect=dispatch),
        ):
            PipelineEngine(ctx, from_stage=FlowStage.EXECUTE).run()

        assert len(seen_tips) == 1
        assert subprocess.run(["git", "merge-base", "--is-ancestor", main_sha, seen_tips[0]], cwd=repo).returncode == 0
        if prior_branch is not None:
            prior_tip = _git(repo, "rev-parse", f"origin/{prior_branch}")
            assert subprocess.run(["git", "merge-base", "--is-ancestor", main_sha, prior_tip], cwd=repo).returncode != 0

    def test_from_stage_execute_conflict_halts_fail_closed_before_dispatch(self, tmp_path: Path):
        repo, _ = _make_stage3_refresh_repo(
            tmp_path,
            plan_files={"src/app.py": "APP = 2\n"},
            main_files={"src/app.py": "APP = 3\n"},
        )
        old_plan_tip = _git(repo, "rev-parse", f"origin/{PLAN}")
        ctx = _stage3_engine_context(repo)
        dispatched: list[list[str]] = []

        with (
            patch(f"{EXECUTE}._plan_budget_decision", return_value=None),
            patch(f"{EXECUTE}._dispatch_role", side_effect=lambda _c, _r, args: dispatched.append(args) or 0),
        ):
            PipelineEngine(ctx, from_stage=FlowStage.EXECUTE).run()

        assert dispatched == []
        assert ctx.stage_results["execute"].status == StageStatus.FAILED
        assert _git(repo, "rev-parse", f"origin/{PLAN}") == old_plan_tip
        assert subprocess.run(["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=repo).returncode != 0
