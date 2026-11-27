"""Predicted, actual and delta calibration metrics: plan and ledger readers, the extractor table, pure deltas."""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.calibration import (
    ActualMetrics,
    EntropyFactors,
    PredictedMetrics,
    compute_calibration_deltas,
    format_markdown_report,
    load_metrics_physics,
    parse_actual_metrics,
    parse_predicted_metrics,
    read_git_file,
)
from sandbox_executor.calibration.actual import ACTUAL_METRIC_EXTRACTORS
from sandbox_executor.calibration.actual_evidence import parse_numstat, parse_shortstat
from sandbox_executor.calibration.deltas import _absolute_errors, _accuracy_ratings, _rate


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

    def test_load_metrics_physics_typed_errors_return_defaults(self):
        payloads = [
            "{not valid json",
            json.dumps({"lambda": "not-a-float", "mu": 0.6}),
            json.dumps({"lambda": None, "mu": 0.6}),
            json.dumps([0.2, 0.4]),
        ]
        for payload in payloads:
            with (
                self.subTest(payload=payload),
                patch("os.path.exists", return_value=True),
                patch("builtins.open", unittest.mock.mock_open(read_data=payload)),
            ):
                self.assertEqual(load_metrics_physics(), (0.3, 0.5))

        with (
            patch("os.path.exists", return_value=True),
            patch("builtins.open", side_effect=OSError("disk error")),
        ):
            self.assertEqual(load_metrics_physics(), (0.3, 0.5))

    def test_load_metrics_physics_unexpected_error_propagates(self):
        with (
            patch("os.path.exists", return_value=True),
            patch("builtins.open", side_effect=RuntimeError("unexpected open bug")),
            self.assertRaises(RuntimeError),
        ):
            load_metrics_physics()


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
        jsonl_content = json.dumps({**record, "intent_branch": "I-1-x/_"}) + "\n"

        with (
            patch("os.path.exists", side_effect=lambda p: "plans.jsonl" in str(p)),
            patch("builtins.open", unittest.mock.mock_open(read_data=jsonl_content)),
        ):
            pred, meta = parse_predicted_metrics("I-1-x/P-test-123/_")
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
            pred, _meta = parse_predicted_metrics("I-1-x/P-test-456/_")
            self.assertEqual(pred.p_success, 0.85)
            self.assertEqual(pred.entropy, 2.1)
            self.assertEqual(pred.impact, 45.0)

    def test_parse_predicted_metrics_multistep(self):
        md_content = """# Plan

## Overall Plan Metrics

| metric | value |
| p_success_pred | 0.90 |
| entropy_pred | 1.5 |
| impact_pred | 50.0 |
| cost_pred | 2.0 |
| learning_value_pred | 2.0 |
| ev_pred | 43.55 |

## Step 1: Implementation

| metric | value |
| p_success_pred | 0.95 |
| entropy_pred | 0.8 |
| impact_pred | 20.0 |
| cost_pred | 1.0 |
| learning_value_pred | 1.0 |
| ev_pred | 18.26 |
"""
        with (
            patch("os.path.exists", return_value=True),
            patch("builtins.open", unittest.mock.mock_open(read_data=md_content)),
        ):
            pred, _meta = parse_predicted_metrics("I-1-x/P-test-multi/_")
            self.assertEqual(pred.p_success, 0.90)
            self.assertEqual(pred.entropy, 1.5)
            self.assertEqual(pred.impact, 50.0)

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

        def mock_subp(cmd, **kwargs):
            if isinstance(cmd, list) and "diff" in cmd:
                return MagicMock(returncode=0, stdout="2 files changed, 40 insertions(+), 5 deletions(-)\n")
            return MagicMock(returncode=1, stdout="", stderr="")

        with (
            patch("os.path.exists", side_effect=lambda p: "executions.jsonl" in str(p)),
            patch("builtins.open", unittest.mock.mock_open(read_data=jsonl_content)),
            patch("subprocess.run", side_effect=mock_subp),
        ):
            actual, _meta = parse_actual_metrics("E-test-123", "I-test/P-test/_", predicted)
            self.assertEqual(actual.p_success, 1.0)
            self.assertEqual(actual.exit_code, 0)
            self.assertEqual(actual.files_changed, 2)
            self.assertEqual(actual.insertions, 40)
            self.assertEqual(actual.deletions, 5)
            self.assertEqual(actual.impact, 35.0)
            # The row records no test result: reported as not measured, never as a pass.
            self.assertIsNone(actual.test_pass_rate)
            self.assertIsNone(actual.to_dict()["test_pass_rate"])

    def test_parse_actual_metrics_reports_only_recorded_test_pass_rate(self):
        predicted = PredictedMetrics(p_success=0.98, entropy=1.2, impact=35.0, cost=4.0, learning_value=2.5, ev=31.19)

        def mock_subp(cmd, **kwargs):
            if isinstance(cmd, list) and "diff" in cmd:
                return MagicMock(returncode=0, stdout="1 file changed, 1 insertion(+)\n")
            return MagicMock(returncode=1, stdout="", stderr="")

        for recorded, expected in ((0.5, 0.5), (1, 1.0), (0.0, 0.0), ("1.0", None), (True, None), (1.5, None)):
            exec_record = {
                "execution_id": "E-test-rate",
                "plan_branch": "I-test/P-test/_",
                "status": "success",
                "test_pass_rate": recorded,
            }
            with (
                self.subTest(recorded=recorded),
                patch("os.path.exists", side_effect=lambda p: "executions.jsonl" in str(p)),
                patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(exec_record) + "\n")),
                patch("subprocess.run", side_effect=mock_subp),
            ):
                actual, _meta = parse_actual_metrics("E-test-rate", "I-test/P-test/_", predicted)
                self.assertEqual(actual.test_pass_rate, expected)

    def test_parse_actual_metrics_reads_the_measured_rate_from_the_stage_3_summary_row(self):
        """A summary row with the executor's measurement flows into the actuals; a null rate stays not measured."""
        predicted = PredictedMetrics(p_success=0.98, entropy=1.2, impact=35.0, cost=4.0, learning_value=2.5, ev=31.19)

        def mock_subp(cmd, **kwargs):
            if isinstance(cmd, list) and "diff" in cmd:
                return MagicMock(returncode=0, stdout="1 file changed, 1 insertion(+)\n")
            return MagicMock(returncode=1, stdout="", stderr="")

        measured = {
            "test_pass_rate": 0.8,
            "tests_passed": 8,
            "tests_failed": 2,
            "tests_errored": 0,
            "test_exit_code": 1,
        }
        unmeasured = {"test_pass_rate": None, "test_exit_code": 127, "test_run_error": "uv was not found on PATH"}
        for fields, expected in ((measured, 0.8), (unmeasured, None)):
            exec_record = {
                "execution_id": "E-test-measured",
                "plan_branch": "I-test/P-test/_",
                "status": "success",
                "test_source": "stage_3",
                **fields,
            }
            with (
                self.subTest(expected=expected),
                patch("os.path.exists", side_effect=lambda p: "executions.jsonl" in str(p)),
                patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(exec_record) + "\n")),
                patch("subprocess.run", side_effect=mock_subp),
            ):
                actual, _meta = parse_actual_metrics("E-test-measured", "I-test/P-test/_", predicted)
                self.assertEqual(actual.test_pass_rate, expected)
                self.assertEqual(actual.to_dict()["test_pass_rate"], expected)

    def test_parse_actual_metrics_selects_highest_ledger_revision_when_superseded(self):
        predicted = PredictedMetrics(p_success=0.98, entropy=1.2, impact=35.0, cost=4.0, learning_value=2.5, ev=31.19)
        rec_rev1 = {
            "execution_id": "E-test-superseded",
            "plan_branch": "I-test/P-test/_",
            "status": "success",
            "ledger_revision": 1,
        }
        rec_rev2 = {
            "execution_id": "E-test-superseded",
            "plan_branch": "I-test/P-test/_",
            "status": "failure",
            "exit_code": 1,
            "agent_output_truncated": False,
            "agent_output_bytes": 128,
            "ledger_revision": 2,
        }
        jsonl_content = json.dumps(rec_rev1) + "\n" + json.dumps(rec_rev2) + "\n"

        def mock_subp(cmd, **kwargs):
            if isinstance(cmd, list) and "diff" in cmd:
                return MagicMock(returncode=0, stdout="1 file changed, 1 deletion(-)\n")
            return MagicMock(returncode=1, stdout="", stderr="")

        with (
            patch("os.path.exists", side_effect=lambda p: "executions.jsonl" in str(p)),
            patch("builtins.open", unittest.mock.mock_open(read_data=jsonl_content)),
            patch("subprocess.run", side_effect=mock_subp),
        ):
            actual, meta = parse_actual_metrics("E-test-superseded", "I-test/P-test/_", predicted)
            self.assertEqual(meta["ledger_revision"], 2)
            self.assertEqual(meta["status"], "failure")
            self.assertEqual(actual.p_success, 0.0)
            self.assertEqual(actual.exit_code, 1)


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

    def test_compute_calibration_deltas_overconfident(self):
        pred = PredictedMetrics(p_success=0.95, entropy=0.50, impact=50.0, cost=2.0, learning_value=2.0, ev=47.35)
        act = ActualMetrics(p_success=0.00, entropy=2.00, impact=50.0, cost=4.0, learning_value=2.0, ev=-3.60)
        _deltas, ratings, biases = compute_calibration_deltas(pred, act)
        self.assertEqual(biases["p_success"], "Overconfident")
        self.assertEqual(biases["entropy"], "Underestimated Risk")
        self.assertEqual(biases["ev"], "Optimistic Overestimate")
        self.assertEqual(biases["cost"], "Underestimated Cost")
        self.assertEqual(ratings["p_success"], "Moderate")


class TestActualMetricsIntegrity(unittest.TestCase):
    """Test loud failures on diff errors and git show provenance reading."""

    def test_diff_failure_raises_runtime_error(self):
        mock_diff = MagicMock(returncode=128, stderr="fatal: bad revision 'bad-base..bad-target'")
        with (
            patch("os.path.exists", return_value=False),
            patch("subprocess.run", return_value=mock_diff),
        ):
            with self.assertRaises(RuntimeError) as cm:
                parse_actual_metrics(
                    "E-test",
                    "bad-base",
                    PredictedMetrics(),
                    execution_branch="bad-target",
                )
            self.assertIn("Git diff failed between", str(cm.exception))
            self.assertIn("128", str(cm.exception))

    def test_read_git_file_success_and_failure(self):
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="sample content")):
            content = read_git_file("commit-sha", "path/to/file")
            self.assertEqual(content, "sample content")

        with patch("subprocess.run", return_value=MagicMock(returncode=1, stderr="missing")):
            content = read_git_file("commit-sha", "missing/file")
            self.assertIsNone(content)

    def test_parse_actual_metrics_reads_git_show_provenance(self):
        exec_record = {
            "execution_id": "E-prov-1",
            "plan_branch": "I-test/P-test/_",
            "status": "success",
            "duration": 42.0,
            "tokens": 1500,
        }
        jsonl_str = json.dumps(exec_record) + "\n"

        def mock_read_git_file(ref, rel_path, repo_dir="."):
            if "executions.jsonl" in rel_path:
                return jsonl_str
            return None

        diff_res = MagicMock(returncode=0, stdout="3 files changed, 100 insertions(+), 10 deletions(-)\n")

        with (
            patch("sandbox_executor.calibration.actual_evidence.read_git_file", side_effect=mock_read_git_file),
            patch("subprocess.run", return_value=diff_res),
        ):
            actual, _meta = parse_actual_metrics("E-prov-1", "I-test/P-test/_", PredictedMetrics(), "exec-sha")
            self.assertEqual(actual.p_success, 1.0)
            self.assertEqual(actual.duration_seconds, 42.0)
            self.assertEqual(actual.tokens, 1500)
            self.assertEqual(actual.files_changed, 3)
            self.assertEqual(actual.insertions, 100)
            self.assertEqual(actual.deletions, 10)

    def test_parse_actual_metrics_excludes_merged_history(self):
        predicted = PredictedMetrics(p_success=0.95, entropy=1.0, impact=50.0, cost=3.0, learning_value=2.0, ev=45.0)
        exec_record = {
            "execution_id": "E-merge-1",
            "plan_branch": "I-test/P-test/_",
            "status": "success",
            "duration": 25.0,
            "tokens": 1000,
        }
        jsonl_str = json.dumps(exec_record) + "\n"

        def mock_read_git_file(ref, rel_path, repo_dir="."):
            if "executions.jsonl" in rel_path:
                return jsonl_str
            return None

        def mock_subp(cmd, **kwargs):
            if isinstance(cmd, list):
                if "diff" in cmd:
                    self.assertEqual(cmd[3:], ["refs/heads/I-test/P-test/_...refs/heads/exec-branch", "--"])
                    return MagicMock(returncode=0, stdout="1 file changed, 10 insertions(+), 2 deletions(-)\n")
                if "rev-parse" in cmd:
                    return MagicMock(returncode=0, stdout="base1234567890abcdef1234567890abcdef1234\n")
                if "merge-base" in cmd:
                    return MagicMock(returncode=0, stdout="mergebase1234567890abcdef1234567890abcde\n")
                if "rev-list" in cmd:
                    return MagicMock(returncode=0, stdout="1\n")
            return MagicMock(returncode=0, stdout="")

        with (
            patch("sandbox_executor.calibration.actual_evidence.read_git_file", side_effect=mock_read_git_file),
            patch("subprocess.run", side_effect=mock_subp),
        ):
            actual, meta = parse_actual_metrics("E-merge-1", "I-test/P-test/_", predicted, "exec-branch")
            self.assertEqual(actual.files_changed, 1)
            self.assertEqual(actual.insertions, 10)
            self.assertEqual(actual.deletions, 2)
            self.assertEqual(meta["base_sha"], "base1234567890abcdef1234567890abcdef1234")
            self.assertEqual(meta["merge_base_sha"], "mergebase1234567890abcdef1234567890abcde")
            self.assertEqual(meta["merged_commit_count"], 1)

            deltas, ratings, biases = compute_calibration_deltas(predicted, actual)
            report_dict = {
                "plan_id": "P-test",
                "execution_id": "E-merge-1",
                "intent_branch": "I-test/_",
                "agent_id": "antigravity-agent",
                "model_name": "gemini-3.8-flash-medium",
                "timestamp": "2026-10-12T00:00:00.000Z",
                "predicted": predicted.to_dict(),
                "actual": actual.to_dict(),
                "deltas": deltas.to_dict(),
                "accuracy_ratings": ratings,
                "bias_directions": biases,
                "entropy_factors": EntropyFactors().to_dict(),
                "base_sha": meta["base_sha"],
                "merge_base_sha": meta["merge_base_sha"],
                "merged_commit_count": meta["merged_commit_count"],
            }
            md = format_markdown_report(report_dict)
            self.assertIn("- **Base SHA:** `base1234567890abcdef1234567890abcdef1234`", md)
            self.assertIn("- **Merge-Base SHA:** `mergebase1234567890abcdef1234567890abcde`", md)
            self.assertIn("- **Merged Commits:** `1`", md)

    def test_parse_actual_metrics_linear_history(self):
        predicted = PredictedMetrics(p_success=0.95, entropy=1.0, impact=50.0, cost=3.0, learning_value=2.0, ev=45.0)
        exec_record = {
            "execution_id": "E-linear-1",
            "plan_branch": "I-test/P-test/_",
            "status": "success",
            "duration": 20.0,
            "tokens": 500,
        }
        jsonl_str = json.dumps(exec_record) + "\n"

        def mock_read_git_file(ref, rel_path, repo_dir="."):
            if "executions.jsonl" in rel_path:
                return jsonl_str
            return None

        def mock_subp(cmd, **kwargs):
            if isinstance(cmd, list):
                if "diff" in cmd:
                    return MagicMock(returncode=0, stdout="2 files changed, 25 insertions(+), 4 deletions(-)\n")
                if "rev-parse" in cmd:
                    return MagicMock(returncode=0, stdout="linearbase1234567890abcdef1234567890abcd\n")
                if "merge-base" in cmd:
                    return MagicMock(returncode=0, stdout="linearbase1234567890abcdef1234567890abcd\n")
                if "rev-list" in cmd:
                    return MagicMock(returncode=0, stdout="0\n")
            return MagicMock(returncode=0, stdout="")

        with (
            patch("sandbox_executor.calibration.actual_evidence.read_git_file", side_effect=mock_read_git_file),
            patch("subprocess.run", side_effect=mock_subp),
        ):
            actual, meta = parse_actual_metrics("E-linear-1", "I-test/P-test/_", predicted, "exec-linear")
            self.assertEqual(actual.files_changed, 2)
            self.assertEqual(actual.insertions, 25)
            self.assertEqual(actual.deletions, 4)
            self.assertEqual(meta["base_sha"], "linearbase1234567890abcdef1234567890abcd")
            self.assertEqual(meta["merge_base_sha"], "linearbase1234567890abcdef1234567890abcd")
            self.assertEqual(meta["merged_commit_count"], 0)

    def test_merge_base_failure_raises_runtime_error(self):
        predicted = PredictedMetrics()

        def mock_subp(cmd, **kwargs):
            if isinstance(cmd, list):
                if "diff" in cmd:
                    return MagicMock(returncode=0, stdout="")
                if "rev-parse" in cmd:
                    return MagicMock(returncode=0, stdout="sha1\n")
                if "merge-base" in cmd:
                    return MagicMock(returncode=1, stderr="fatal: Not a valid object name")
            return MagicMock(returncode=0, stdout="")

        with (
            patch("os.path.exists", return_value=False),
            patch("subprocess.run", side_effect=mock_subp),
        ):
            with self.assertRaises(RuntimeError) as cm:
                parse_actual_metrics("E-test", "base-ref", predicted, execution_branch="target-ref")
            self.assertIn("Git merge-base failed between", str(cm.exception))


class TestCalibrationInterleavedActionLedger(unittest.TestCase):
    """Regression test suite verifying backward compatibility with interleaved action ledger records."""

    def test_parse_actual_metrics_with_interleaved_action_records(self):
        """Verify parse_actual_metrics ignores action records and matches summary record."""
        action_1 = {
            "entry_type": "action",
            "action_id": "ACT-101",
            "execution_id": "E-interleave",
            "action": "Modify code",
            "category": "code",
            "target": "ledger.py",
            "outcome": "success",
        }
        summary = {
            "entry_type": "summary",
            "execution_id": "E-interleave",
            "plan_branch": "I-test/P-test/_",
            "status": "success",
            "duration": 25.0,
            "tokens": 1200,
        }
        action_2 = {
            "entry_type": "action",
            "action_id": "ACT-102",
            "execution_id": "E-interleave",
            "action": "Run tests",
            "category": "test",
            "target": "pytest",
            "outcome": "failure",
        }
        jsonl_str = json.dumps(action_1) + "\n" + json.dumps(summary) + "\n" + json.dumps(action_2) + "\n"

        def mock_read_git_file(ref, rel_path, repo_dir="."):
            if "executions.jsonl" in rel_path:
                return jsonl_str
            return None

        diff_res = MagicMock(returncode=0, stdout="1 file changed, 10 insertions(+), 0 deletions(-)\n")

        with (
            patch("sandbox_executor.calibration.actual_evidence.read_git_file", side_effect=mock_read_git_file),
            patch("subprocess.run", return_value=diff_res),
        ):
            actual, meta = parse_actual_metrics("E-interleave", "I-test/P-test/_", PredictedMetrics(), "exec-sha")
            self.assertEqual(actual.p_success, 1.0)
            self.assertEqual(actual.duration_seconds, 25.0)
            self.assertEqual(actual.tokens, 1200)
            self.assertEqual(meta.get("entry_type"), "summary")
            self.assertEqual(meta.get("execution_id"), "E-interleave")

    def test_is_metadata_path(self):
        from sandbox_executor.calibration import is_metadata_path

        self.assertTrue(is_metadata_path("holon-knowledge/ledger/executions.jsonl"))
        self.assertTrue(is_metadata_path("./holon-knowledge/.index/action_index.db"))
        self.assertTrue(is_metadata_path("plans/P-1792372417-plan.md"))
        self.assertTrue(is_metadata_path("plans/P-1792372417-plan_calibration.md"))
        self.assertTrue(is_metadata_path(".holon/checkpoints/flow.json"))
        # Execution notes are committed by every execution and are flow metadata, not code.
        self.assertTrue(is_metadata_path("executions/E-1792372417-claude-agent-claude-opus-5-5.md"))
        self.assertFalse(is_metadata_path("src/executions/runner.py"))
        self.assertFalse(is_metadata_path(".tasks/0001-task.md"))
        self.assertFalse(is_metadata_path("tmp/scratch.py"))

        self.assertFalse(is_metadata_path("src/module.py"))
        self.assertFalse(is_metadata_path("apps/sandbox-executor/src/sandbox_executor/flow.py"))
        self.assertFalse(is_metadata_path("Makefile"))
        self.assertFalse(is_metadata_path("README.md"))

    def test_metadata_paths_extend_the_empty_diff_bookkeeping_list(self):
        # One definition of execution notes as metadata: the empty-diff guard's list (#95) is part of calibration's.
        from sandbox_executor.calibration import METADATA_PATH_PREFIXES, is_metadata_path
        from sandbox_executor.git import EMPTY_DIFF_EXCLUDES

        self.assertIn("executions/", EMPTY_DIFF_EXCLUDES)
        self.assertEqual(METADATA_PATH_PREFIXES[: len(EMPTY_DIFF_EXCLUDES)], EMPTY_DIFF_EXCLUDES)
        for prefix in EMPTY_DIFF_EXCLUDES:
            self.assertTrue(is_metadata_path(f"{prefix}example"))

    def test_parse_actual_metrics_excludes_metadata_files(self):
        predicted = PredictedMetrics(p_success=0.90, entropy=2.0, impact=50.0, cost=5.0, learning_value=3.0, ev=35.0)
        exec_record = {
            "execution_id": "E-meta-test",
            "plan_branch": "I-test/P-test/_",
            "status": "success",
            "duration": 10.0,
        }
        jsonl_content = json.dumps(exec_record) + "\n"

        numstat_output = (
            "3\t0\tholon-knowledge/ledger/executions.jsonl\n"
            "60\t0\tplans/P-1234567890-test_calibration.md\n"
            "42\t0\texecutions/E-meta-test.md\n"
            "20\t2\tapps/sandbox-executor/src/sandbox_executor/calibration.py\n"
        )

        def mock_subp(cmd, **kwargs):
            if isinstance(cmd, list) and "diff" in cmd:
                return MagicMock(returncode=0, stdout=numstat_output)
            return MagicMock(returncode=1, stdout="", stderr="")

        with (
            patch("os.path.exists", side_effect=lambda p: "executions.jsonl" in str(p)),
            patch("builtins.open", unittest.mock.mock_open(read_data=jsonl_content)),
            patch("subprocess.run", side_effect=mock_subp),
        ):
            actual, meta = parse_actual_metrics("E-meta-test", "I-test/P-test/_", predicted)
            # Only calibration.py should be counted!
            self.assertEqual(actual.files_changed, 1)
            self.assertEqual(actual.insertions, 20)
            self.assertEqual(actual.deletions, 2)
            self.assertEqual(meta["ssa_obs"], 0.31)


_PLAN_MARKDOWN = """# Plan

## Overall Plan Metrics

| metric | value |
| p_success_pred | 0.85 |
| entropy_pred | 2.1 |
| impact_pred | 45.0 |
| cost_pred | 3.5 |
| learning_value_pred | 2.0 |
| ev_pred | 34.12 |
"""


def _numstat_or_shortstat(stdout: str):
    """A ``subprocess.run`` fake: ``git diff`` prints ``stdout``, every other command fails."""

    def fake(cmd, **_kwargs):
        if isinstance(cmd, list) and "diff" in cmd:
            return MagicMock(returncode=0, stdout=stdout)
        return MagicMock(returncode=1, stdout="", stderr="")

    return fake


def test_actual_metric_extractors_run_in_the_reviewed_order():
    assert [name for name, _extract in ACTUAL_METRIC_EXTRACTORS] == [
        "run_outcome",
        "duration",
        "tokens",
        "test_pass_rate",
        "patch_size",
        "value",
        "cost",
        "entropy",
        "dual_ev",
    ]


@pytest.mark.parametrize(
    "diff_stdout",
    [
        "2 files changed, 40 insertions(+), 5 deletions(-)\n",
        "3\t1\tsrc/a.py\n-\t-\tassets/logo.png\n7\t0\texecutions/E-test-123.md\n",
    ],
)
def test_from_execution_equals_the_parse_actual_metrics_wrapper(diff_stdout):
    predicted = PredictedMetrics(p_success=0.98, entropy=1.2, impact=35.0, cost=4.0, learning_value=2.5, ev=31.19)
    row = {"execution_id": "E-test-123", "plan_branch": "I-test/P-test/_", "duration": 30.5, "tokens": 800, "cost": 2.0}
    results = []
    for measure in (ActualMetrics.from_execution, parse_actual_metrics):
        with (
            patch("os.path.exists", side_effect=lambda p: "executions.jsonl" in str(p)),
            patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(row) + "\n")),
            patch("subprocess.run", side_effect=_numstat_or_shortstat(diff_stdout)),
        ):
            results.append(measure("E-test-123", "I-test/P-test/_", predicted))

    (actual, meta), (wrapped_actual, wrapped_meta) = results
    assert actual == wrapped_actual
    assert meta == wrapped_meta
    assert (actual.duration_seconds, actual.tokens, actual.cost) == (30.5, 800, 2.0)


@pytest.mark.parametrize("plan_file_exists", [True, False])
def test_from_plan_equals_the_parse_predicted_metrics_wrapper(plan_file_exists):
    results = []
    for read in (PredictedMetrics.from_plan, parse_predicted_metrics):
        with (
            patch("os.path.exists", side_effect=lambda p: plan_file_exists and "P-test-456.md" in str(p)),
            patch("builtins.open", unittest.mock.mock_open(read_data=_PLAN_MARKDOWN)),
        ):
            results.append(read("I-1-x/P-test-456/_"))

    assert results[0] == results[1]
    assert results[0][0].p_success == (0.85 if plan_file_exists else PredictedMetrics().p_success)


def test_numstat_and_shortstat_parsers_need_no_git():
    numstat = "3\t1\tsrc/a.py\n-\t-\tassets/logo.png\n7\t0\texecutions/E-1.md\nmalformed line\n"
    assert parse_numstat(numstat) == (["src/a.py", "assets/logo.png"], 2, 3, 1)
    assert parse_shortstat("2 files changed, 40 insertions(+)") == (2, 40, 0)
    assert parse_shortstat("nothing") == (0, 0, 0)


@pytest.mark.parametrize(
    ("error", "limit", "label", "rating"),
    [
        (0.0, 0.05, "High (≤ 0.05)", "Exact"),
        (0.05, 0.05, "High (≤ 0.05)", "High (≤ 0.05)"),
        (0.0501, 0.05, "High (≤ 0.05)", "Moderate"),
        (99.0, None, "High", "High"),
    ],
)
def test_rate_is_exact_high_or_moderate(error, limit, label, rating):
    assert _rate(error, limit, label) == rating


def test_delta_helpers_are_pure():
    predicted = PredictedMetrics(p_success=0.9, entropy=2.0, impact=50.0, cost=5.0, learning_value=3.0, ev=35.0)
    actual = ActualMetrics(p_success=1.0, entropy=0.5, impact=50.0, cost=3.0, learning_value=3.0, ev=47.35)

    errors = _absolute_errors(predicted, actual)
    ratings = _accuracy_ratings(errors)

    assert errors == {
        "p_success": 0.1,
        "entropy": 1.5,
        "impact": 0.0,
        "cost": 2.0,
        "learning_value": 0.0,
        "ev_clean": 12.35,
        "ev_with_bugs": 12.35,
    }
    assert ratings["p_success"] == ratings["entropy"] == ratings["cost"] == "Moderate"
    assert ratings["impact"] == ratings["learning_value"] == "Exact"
    assert ratings["ev"] == ratings["ev_clean"] == ratings["ev_with_bugs"] == "Moderate"
