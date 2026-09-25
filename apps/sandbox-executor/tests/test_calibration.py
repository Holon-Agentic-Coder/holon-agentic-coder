"""Unit test suite for sandbox_executor.calibration and holon calibrate CLI command."""

from __future__ import annotations

import json
import sys
import unittest
from unittest.mock import MagicMock, patch

from sandbox_executor.calibration import (
    ActualMetrics,
    EntropyFactors,
    PredictedMetrics,
    compute_calibration_deltas,
    extract_branch_components,
    format_markdown_report,
    load_metrics_physics,
    parse_actual_metrics,
    parse_predicted_metrics,
    run_calibrate,
)
from sandbox_executor.cli import main as cli_main


class TestCalibrationDataModels(unittest.TestCase):
    """Test calibration dataclass models and serialization."""

    def test_predicted_metrics_defaults_and_dict(self):
        pm = PredictedMetrics(p_success=0.95, entropy=1.5, impact=70.0, cost=3.0, learning_value=2.0, ev=60.0)
        d = pm.to_dict()
        self.assertEqual(d["p_success"], 0.95)
        self.assertEqual(d["entropy"], 1.5)
        self.assertEqual(d["impact"], 70.0)
        self.assertEqual(d["cost"], 3.0)
        self.assertEqual(d["learning_value"], 2.0)
        self.assertEqual(d["ev"], 60.0)

    def test_actual_metrics_defaults_and_dict(self):
        am = ActualMetrics(
            p_success=1.0,
            entropy=0.4,
            impact=70.0,
            cost=2.5,
            learning_value=2.0,
            ev=67.38,
            duration_seconds=45.2,
            tokens=1250,
            exit_code=0,
            test_pass_rate=1.0,
            files_changed=2,
            insertions=50,
            deletions=5,
        )
        d = am.to_dict()
        self.assertEqual(d["p_success"], 1.0)
        self.assertEqual(d["exit_code"], 0)
        self.assertEqual(d["duration_seconds"], 45.2)
        self.assertEqual(d["tokens"], 1250)
        self.assertEqual(d["files_changed"], 2)

    def test_entropy_factors_dict(self):
        ef = EntropyFactors(ssa=(0.6, 0.4), irr=(0.0, 0.0), cl=(0.1, 0.0), ser=(0.0, 0.0), nov=(0.4, 0.4))
        d = ef.to_dict()
        self.assertEqual(d["ssa"]["predicted"], 0.6)
        self.assertEqual(d["ssa"]["observed"], 0.4)
        self.assertEqual(d["cl"]["predicted"], 0.1)


class TestBranchComponentExtraction(unittest.TestCase):
    """Test branch string parsing logic."""

    def test_extract_full_branch(self):
        branch = (
            "I-1790379374-add-holon-calibrate-command/"
            "P-1790379384-antigravity-agent-gemini-3.8-flash-medium/"
            "E-1790379620-antigravity-agent-gemini-3.8-flash-medium/_"
        )
        comp = extract_branch_components(branch)
        self.assertEqual(comp["intent_id"], "I-1790379374-add-holon-calibrate-command")
        self.assertEqual(comp["plan_id"], "P-1790379384-antigravity-agent-gemini-3.8-flash-medium")
        self.assertEqual(comp["execution_id"], "E-1790379620-antigravity-agent-gemini-3.8-flash-medium")
        self.assertEqual(comp["intent_branch"], "I-1790379374-add-holon-calibrate-command/_")
        self.assertEqual(
            comp["plan_branch"],
            "I-1790379374-add-holon-calibrate-command/P-1790379384-antigravity-agent-gemini-3.8-flash-medium/_",
        )

    def test_extract_short_execution_id(self):
        comp = extract_branch_components("E-1787051559-antigravity-agent-gemini-3.5-flash")
        self.assertEqual(comp["execution_id"], "E-1787051559-antigravity-agent-gemini-3.5-flash")
        self.assertEqual(comp["plan_id"], "")


class TestMetricsPhysicsLoader(unittest.TestCase):
    """Test loading lambda and mu constants from config."""

    def test_load_metrics_physics_default(self):
        with patch("os.path.exists", return_value=False):
            l_val, m_val = load_metrics_physics()
            self.assertEqual(l_val, 0.3)
            self.assertEqual(m_val, 0.5)

    def test_load_metrics_physics_custom(self):
        custom_json = json.dumps({"lambda": 0.25, "mu": 0.6})
        with (
            patch("os.path.exists", return_value=True),
            patch("builtins.open", unittest.mock.mock_open(read_data=custom_json)),
        ):
            l_val, m_val = load_metrics_physics()
            self.assertEqual(l_val, 0.25)
            self.assertEqual(m_val, 0.6)


class TestMetricsParsing(unittest.TestCase):
    """Test parsing predicted and actual metrics."""

    def test_parse_predicted_metrics_from_ledger(self):
        record = {
            "plan_id": "P-test-123",
            "p_success": 0.92,
            "entropy": 1.8,
            "impact": 65.0,
            "cost": 4.0,
            "learning_value": 3.0,
            "ev": 58.26,
        }
        jsonl_content = json.dumps(record) + "\n"

        with (
            patch("os.path.exists", side_effect=lambda p: "plans.jsonl" in str(p)),
            patch("builtins.open", unittest.mock.mock_open(read_data=jsonl_content)),
        ):
            pred, meta = parse_predicted_metrics("P-test-123")
            self.assertEqual(pred.p_success, 0.92)
            self.assertEqual(pred.entropy, 1.8)
            self.assertEqual(pred.impact, 65.0)
            self.assertEqual(pred.cost, 4.0)
            self.assertEqual(pred.learning_value, 3.0)
            self.assertEqual(pred.ev, 58.26)
            self.assertEqual(meta["plan_id"], "P-test-123")

    def test_parse_predicted_metrics_from_markdown(self):
        md_content = """# Plan

## Overall Plan Metrics

| metric | value |
| p_success_pred | 0.85 |
| entropy_pred | 2.1 |
| impact_pred | 45.0 |
| cost_pred | 3.5 |
| learning_value_pred | 2.0 |
| ev_pred | 34.12 |
"""

        def exists_side_effect(p):
            return "P-test-456.md" in str(p)

        with (
            patch("os.path.exists", side_effect=exists_side_effect),
            patch("builtins.open", unittest.mock.mock_open(read_data=md_content)),
        ):
            pred, _meta = parse_predicted_metrics("P-test-456")
            self.assertEqual(pred.p_success, 0.85)
            self.assertEqual(pred.entropy, 2.1)
            self.assertEqual(pred.impact, 45.0)

    def test_parse_actual_metrics_from_ledger_and_git(self):
        predicted = PredictedMetrics(p_success=0.98, entropy=1.2, impact=35.0, cost=4.0, learning_value=2.5, ev=31.19)
        exec_record = {
            "execution_id": "E-test-123",
            "plan_branch": "I-test/P-test/_",
            "status": "success",
            "duration": 30.5,
            "tokens": 800,
        }
        jsonl_content = json.dumps(exec_record) + "\n"

        mock_diff = MagicMock()
        mock_diff.returncode = 0
        mock_diff.stdout = "2 files changed, 40 insertions(+), 5 deletions(-)\n"

        with (
            patch("os.path.exists", side_effect=lambda p: "executions.jsonl" in str(p)),
            patch("builtins.open", unittest.mock.mock_open(read_data=jsonl_content)),
            patch("subprocess.run", return_value=mock_diff),
        ):
            actual, _meta = parse_actual_metrics("E-test-123", "I-test/P-test/_", predicted)
            self.assertEqual(actual.p_success, 1.0)
            self.assertEqual(actual.exit_code, 0)
            self.assertEqual(actual.files_changed, 2)
            self.assertEqual(actual.insertions, 40)
            self.assertEqual(actual.deletions, 5)
            self.assertEqual(actual.impact, 35.0)


class TestCalibrationCalculations(unittest.TestCase):
    """Test error calculations, accuracy classifications, and bias ratings."""

    def test_compute_calibration_deltas_exact_match(self):
        pred = PredictedMetrics(p_success=0.98, entropy=1.20, impact=35.0, cost=4.0, learning_value=2.5, ev=31.19)
        act = ActualMetrics(p_success=1.00, entropy=0.40, impact=35.0, cost=3.5, learning_value=2.5, ev=32.63)

        deltas, ratings, biases = compute_calibration_deltas(pred, act)
        self.assertAlmostEqual(deltas.p_success_error, 0.02, places=2)
        self.assertAlmostEqual(deltas.entropy_error, 0.80, places=2)
        self.assertAlmostEqual(deltas.impact_error, 0.0, places=2)
        self.assertAlmostEqual(deltas.cost_error, 0.50, places=2)
        self.assertAlmostEqual(deltas.delta_ev, 1.44, places=2)

        self.assertEqual(ratings["impact"], "Exact")
        self.assertEqual(ratings["learning_value"], "Exact")
        self.assertIn("High", ratings["p_success"])
        self.assertEqual(biases["p_success"], "Slight Underconfidence")
        self.assertEqual(biases["entropy"], "Overestimated Risk")
        self.assertEqual(biases["ev"], "Conservative Underestimate")


class TestReportFormatting(unittest.TestCase):
    """Test markdown generation conforming to calibration schema."""

    def test_format_markdown_report_structure(self):
        pred = PredictedMetrics(p_success=0.98, entropy=1.20, impact=35.0, cost=4.0, learning_value=2.5, ev=31.19)
        act = ActualMetrics(p_success=1.00, entropy=0.40, impact=35.0, cost=3.5, learning_value=2.5, ev=32.63)
        deltas, ratings, biases = compute_calibration_deltas(pred, act)
        ef = EntropyFactors()

        report_dict = {
            "plan_id": "P-1787051525",
            "execution_id": "E-1787051559",
            "intent_branch": "I-1787051498-executor-plan-calibration/_",
            "agent_id": "antigravity-agent",
            "model_name": "gemini-3.5-flash",
            "timestamp": "2026-08-18T11:13:50.000Z",
            "predicted": pred.to_dict(),
            "actual": act.to_dict(),
            "deltas": deltas.to_dict(),
            "entropy_factors": ef.to_dict(),
            "accuracy_ratings": ratings,
            "bias_directions": biases,
        }

        md = format_markdown_report(report_dict)
        self.assertIn("# Plan Calibration Report: P-1787051525", md)
        self.assertIn("## 1. Executive Calibration Summary", md)
        self.assertIn("## 2. Mathematical Derivations & Calibration Errors", md)
        self.assertIn("## 3. Entropy Factor Breakdown", md)
        self.assertIn("## 4. Calibration Assessment", md)
        self.assertIn("| **$P(\\text{success})$**", md)
        self.assertIn("EV_{\\text{pred}}", md)
        self.assertIn("EV_{\\text{actual}}", md)


class TestRunCalibrateWorkflow(unittest.TestCase):
    """Test run_calibrate workflow and git interactions."""

    @patch("subprocess.run")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    @patch("os.makedirs")
    def test_run_calibrate_checkout_and_commit(self, mock_makedirs, mock_open, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")

        branch = "I-123-intent/P-456-plan/E-789-exec/_"
        report = run_calibrate(branch, repo_dir="/tmp/test_repo", json_output=False, skip_commit=False)

        self.assertEqual(report.calibrated_branch, "I-123-intent/P-456-plan/E-789-exec/calibrated")
        mock_open.assert_called()
        self.assertTrue(mock_run.call_count >= 2)
        called_cmds = [" ".join(c[0][0]) for c in mock_run.call_args_list if c[0] and isinstance(c[0][0], list)]
        self.assertTrue(any("git checkout -B" in cmd for cmd in called_cmds))
        self.assertTrue(any("git commit" in cmd for cmd in called_cmds))

    @patch("subprocess.run")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    @patch("os.makedirs")
    def test_run_calibrate_json_output(self, mock_makedirs, mock_open, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        branch = "I-123-intent/P-456-plan/E-789-exec/_"

        with patch("builtins.print") as mock_print:
            run_calibrate(branch, repo_dir="/tmp/test_repo", json_output=True, skip_commit=True)
            mock_print.assert_called()
            printed_arg = mock_print.call_args[0][0]
            parsed = json.loads(printed_arg)
            self.assertEqual(parsed["calibrated_branch"], "I-123-intent/P-456-plan/E-789-exec/calibrated")


class TestCLICalibrateCommand(unittest.TestCase):
    """Test CLI dispatch for `holon calibrate`."""

    @patch("sandbox_executor.cli.run_calibrate")
    def test_cli_calibrate_invocation(self, mock_run_calibrate):
        test_args = ["holon", "calibrate", "I-123/P-456/E-789/_"]
        with patch.object(sys, "argv", test_args):
            with self.assertRaises(SystemExit) as cm:
                cli_main()
            self.assertEqual(cm.exception.code, 0)
            mock_run_calibrate.assert_called_once_with(
                execution_branch="I-123/P-456/E-789/_",
                json_output=False,
            )

    @patch("sandbox_executor.cli.run_calibrate")
    def test_cli_calibrate_invocation_json(self, mock_run_calibrate):
        test_args = ["holon", "calibrate", "I-123/P-456/E-789/_", "--json"]
        with patch.object(sys, "argv", test_args):
            with self.assertRaises(SystemExit) as cm:
                cli_main()
            self.assertEqual(cm.exception.code, 0)
            mock_run_calibrate.assert_called_once_with(
                execution_branch="I-123/P-456/E-789/_",
                json_output=True,
            )


if __name__ == "__main__":
    unittest.main()
