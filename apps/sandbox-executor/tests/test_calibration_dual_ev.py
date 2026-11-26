"""Unit tests for dual Actual EV calibration with and without pipeline/engine bugs."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.calibration import (
    ActualMetrics,
    EntropyFactors,
    PredictedMetrics,
    compute_calibration_deltas,
    format_markdown_report,
    parse_actual_metrics,
)
from sandbox_executor.calibration_ev import evaluate_dual_ev_outcomes
from sandbox_executor.calibration_rows import is_infrastructure_failure, row_stage


class TestCalibrationDualEV(unittest.TestCase):
    """Test suite for Dual Actual EV computation, bug drag, and report rendering."""

    def test_clean_run_success(self) -> None:
        """Clean run with single success summary row produces zero drag and equal EVs."""
        records = [
            {
                "entry_type": "summary",
                "execution_id": "E-clean-1",
                "plan_branch": "I-test/P-test/_",
                "status": "success",
                "exit_code": 0,
                "ledger_revision": 1,
            }
        ]
        metrics = evaluate_dual_ev_outcomes(
            records=records,
            execution_id="E-clean-1",
            plan_branch="I-test/P-test/_",
            predicted_impact=50.0,
            cost_effort=4.0,
        )
        self.assertEqual(metrics.p_success_clean, 1.0)
        self.assertEqual(metrics.p_success_with_bugs, 1.0)
        self.assertEqual(metrics.impact_clean, 50.0)
        self.assertEqual(metrics.impact_with_bugs, 50.0)
        self.assertEqual(metrics.ev_clean, 46.0)
        self.assertEqual(metrics.ev_with_bugs, 46.0)
        self.assertEqual(metrics.bug_ev_drag, 0.0)

    def test_clean_run_halted_for_human(self) -> None:
        """Clean run ending in human-merge halt with exit code 0 is treated as converged success."""
        records = [
            {
                "entry_type": "summary",
                "execution_id": "REV-halt-1",
                "plan_branch": "I-test/P-test/_",
                "status": "halted_for_human",
                "exit_code": 0,
                "ledger_revision": 1,
            }
        ]
        metrics = evaluate_dual_ev_outcomes(
            records=records,
            execution_id="E-halt-1",
            plan_branch="I-test/P-test/_",
            predicted_impact=35.0,
            cost_effort=3.5,
        )
        self.assertEqual(metrics.p_success_clean, 1.0)
        self.assertEqual(metrics.p_success_with_bugs, 1.0)
        self.assertEqual(metrics.ev_clean, 31.5)
        self.assertEqual(metrics.ev_with_bugs, 31.5)
        self.assertEqual(metrics.bug_ev_drag, 0.0)

    def test_clean_run_approved(self) -> None:
        """Clean run ending with Stage 4 ``approved`` status (no explicit exit_code) converges with zero drag."""
        records = [
            {
                "entry_type": "summary",
                "execution_id": "REV-approved-1",
                "plan_branch": "I-test/P-test/_",
                "status": "approved",
                "ledger_revision": 1,
            }
        ]
        metrics = evaluate_dual_ev_outcomes(
            records=records,
            execution_id="E-approved-1",
            plan_branch="I-test/P-test/_",
            predicted_impact=35.0,
            cost_effort=3.5,
        )
        self.assertEqual(metrics.p_success_clean, 1.0)
        self.assertEqual(metrics.p_success_with_bugs, 1.0)
        self.assertEqual(metrics.ev_clean, 31.5)
        self.assertEqual(metrics.ev_with_bugs, 31.5)
        self.assertEqual(metrics.bug_ev_drag, 0.0)

    def test_integrated_decomposed_parent_is_scored_as_success(self) -> None:
        """The superseding integration row (status decomposed, integrated, no exit_code) is a converged success."""
        predicted = PredictedMetrics(p_success=0.95, entropy=1.0, impact=50.0, cost=4.0, learning_value=2.0, ev=46.0)
        jsonl = (
            '{"entry_type": "summary", "execution_id": "E-dec-1", "plan_branch": "I-t/P-t/_", '
            '"status": "decomposed", "ledger_revision": 1, "created_at": "2026-11-09T01:00:00Z"}\n'
            '{"entry_type": "summary", "execution_id": "E-dec-1", "plan_branch": "I-t/P-t/_", '
            '"status": "decomposed", "integrated": true, "ledger_revision": 2, "created_at": "2026-11-09T02:00:00Z"}\n'
        )

        def mock_subp(cmd: list[str], **kwargs: object) -> MagicMock:
            if "diff" in cmd:
                return MagicMock(returncode=0, stdout="1 file changed, 10 insertions(+), 2 deletions(-)\n")
            return MagicMock(returncode=0, stdout="")

        with (
            patch("sandbox_executor.calibration.actual.read_git_file", return_value=jsonl),
            patch("subprocess.run", side_effect=mock_subp),
        ):
            actual, _meta = parse_actual_metrics("E-dec-1", "I-t/P-t/_", predicted)
        self.assertEqual(actual.exit_code, 0)
        self.assertEqual(actual.p_success, 1.0)
        self.assertEqual(actual.p_success_clean, 1.0)
        self.assertEqual(actual.p_success_with_bugs, 1.0)
        self.assertEqual(actual.bug_ev_drag, 0.0)

    def test_non_integrated_final_decomposed_row_is_not_a_converged_success(self) -> None:
        """A final decomposed row without ``integrated: true`` (absent or false) is not scored as success."""
        predicted = PredictedMetrics(p_success=0.95, entropy=1.0, impact=50.0, cost=4.0, learning_value=2.0, ev=46.0)
        for marker in ("", '"integrated": false, '):
            jsonl = (
                '{"entry_type": "summary", "execution_id": "E-dec-2", "plan_branch": "I-t/P-t/_", '
                f'"status": "decomposed", {marker}"ledger_revision": 1, "created_at": "2026-11-09T01:00:00Z"}}\n'
            )

            def mock_subp(cmd: list[str], **kwargs: object) -> MagicMock:
                if "diff" in cmd:
                    return MagicMock(returncode=0, stdout="1 file changed, 10 insertions(+), 2 deletions(-)\n")
                return MagicMock(returncode=0, stdout="")

            with (
                patch("sandbox_executor.calibration.actual.read_git_file", return_value=jsonl),
                patch("subprocess.run", side_effect=mock_subp),
            ):
                actual, _meta = parse_actual_metrics("E-dec-2", "I-t/P-t/_", predicted)
            self.assertEqual(actual.p_success, 0.0, marker)
            self.assertEqual(actual.p_success_clean, 0.0, marker)

    def test_recovered_review_failure_adds_no_drag(self) -> None:
        """A failed Stage 4 (``REV-``) row between Stage 3 success and a later success is not a delivery failure."""
        records = [
            {
                "entry_type": "summary",
                "execution_id": "E-test-1",
                "plan_branch": "I-test/P-test/_",
                "status": "success",
                "exit_code": 0,
                "ledger_revision": 1,
                "created_at": "2026-11-09T01:00:00Z",
            },
            {
                "entry_type": "summary",
                "execution_id": "REV-test-1",
                "plan_branch": "I-test/P-test/_",
                "status": "failed",
                "exit_code": 1,
                "ledger_revision": 2,
                "created_at": "2026-11-09T02:00:00Z",
            },
            {
                "entry_type": "summary",
                "execution_id": "REV-test-2",
                "plan_branch": "I-test/P-test/_",
                "status": "success",
                "exit_code": 0,
                "ledger_revision": 3,
                "created_at": "2026-11-09T03:00:00Z",
            },
        ]
        metrics = evaluate_dual_ev_outcomes(
            records=records,
            execution_id="E-test-1",
            plan_branch="I-test/P-test/_",
            predicted_impact=50.0,
            cost_effort=5.0,
        )
        self.assertEqual(metrics.p_success_clean, 1.0)
        self.assertEqual(metrics.impact_clean, 50.0)
        self.assertEqual(metrics.ev_clean, 45.0)
        self.assertGreater(metrics.ev_clean, 0.0)

        self.assertEqual(metrics.p_success_with_bugs, 1.0)
        self.assertEqual(metrics.impact_with_bugs, 50.0)
        self.assertEqual(metrics.ev_with_bugs, metrics.ev_clean)
        self.assertEqual(metrics.bug_ev_drag, 0.0)

    def test_converged_failure_run(self) -> None:
        """Run that fails without subsequent recovery sets clean and observed EV to drag penalty."""
        records = [
            {
                "entry_type": "summary",
                "execution_id": "E-fail-1",
                "plan_branch": "I-test/P-test/_",
                "status": "failed",
                "exit_code": 1,
                "ledger_revision": 1,
            }
        ]
        metrics = evaluate_dual_ev_outcomes(
            records=records,
            execution_id="E-fail-1",
            plan_branch="I-test/P-test/_",
            predicted_impact=50.0,
            cost_effort=4.5,
        )
        self.assertEqual(metrics.p_success_clean, 0.0)
        self.assertEqual(metrics.p_success_with_bugs, 0.0)
        self.assertEqual(metrics.ev_clean, -4.5)
        self.assertEqual(metrics.ev_with_bugs, -4.5)
        self.assertEqual(metrics.bug_ev_drag, 0.0)

    def test_format_markdown_report_renders_dual_ev_and_bug_drag(self) -> None:
        """Markdown report must render both Clean EV, Observed EV, and Bug EV Drag."""
        pred = PredictedMetrics(p_success=0.95, entropy=1.0, impact=40.0, cost=4.0, learning_value=3.0, ev=37.2)
        act = ActualMetrics(
            p_success=1.0,
            entropy=0.5,
            impact=40.0,
            cost=3.5,
            learning_value=3.0,
            ev=37.85,
            ev_clean=37.85,
            ev_with_bugs=-3.5,
            p_success_clean=1.0,
            p_success_with_bugs=0.0,
            impact_clean=40.0,
            impact_with_bugs=0.0,
            bug_ev_drag=41.35,
        )
        deltas, ratings, biases = compute_calibration_deltas(pred, act)
        report_data = {
            "plan_id": "P-dual-ev-test",
            "execution_id": "E-dual-ev-test",
            "intent_branch": "I-dual-ev-test/_",
            "agent_id": "antigravity-agent",
            "model_name": "gemini-3.8-flash-medium",
            "timestamp": "2026-11-09T04:00:00.000Z",
            "evaluated_commit_sha": "abc1234567890abcdef1234567890abcdef1234",
            "predicted": pred.to_dict(),
            "actual": act.to_dict(),
            "deltas": deltas.to_dict(),
            "entropy_factors": EntropyFactors().to_dict(),
            "accuracy_ratings": ratings,
            "bias_directions": biases,
        }
        report = format_markdown_report(report_data)

        self.assertIn("Expected Value — Clean (without bugs)", report)
        self.assertIn("Expected Value — Observed (with bugs included)", report)
        self.assertIn("Bug EV Drag", report)
        self.assertIn("EV_{\\text{actual}}", report)
        self.assertIn("EV_{\\text{clean}}", report)
        self.assertIn("EV_{\\text{with\\_bugs}}", report)
        self.assertIn("\\text{Bug EV Drag}", report)

    def test_parse_actual_metrics_dual_ev_integration(self) -> None:
        """parse_actual_metrics ignores an intermediate Stage 4 failure: no drag, Stage 3 row sets the outcome."""
        predicted = PredictedMetrics(p_success=0.95, entropy=1.0, impact=50.0, cost=4.0, learning_value=2.0, ev=46.0)
        jsonl = (
            '{"entry_type": "summary", "execution_id": "E-int-1", "plan_branch": "I-t/P-t/_", '
            '"status": "success", "exit_code": 0, "ledger_revision": 1, "created_at": "2026-11-09T01:00:00Z"}\n'
            '{"entry_type": "summary", "execution_id": "REV-int-1", "plan_branch": "I-t/P-t/_", '
            '"status": "failed", "exit_code": 1, "ledger_revision": 2, "created_at": "2026-11-09T02:00:00Z"}\n'
            '{"entry_type": "summary", "execution_id": "REV-int-2", "plan_branch": "I-t/P-t/_", '
            '"status": "halted_for_human", "exit_code": 0, "ledger_revision": 3, '
            '"created_at": "2026-11-09T03:00:00Z"}\n'
        )

        def mock_subp(cmd: list[str], **kwargs: object) -> MagicMock:
            if "diff" in cmd:
                return MagicMock(returncode=0, stdout="1 file changed, 10 insertions(+), 2 deletions(-)\n")
            return MagicMock(returncode=0, stdout="")

        with (
            patch("sandbox_executor.calibration.actual.read_git_file", return_value=jsonl),
            patch("subprocess.run", side_effect=mock_subp),
        ):
            actual, _meta = parse_actual_metrics("E-int-1", "I-t/P-t/_", predicted)
            self.assertEqual(actual.exit_code, 0)
            self.assertEqual(actual.p_success_clean, 1.0)
            self.assertEqual(actual.p_success_with_bugs, 1.0)
            self.assertEqual(actual.impact_clean, 50.0)
            self.assertEqual(actual.impact_with_bugs, 50.0)
            self.assertGreater(actual.ev_clean, 0.0)
            self.assertEqual(actual.ev_with_bugs, actual.ev_clean)
            self.assertEqual(actual.bug_ev_drag, 0.0)
            # Backward compatibility invariants
            self.assertEqual(actual.p_success, actual.p_success_clean)
            self.assertEqual(actual.ev, actual.ev_clean)
            self.assertEqual(actual.impact, actual.impact_clean)

    def test_parse_actual_metrics_dual_ev_with_approved_recovery(self) -> None:
        """A Stage 4 ``failed`` then ``approved`` pair after a Stage 3 success adds no drag."""
        predicted = PredictedMetrics(p_success=0.95, entropy=1.0, impact=50.0, cost=4.0, learning_value=2.0, ev=46.0)
        jsonl = (
            '{"entry_type": "summary", "execution_id": "E-int-2", "plan_branch": "I-t/P-t/_", '
            '"status": "success", "exit_code": 0, "ledger_revision": 1, "created_at": "2026-11-09T01:00:00Z"}\n'
            '{"entry_type": "summary", "execution_id": "REV-int-3", "plan_branch": "I-t/P-t/_", '
            '"status": "failed", "exit_code": 1, "ledger_revision": 2, "created_at": "2026-11-09T02:00:00Z"}\n'
            '{"entry_type": "summary", "execution_id": "REV-int-4", "plan_branch": "I-t/P-t/_", '
            '"status": "approved", "ledger_revision": 3, "created_at": "2026-11-09T03:00:00Z"}\n'
        )

        def mock_subp(cmd: list[str], **kwargs: object) -> MagicMock:
            if "diff" in cmd:
                return MagicMock(returncode=0, stdout="1 file changed, 10 insertions(+), 2 deletions(-)\n")
            return MagicMock(returncode=0, stdout="")

        with (
            patch("sandbox_executor.calibration.actual.read_git_file", return_value=jsonl),
            patch("subprocess.run", side_effect=mock_subp),
        ):
            actual, _meta = parse_actual_metrics("E-int-2", "I-t/P-t/_", predicted)
        self.assertEqual(actual.exit_code, 0)
        self.assertEqual(actual.p_success_clean, 1.0)
        self.assertEqual(actual.p_success_with_bugs, 1.0)
        self.assertEqual(actual.bug_ev_drag, 0.0)

    def test_code_gate_compliance(self) -> None:
        """Assert module size budgets and complexity limits are strictly respected."""
        repo_root = Path(__file__).resolve().parents[3]
        helper_file = repo_root / "apps/sandbox-executor/src/sandbox_executor/calibration_ev.py"
        calibration_package = repo_root / "apps/sandbox-executor/src/sandbox_executor/calibration"
        calibration_modules = sorted(calibration_package.glob("*.py"))

        helper_lines = len(helper_file.read_text(encoding="utf-8").splitlines())
        calibration_lines = sum(len(path.read_text(encoding="utf-8").splitlines()) for path in calibration_modules)

        self.assertLess(helper_lines, 200, f"calibration_ev.py has {helper_lines} lines (budget < 200)")
        self.assertLessEqual(
            calibration_lines,
            1684,
            f"the calibration package has {calibration_lines} lines (baseline was 1684; net delta must be <= 0)",
        )

        # Assert no new noqa annotations added in calibration_ev.py
        helper_content = helper_file.read_text(encoding="utf-8")
        self.assertNotIn("# no" + "qa", helper_content, "calibration_ev.py must not contain any '# noqa' annotations")


MATRIX_PLAN = "I-m/P-m/_"
ID_PREFIX = {"execution": "E-", "review": "REV-", "calibrate": "CAL-"}
# The slots of each order: "x" is a row with the case's status, "s" a success row.
ORDERS = {"failure_then_success": "xs", "success_then_failure": "sx", "failure_last": "xsx"}


def _matrix_rows(kind: str, status: str, order: str) -> list[dict[str, Any]]:
    """A Stage 3 success row first (review/calibrate kinds only), then one row per slot with its own id and instant."""
    rows: list[dict[str, Any]] = []
    if kind != "execution":
        rows.append(_ledger_row("E-0", "success", 0))
    for index, slot in enumerate(ORDERS[order], start=1):
        slot_status = status if slot == "x" else "success"
        rows.append(_ledger_row(f"{ID_PREFIX[kind]}{index}", slot_status, index))
    return rows


def _ledger_row(execution_id: str, status: str, minute: int, **extra: Any) -> dict[str, Any]:
    return {
        "entry_type": "summary",
        "execution_id": execution_id,
        "plan_branch": MATRIX_PLAN,
        "status": status,
        "exit_code": 0 if status == "success" else 1,
        "created_at": f"2026-11-20T01:{minute:02d}:00Z",
        **extra,
    }


def _matrix_drag(rows: list[dict[str, Any]]) -> float:
    return evaluate_dual_ev_outcomes(rows, "E-0", MATRIX_PLAN, predicted_impact=50.0, cost_effort=5.0).bug_ev_drag


@pytest.mark.parametrize("order", sorted(ORDERS))
@pytest.mark.parametrize("status", ["failed", "sync_conflict", "success"])
@pytest.mark.parametrize("kind", ["execution", "review", "calibrate"])
def test_dual_ev_matrix(kind: str, status: str, order: str) -> None:
    """Only an unresolved Stage 3 failure adds drag; review and calibrate rows never do, whatever their order."""
    drag = _matrix_drag(_matrix_rows(kind, status, order))
    if kind == "execution" and status != "success" and order != "failure_then_success":
        assert drag == 50.0
    else:
        assert drag == 0.0


@pytest.mark.parametrize(
    "summary",
    [
        "Reviewer ensemble container dispatch exited with code 1 without writing results",
        "Executor stopped: usage limit reached",
        "execute turn budget of 900 s exceeded after 901.2 s",
        "Harness timeout while waiting for the agent",
    ],
)
def test_infrastructure_failure_after_delivery_adds_no_drag(summary: str) -> None:
    rows = [_ledger_row("E-1", "success", 1), _ledger_row("E-2", "failed", 2, summary=summary)]
    assert is_infrastructure_failure(rows[1])
    assert _matrix_drag(rows) == 0.0


@pytest.mark.parametrize(
    "summary", ["Plan execution failed with exit code 1: 3 tests failed", "pytest timed out in test_slow"]
)
def test_real_failure_after_delivery_adds_drag(summary: str) -> None:
    rows = [_ledger_row("E-1", "success", 1), _ledger_row("E-2", "failed", 2, summary=summary)]
    assert not is_infrastructure_failure(rows[1])
    assert _matrix_drag(rows) == 50.0


def test_failure_reason_and_error_fields_are_read() -> None:
    assert is_infrastructure_failure({"error": "Resolver container dispatch failed: boom"})
    assert is_infrastructure_failure({"failure_reason": "session limit"})
    assert not is_infrastructure_failure({"summary": "failed", "error": "error"})


def test_pr_249_rows_add_no_drag() -> None:
    """The PR #249 summary rows verbatim: Stage 3 success, a failed reviewer dispatch, then the approval."""
    plan = "I-1795409225-remove-token-reduction-copy/P-1795409242-CLA-CO5.5/_"
    rows = [
        {
            "entry_type": "summary",
            "execution_id": "E-1795409573-CLA-CO5.5",
            "plan_branch": plan,
            "status": "success",
            "summary": "Plan executed successfully",
            "created_at": "2026-11-23T04:52:53.778310+00:00",
            "ledger_revision": 1,
        },
        {
            "execution_id": "REV-1795410989-b2a486df-claude-agent",
            "plan_branch": plan,
            "status": "failed",
            "entry_type": "summary",
            "summary": (
                "Review Loop halted without consensus after 2 iteration(s): Reviewer ensemble container "
                "dispatch exited with code 1 without writing results"
            ),
            "created_at": "2026-11-23T05:16:29.233Z",
        },
        {
            "execution_id": "REV-1795412634-3cf610db-claude-agent",
            "plan_branch": plan,
            "status": "approved",
            "entry_type": "summary",
            "summary": "Review Loop reached consensus approval after 5 iteration(s)",
            "created_at": "2026-11-23T05:43:54.358Z",
        },
    ]
    metrics = evaluate_dual_ev_outcomes(rows, "E-1795409573-CLA-CO5.5", plan, predicted_impact=82.0, cost_effort=4.0)
    assert metrics.bug_ev_drag == 0.0
    assert metrics.ev_with_bugs == metrics.ev_clean == 78.0


@pytest.mark.parametrize(
    ("record", "stage"),
    [
        ({"execution_id": "REV-1"}, "review"),
        ({"execution_id": "CAL-P-1-2"}, "calibrate"),
        ({"execution_id": "E-1"}, "execution"),
        ({}, "execution"),
        ({"execution_id": "X-1"}, "other"),
        ({"execution_id": "REV-1", "stage": "execution"}, "execution"),
        ({"execution_id": "E-1", "stage": "Review"}, "review"),
        ({"execution_id": "E-1", "stage": "bogus"}, "other"),
    ],
)
def test_row_stage(record: dict[str, Any], stage: str) -> None:
    assert row_stage(record) == stage


def test_row_stage_accepts_the_plan_execution_id_as_execution() -> None:
    assert row_stage({"execution_id": "legacy-1"}, "legacy-1") == "execution"


# Targeted cases for mutants that survived the scoped mutation pass on changed lines.


def test_failed_status_with_a_zero_exit_code_is_a_failure() -> None:
    rows = [_ledger_row("E-1", "success", 1), {**_ledger_row("E-2", "failed", 2), "exit_code": 0}]
    assert _matrix_drag(rows) == 50.0


def test_final_sync_conflict_with_a_zero_exit_code_is_unresolved() -> None:
    rows = [_ledger_row("E-1", "success", 1), {**_ledger_row("E-2", "sync_conflict", 2), "exit_code": 0}]
    assert _matrix_drag(rows) == 50.0


def test_a_later_row_of_the_same_execution_moves_to_its_own_place_in_flow_order() -> None:
    """E-1 fails, E-2 fails, then E-1's superseding success: the success comes last and resolves both."""
    rows = [_ledger_row("E-1", "failed", 1), _ledger_row("E-2", "failed", 2), _ledger_row("E-1", "success", 3)]
    assert _matrix_drag(rows) == 0.0


def test_rows_without_an_id_supersede_each_other_in_flow_order() -> None:
    no_id = {key: value for key, value in _ledger_row("", "success", 1).items() if key != "execution_id"}
    rows = [no_id, _ledger_row("E-2", "failed", 2), {**no_id, "created_at": "2026-11-20T01:03:00Z"}]
    assert _matrix_drag(rows) == 0.0


@pytest.mark.parametrize(("approval_first", "drag"), [(False, 0.0), (True, 50.0)])
def test_sync_conflict_and_approval_at_the_same_instant_follow_ledger_order(approval_first: bool, drag: float) -> None:
    conflict = {**_ledger_row("E-1", "sync_conflict", 2), "exit_code": 0}
    approval = {**_ledger_row("REV-1", "approved", 2), "exit_code": 0}
    tail = [approval, conflict] if approval_first else [conflict, approval]
    assert _matrix_drag([_ledger_row("E-0", "success", 1), *tail]) == drag


def test_another_plans_rows_are_never_used_when_the_plan_has_rows() -> None:
    other = {**_ledger_row("E-9", "failed", 5), "plan_branch": "I-other/P-other/_"}
    assert _matrix_drag([_ledger_row("E-1", "success", 1), other]) == 0.0


def test_legacy_fallback_uses_every_execution_summary_row() -> None:
    """No row of the plan: every execution summary row (``entry_type`` defaulting to summary) is evaluated."""
    rows = [
        {"execution_id": "E-x", "status": "success", "exit_code": 0, "created_at": "2026-11-20T01:01:00Z"},
        {"execution_id": "E-y", "status": "failed", "exit_code": 1, "created_at": "2026-11-20T01:02:00Z"},
        {"execution_id": "REV-z", "status": "failed", "exit_code": 1, "created_at": "2026-11-20T01:03:00Z"},
    ]
    metrics = evaluate_dual_ev_outcomes(rows, "E-unknown", "", predicted_impact=50.0, cost_effort=5.0)
    assert metrics.bug_ev_drag == 50.0


def test_execution_rows_match_by_id_without_a_plan_branch() -> None:
    from sandbox_executor.calibration_ev import is_matching_execution_record
    from sandbox_executor.calibration_rows import is_execution_row

    assert is_matching_execution_record({"execution_id": "E-9"}, "E-9", MATRIX_PLAN)
    assert is_matching_execution_record({"execution_id": "legacy-9"}, "legacy-9", "")
    assert is_execution_row({"execution_id": "legacy-9"}, "legacy-9")
    assert not is_execution_row({"execution_id": "XXXX"})
    assert row_stage({"execution_id": "XXXX"}) == "other"


def test_execution_rows_without_a_plan_branch_match_by_id() -> None:
    own = {key: value for key, value in _ledger_row("E-0", "success", 1).items() if key != "plan_branch"}
    other = {**_ledger_row("E-9", "failed", 2), "plan_branch": "I-other/P-other/_"}
    assert _matrix_drag([own, other]) == 0.0


def test_legacy_fallback_ignores_action_rows() -> None:
    rows = [
        {"execution_id": "E-x", "status": "success", "exit_code": 0, "created_at": "2026-11-20T01:01:00Z"},
        {"entry_type": "action", "execution_id": "E-y", "status": "failed", "created_at": "2026-11-20T01:02:00Z"},
    ]
    metrics = evaluate_dual_ev_outcomes(rows, "E-unknown", "", predicted_impact=50.0, cost_effort=5.0)
    assert metrics.bug_ev_drag == 0.0


def test_outcome_helpers_without_plan_rows() -> None:
    from sandbox_executor.calibration_outcome import has_delivered, has_unresolved_failure

    failed = _ledger_row("E-1", "failed", 1)
    conflict = {**_ledger_row("E-1", "sync_conflict", 1), "exit_code": 0}
    assert has_unresolved_failure([failed]) is True
    assert has_unresolved_failure([conflict]) is True
    assert has_delivered([conflict]) is False


@pytest.mark.parametrize(("approval_minute", "unresolved"), [(3, False), (2, True)])
def test_plan_rows_that_are_copies_resolve_by_a_strictly_later_instant(approval_minute: int, unresolved: bool) -> None:
    """A conflict row that is not itself among the plan rows is resolved only by a strictly later delivered row."""
    from sandbox_executor.calibration_outcome import has_unresolved_failure

    conflict = {**_ledger_row("E-1", "sync_conflict", 2), "exit_code": 0}
    approval = {**_ledger_row("REV-1", "approved", approval_minute), "exit_code": 0}
    assert has_unresolved_failure([conflict], [dict(conflict), approval]) is unresolved
