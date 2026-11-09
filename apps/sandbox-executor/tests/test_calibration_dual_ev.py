"""Unit tests for dual Actual EV calibration with and without pipeline/engine bugs."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sandbox_executor.calibration import (
    ActualMetrics,
    EntropyFactors,
    PredictedMetrics,
    compute_calibration_deltas,
    format_markdown_report,
    parse_actual_metrics,
)
from sandbox_executor.calibration_ev import evaluate_dual_ev_outcomes


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
            patch("sandbox_executor.calibration.read_git_file", return_value=jsonl),
            patch("subprocess.run", side_effect=mock_subp),
        ):
            actual, _meta = parse_actual_metrics("E-dec-1", "I-t/P-t/_", predicted)
        self.assertEqual(actual.exit_code, 0)
        self.assertEqual(actual.p_success, 1.0)
        self.assertEqual(actual.p_success_clean, 1.0)
        self.assertEqual(actual.p_success_with_bugs, 1.0)
        self.assertEqual(actual.bug_ev_drag, 0.0)

    def test_intermediate_failure_with_converged_recovery(self) -> None:
        """Intermediate failed summary row followed by converged recovery produces positive bug drag."""
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

        self.assertEqual(metrics.p_success_with_bugs, 0.0)
        self.assertEqual(metrics.impact_with_bugs, 0.0)
        self.assertEqual(metrics.ev_with_bugs, -5.0)
        self.assertLess(metrics.ev_with_bugs, 0.0)

        self.assertEqual(metrics.bug_ev_drag, 50.0)
        self.assertEqual(metrics.bug_ev_drag, round(metrics.ev_clean - metrics.ev_with_bugs, 2))

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
        """parse_actual_metrics correctly ingests intermediate failure and computes dual EV."""
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
            patch("sandbox_executor.calibration.read_git_file", return_value=jsonl),
            patch("subprocess.run", side_effect=mock_subp),
        ):
            actual, _meta = parse_actual_metrics("E-int-1", "I-t/P-t/_", predicted)
            self.assertEqual(actual.p_success_clean, 1.0)
            self.assertEqual(actual.p_success_with_bugs, 0.0)
            self.assertEqual(actual.impact_clean, 50.0)
            self.assertEqual(actual.impact_with_bugs, 0.0)
            self.assertGreater(actual.ev_clean, 0.0)
            self.assertLess(actual.ev_with_bugs, 0.0)
            self.assertEqual(actual.bug_ev_drag, round(actual.ev_clean - actual.ev_with_bugs, 2))
            # Backward compatibility invariants
            self.assertEqual(actual.p_success, actual.p_success_clean)
            self.assertEqual(actual.ev, actual.ev_clean)
            self.assertEqual(actual.impact, actual.impact_clean)

    def test_code_gate_compliance(self) -> None:
        """Assert module size budgets and complexity limits are strictly respected."""
        repo_root = Path(__file__).resolve().parents[3]
        helper_file = repo_root / "apps/sandbox-executor/src/sandbox_executor/calibration_ev.py"
        calibration_file = repo_root / "apps/sandbox-executor/src/sandbox_executor/calibration.py"

        helper_lines = len(helper_file.read_text(encoding="utf-8").splitlines())
        calibration_lines = len(calibration_file.read_text(encoding="utf-8").splitlines())

        self.assertLess(helper_lines, 200, f"calibration_ev.py has {helper_lines} lines (budget < 200)")
        self.assertLessEqual(
            calibration_lines,
            1684,
            f"calibration.py has {calibration_lines} lines (baseline was 1684; net delta must be <= 0)",
        )

        # Assert no new noqa annotations added in calibration_ev.py
        helper_content = helper_file.read_text(encoding="utf-8")
        self.assertNotIn("# no" + "qa", helper_content, "calibration_ev.py must not contain any '# noqa' annotations")
