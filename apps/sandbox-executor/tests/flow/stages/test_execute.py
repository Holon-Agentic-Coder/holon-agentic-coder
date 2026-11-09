"""Tests for Stage 3 resumption from a failed partial execution branch."""

from typing import Any
from unittest.mock import patch

import pytest
from sandbox_executor import flow as flow_module
from sandbox_executor.flow import FlowContext, FlowStage, PipelineEngine, StageResult, StageStatus, run_execute_stage
from sandbox_executor.git import EMPTY_DIFF_STATUS


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
            patch("sandbox_executor.flow.stages.execute.check_and_resolve_stage_conflicts"),
            patch("sandbox_executor.flow.stages.execute._plan_budget_decision", return_value=None),
            patch(
                "sandbox_executor.flow.stages.execute._snapshot_child_branches", return_value={self.FAILED, self.OLDER}
            ),
            patch("sandbox_executor.flow.stages.execute._read_ledger_rows", side_effect=self._rows(with_partial_head)),
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

        with patch("sandbox_executor.flow.stages.execute._read_ledger_rows", side_effect=read_rows):
            branch = flow_module._resumable_partial_branch(str(tmp_path), {self.FAILED, self.OLDER})
        assert branch == self.OLDER

    @pytest.mark.parametrize("status", ["failure", EMPTY_DIFF_STATUS])
    def test_failed_partial_head_accepts_both_failure_statuses(self, tmp_path, status):
        row = {"entry_type": "summary", "execution_id": "E-100", "status": status, "partial_head": "c" * 40}
        with patch("sandbox_executor.flow.stages.execute._read_ledger_rows", return_value=[row]):
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

        with patch("sandbox_executor.flow.stages.execute._read_ledger_rows", side_effect=read_rows):
            branch = flow_module._resumable_partial_branch(str(tmp_path), {self.FAILED, self.OLDER})
        assert branch == self.FAILED
