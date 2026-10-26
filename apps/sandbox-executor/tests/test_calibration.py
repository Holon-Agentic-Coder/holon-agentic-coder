"""Unit test suite for sandbox_executor.calibration and holon calibrate CLI command."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.calibration import (
    ActualMetrics,
    CalibrationCommitError,
    CalibrationDeltas,
    CalibrationReport,
    EntropyFactors,
    PredictedMetrics,
    compute_calibration_deltas,
    extract_branch_components,
    fetch_remote_ref_if_needed,
    format_markdown_report,
    is_calibration_stale,
    load_metrics_physics,
    parse_actual_metrics,
    parse_evaluated_commit_sha,
    parse_predicted_metrics,
    read_git_file,
    record_calibration_action,
    resolve_commit_sha,
    resolve_git_ref,
    run_calibrate,
    verify_calibration_freshness,
)
from sandbox_executor.cli import main as cli_main


class TestCalibrationDataModels(unittest.TestCase):
    """Test calibration dataclass models and serialisation."""

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

    def test_calibration_report_with_merge_telemetry(self):
        pm = PredictedMetrics()
        am = ActualMetrics()
        deltas, ratings, biases = compute_calibration_deltas(pm, am)
        ef = EntropyFactors()
        cr = CalibrationReport(
            plan_id="P-1",
            execution_id="E-1",
            intent_branch="I-1/_",
            plan_branch="I-1/P-1/_",
            execution_branch="I-1/P-1/E-1/_",
            calibrated_branch="I-1/P-1/E-1/calibrated",
            agent_id="test-agent",
            agent_version="1.0",
            model_name="test-model",
            timestamp="2026-10-12T00:00:00.000Z",
            predicted=pm,
            actual=am,
            deltas=deltas,
            entropy_factors=ef,
            accuracy_ratings=ratings,
            bias_directions=biases,
            markdown_content="# Test",
            base_sha="base_sha_123",
            merge_base_sha="mb_sha_456",
            merged_commit_count=2,
        )
        d = cr.to_dict()
        self.assertEqual(d["base_sha"], "base_sha_123")
        self.assertEqual(d["merge_base_sha"], "mb_sha_456")
        self.assertEqual(d["merged_commit_count"], 2)


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

    def test_extract_raw_commit_sha(self):
        sha = "e5c607b1234567890abcdef1234567890abcdef1"
        comp = extract_branch_components(sha)
        self.assertEqual(comp["execution_id"], sha)
        self.assertEqual(comp["execution_branch"], sha)


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
            pred, _meta = parse_predicted_metrics("P-test-multi")
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
            # The row records no test result: reported as not measured, never as a pass (C10-N1).
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
            "evaluated_commit_sha": "abcdef1234567890abcdef1234567890abcdef12",
            "predicted": pred.to_dict(),
            "actual": act.to_dict(),
            "deltas": deltas.to_dict(),
            "entropy_factors": ef.to_dict(),
            "accuracy_ratings": ratings,
            "bias_directions": biases,
        }

        md = format_markdown_report(report_dict)
        self.assertIn("# Plan Calibration Report: P-1787051525", md)
        self.assertIn("- **Execution ID:** `E-1787051559`", md)
        self.assertNotIn("executions/E-", md)
        self.assertIn("- **Evaluated Commit SHA:** `abcdef1234567890abcdef1234567890abcdef12`", md)
        self.assertIn("## 1. Executive Calibration Summary", md)
        self.assertIn("## 2. Mathematical Derivations & Calibration Errors", md)
        self.assertIn("## 3. Entropy Factor Breakdown", md)
        self.assertIn("## 4. Calibration Assessment", md)
        self.assertIn("| **$P(\\text{success})$**", md)
        self.assertIn("EV_{\\text{pred}}", md)
        self.assertIn("EV_{\\text{actual}}", md)


class TestRefResolution(unittest.TestCase):
    """Test robust git reference resolution across SHAs, local branches, and remotes."""

    def test_resolve_commit_sha_valid(self):
        sha = "1234567890abcdef1234567890abcdef12345678"
        mock_res = MagicMock(returncode=0, stdout=f"{sha}\n")
        with patch("subprocess.run", return_value=mock_res):
            resolved = resolve_commit_sha("HEAD")
            self.assertEqual(resolved, sha)

    def test_resolve_commit_sha_empty_or_failure(self):
        with self.assertRaises(RuntimeError):
            resolve_commit_sha("")

        mock_res = MagicMock(returncode=128, stderr="fatal: bad revision")
        with patch("subprocess.run", return_value=mock_res):
            with self.assertRaises(RuntimeError) as cm:
                resolve_commit_sha("bad-ref")
            self.assertIn("Cannot resolve commit SHA", str(cm.exception))

    def test_resolve_git_ref_sha(self):
        sha = "1234567890abcdef1234567890abcdef12345678"

        def mock_subp(cmd, **kwargs):
            if isinstance(cmd, list) and f"{sha}^{{commit}}" in cmd:
                return MagicMock(returncode=0, stdout=f"{sha}\n")
            return MagicMock(returncode=1, stderr="not found")

        with patch("subprocess.run", side_effect=mock_subp):
            resolved = resolve_git_ref(sha)
            self.assertEqual(resolved, sha)

    def test_resolve_git_ref_local_branch(self):
        def mock_subp(cmd, **kwargs):
            if isinstance(cmd, list) and "refs/heads/feature-branch" in cmd:
                return MagicMock(returncode=0, stdout="commit-hash\n")
            return MagicMock(returncode=1, stderr="")

        with patch("subprocess.run", side_effect=mock_subp):
            resolved = resolve_git_ref("feature-branch")
            self.assertEqual(resolved, "feature-branch")

    def test_resolve_git_ref_remote_tracking(self):
        def mock_subp(cmd, **kwargs):
            if isinstance(cmd, list) and "origin/remote-branch" in cmd:
                return MagicMock(returncode=0, stdout="commit-hash\n")
            return MagicMock(returncode=1, stderr="")

        with patch("subprocess.run", side_effect=mock_subp):
            resolved = resolve_git_ref("remote-branch")
            self.assertEqual(resolved, "origin/remote-branch")

    def test_resolve_git_ref_remote_fetch_fallback(self):
        calls = []

        def mock_subp(cmd, **kwargs):
            calls.append(cmd)
            # Local and remote checks before fetch return 1
            if len(calls) <= 3:
                return MagicMock(returncode=1, stderr="")
            # git fetch succeeds
            if isinstance(cmd, list) and "fetch" in cmd:
                return MagicMock(returncode=0, stdout="")
            # Post-fetch local branch check succeeds
            if isinstance(cmd, list) and "refs/heads/fetched-branch" in cmd:
                return MagicMock(returncode=0, stdout="commit-hash\n")
            return MagicMock(returncode=1, stderr="")

        with patch("subprocess.run", side_effect=mock_subp):
            resolved = resolve_git_ref("fetched-branch")
            self.assertEqual(resolved, "fetched-branch")

    def test_resolve_git_ref_pr_head_ref(self):
        def mock_subp(cmd, **kwargs):
            if isinstance(cmd, list) and any("pull/99/head:pr-99" in str(arg) for arg in cmd):
                return MagicMock(returncode=0, stdout="")
            return MagicMock(returncode=1, stderr="")

        with patch("subprocess.run", side_effect=mock_subp):
            resolved = resolve_git_ref("pull/99/head")
            self.assertEqual(resolved, "pr-99")

    def test_fetch_remote_ref_if_needed(self):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            self.assertTrue(fetch_remote_ref_if_needed("my-branch"))
        with patch("subprocess.run", return_value=MagicMock(returncode=1)):
            self.assertFalse(fetch_remote_ref_if_needed("my-branch"))

    def test_resolve_git_ref_failure_loud(self):
        with patch("subprocess.run", return_value=MagicMock(returncode=1, stderr="not found")):
            with self.assertRaises(RuntimeError) as cm:
                resolve_git_ref("completely-unknown-ref")
            self.assertIn("could not be resolved locally or remotely", str(cm.exception))


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
            patch("sandbox_executor.calibration.read_git_file", side_effect=mock_read_git_file),
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
                    self.assertIn("I-test/P-test/_...exec-branch", cmd[3])
                    return MagicMock(returncode=0, stdout="1 file changed, 10 insertions(+), 2 deletions(-)\n")
                if "rev-parse" in cmd:
                    return MagicMock(returncode=0, stdout="base1234567890abcdef1234567890abcdef1234\n")
                if "merge-base" in cmd:
                    return MagicMock(returncode=0, stdout="mergebase1234567890abcdef1234567890abcde\n")
                if "rev-list" in cmd:
                    return MagicMock(returncode=0, stdout="1\n")
            return MagicMock(returncode=0, stdout="")

        with (
            patch("sandbox_executor.calibration.read_git_file", side_effect=mock_read_git_file),
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
            patch("sandbox_executor.calibration.read_git_file", side_effect=mock_read_git_file),
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


class TestAppendOnlyCalibratedBranch(unittest.TestCase):
    """Test fast-forward and append-only commit logic on /calibrated branches."""

    @patch("sandbox_executor.calibration.converge_prettier")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    @patch("os.makedirs")
    def test_run_calibrate_appends_to_existing_local_branch(self, mock_makedirs, mock_open, mock_prettier):
        called_cmds = []

        def mock_subp(cmd, **kwargs):
            called_cmds.append(cmd)
            # Local calibrated branch exists
            if isinstance(cmd, list) and "refs/heads/I-1/P-2/E-3/calibrated" in cmd:
                return MagicMock(returncode=0, stdout="hash\n")
            # Remote calibrated branch does not exist
            if isinstance(cmd, list) and "refs/remotes/origin/I-1/P-2/E-3/calibrated" in cmd:
                return MagicMock(returncode=1, stderr="")
            # Git diff returns valid diff
            if isinstance(cmd, list) and "diff" in cmd:
                return MagicMock(returncode=0, stdout="1 file changed, 5 insertions(+)\n")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=mock_subp):
            report = run_calibrate("I-1/P-2/E-3/_", repo_dir="/tmp/repo", skip_commit=False)
            self.assertEqual(report.calibrated_branch, "I-1/P-2/E-3/calibrated")

        # Verify no `checkout -B` command was run
        for cmd in called_cmds:
            if isinstance(cmd, list):
                self.assertNotIn("-B", cmd)

        # Verify `git checkout I-1/P-2/E-3/calibrated` was executed
        expected_co = ["git", "checkout", "I-1/P-2/E-3/calibrated"]
        self.assertIn(expected_co, called_cmds)

    @patch("sandbox_executor.calibration.converge_prettier")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    @patch("os.makedirs")
    def test_run_calibrate_checks_out_remote_branch_when_local_absent(self, mock_makedirs, mock_open, mock_prettier):
        called_cmds = []

        def mock_subp(cmd, **kwargs):
            called_cmds.append(cmd)
            # Local absent, remote present
            if isinstance(cmd, list) and "refs/heads/I-1/P-2/E-3/calibrated" in cmd:
                return MagicMock(returncode=1, stderr="")
            if isinstance(cmd, list) and "refs/remotes/origin/I-1/P-2/E-3/calibrated" in cmd:
                return MagicMock(returncode=0, stdout="hash\n")
            if isinstance(cmd, list) and "diff" in cmd:
                return MagicMock(returncode=0, stdout="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=mock_subp):
            run_calibrate("I-1/P-2/E-3/_", repo_dir="/tmp/repo", skip_commit=False)

        expected_co = ["git", "checkout", "-b", "I-1/P-2/E-3/calibrated", "origin/I-1/P-2/E-3/calibrated"]
        self.assertIn(expected_co, called_cmds)

    @patch("sandbox_executor.calibration.converge_prettier")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    @patch("os.makedirs")
    def test_run_calibrate_creates_new_branch_when_neither_exists(self, mock_makedirs, mock_open, mock_prettier):
        called_cmds = []

        def mock_subp(cmd, **kwargs):
            called_cmds.append(cmd)
            # Both absent
            if isinstance(cmd, list) and "refs/heads/I-1/P-2/E-3/calibrated" in cmd:
                return MagicMock(returncode=1, stderr="")
            if isinstance(cmd, list) and "refs/remotes/origin/I-1/P-2/E-3/calibrated" in cmd:
                return MagicMock(returncode=1, stderr="")
            if isinstance(cmd, list) and "diff" in cmd:
                return MagicMock(returncode=0, stdout="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=mock_subp):
            run_calibrate("I-1/P-2/E-3/_", repo_dir="/tmp/repo", skip_commit=False)

        expected_prefix = ["git", "checkout", "-b", "I-1/P-2/E-3/calibrated"]
        matching_cmds = [cmd for cmd in called_cmds if isinstance(cmd, list) and cmd[:4] == expected_prefix]
        self.assertTrue(matching_cmds, "Expected checkout -b command was not executed")

    @patch("sandbox_executor.calibration.converge_prettier")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    @patch("os.makedirs")
    def test_run_calibrate_remote_only_execution_branch_strips_origin(self, mock_makedirs, mock_open, mock_prettier):
        called_cmds = []

        def mock_subp(cmd, **kwargs):
            called_cmds.append(cmd)
            if isinstance(cmd, list) and "rev-parse" in cmd and any("commit" in str(a) for a in cmd):
                return MagicMock(returncode=0, stdout="deadbeef1234567890deadbeef1234567890dead\n")
            if isinstance(cmd, list) and "diff" in cmd:
                return MagicMock(returncode=0, stdout="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=mock_subp):
            report = run_calibrate("origin/I-1/P-2/E-3/_", repo_dir="/tmp/repo", skip_commit=False)

        self.assertEqual(report.calibrated_branch, "I-1/P-2/E-3/calibrated")


class TestEvaluatedCommitShaAndStaleness(unittest.TestCase):
    """Test commit SHA extraction and staleness detection."""

    def test_parse_evaluated_commit_sha(self):
        sha = "fedcba9876543210fedcba9876543210fedcba98"
        md_text = f"# Report\n\n- **Evaluated Commit SHA:** `{sha}`\n"
        parsed = parse_evaluated_commit_sha(md_text)
        self.assertEqual(parsed, sha)

        # None/missing cases return None
        na_text = "# Report\n\n- **Evaluated Commit SHA:** N/A\n"
        self.assertIsNone(parse_evaluated_commit_sha(na_text))
        empty_text = "# Report\n\n- **Evaluated Commit SHA:** ``\n"
        self.assertIsNone(parse_evaluated_commit_sha(empty_text))

    def test_calibration_report_evaluated_commit_sha_defaults_to_none(self):
        pred = PredictedMetrics()
        act = ActualMetrics()
        deltas = CalibrationDeltas(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        ef = EntropyFactors()
        report = CalibrationReport(
            plan_id="P-1",
            execution_id="E-1",
            intent_branch="I-1",
            plan_branch="P-1",
            execution_branch="E-1",
            calibrated_branch="C-1",
            agent_id="test-agent",
            agent_version="1.0",
            model_name="model",
            timestamp="2026-10-05T00:00:00.000Z",
            predicted=pred,
            actual=act,
            deltas=deltas,
            entropy_factors=ef,
            accuracy_ratings={},
            bias_directions={},
            markdown_content="",
        )
        self.assertIsNone(report.evaluated_commit_sha)
        self.assertIsNone(report.to_dict()["evaluated_commit_sha"])

    def test_format_markdown_report_with_none_evaluated_commit_sha(self):
        pred = PredictedMetrics()
        act = ActualMetrics()
        deltas, ratings, biases = compute_calibration_deltas(pred, act)
        ef = EntropyFactors()
        report_dict = {
            "plan_id": "P-1",
            "execution_id": "E-1",
            "intent_branch": "I-1",
            "agent_id": "test-agent",
            "model_name": "model",
            "timestamp": "2026-10-05T00:00:00.000Z",
            "evaluated_commit_sha": None,
            "predicted": pred.to_dict(),
            "actual": act.to_dict(),
            "deltas": deltas.to_dict(),
            "entropy_factors": ef.to_dict(),
            "accuracy_ratings": ratings,
            "bias_directions": biases,
        }
        md = format_markdown_report(report_dict)
        self.assertIn("- **Evaluated Commit SHA:** N/A", md)

    def test_is_calibration_stale_detection(self):
        eval_sha = "1111111111111111111111111111111111111111"
        fresh_head = "1111111111111111111111111111111111111111"
        stale_head = "2222222222222222222222222222222222222222"

        report_md = f"# Report\n- **Evaluated Commit SHA:** `{eval_sha}`\n"

        # Case 1: Fresh (SHA matches HEAD)
        with patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout=f"{fresh_head}\n"),
        ):
            is_stale, e_sha, h_sha = is_calibration_stale(report_md)
            self.assertFalse(is_stale)
            self.assertEqual(e_sha, eval_sha)
            self.assertEqual(h_sha, fresh_head)
            self.assertTrue(verify_calibration_freshness(report_md))

        # Case 2: Stale (HEAD has advanced)
        with patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout=f"{stale_head}\n"),
        ):
            is_stale, e_sha, h_sha = is_calibration_stale(report_md)
            self.assertTrue(is_stale)
            self.assertEqual(e_sha, eval_sha)
            self.assertEqual(h_sha, stale_head)
            self.assertFalse(verify_calibration_freshness(report_md))

    def test_is_calibration_stale_missing_sha(self):
        report_md = "# Report without SHA\n- **Evaluation Timestamp:** 2026-10-04\n"
        is_stale, _e_sha, h_sha = is_calibration_stale(report_md)
        self.assertTrue(is_stale)
        self.assertEqual(h_sha, "missing_evaluated_sha")


class TestNoCommitMessagingAndJson(unittest.TestCase):
    """Test accurate logging and schema output for --no-commit."""

    @patch("sandbox_executor.calibration.converge_prettier")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    @patch("os.makedirs")
    def test_no_commit_console_output(self, mock_makedirs, mock_open, mock_prettier):
        with (
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="")),
            patch("builtins.print") as mock_print,
        ):
            report = run_calibrate("I-1/P-2/E-3/_", repo_dir="/tmp/repo", json_output=False, skip_commit=True)
            self.assertFalse(report.committed)
            self.assertIsNone(report.calibrated_branch)

            printed_msgs = [call[0][0] for call in mock_print.call_args_list if call[0]]
            self.assertTrue(any("(uncommitted)" in msg for msg in printed_msgs))
            self.assertFalse(any("Calibrated branch:" in msg for msg in printed_msgs))

    @patch("sandbox_executor.calibration.converge_prettier")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    @patch("os.makedirs")
    def test_no_commit_json_output(self, mock_makedirs, mock_open, mock_prettier):
        with (
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="")),
            patch("builtins.print") as mock_print,
        ):
            run_calibrate("I-1/P-2/E-3/_", repo_dir="/tmp/repo", json_output=True, skip_commit=True)
            printed_arg = mock_print.call_args[0][0]
            parsed = json.loads(printed_arg)
            self.assertFalse(parsed["committed"])
            self.assertIsNone(parsed["calibrated_branch"])
            self.assertIn("evaluated_commit_sha", parsed)

    @patch("sandbox_executor.calibration.converge_prettier")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    @patch("os.makedirs")
    def test_committed_json_output(self, mock_makedirs, mock_open, mock_prettier):
        with (
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="")),
            patch("builtins.print") as mock_print,
        ):
            run_calibrate("I-1/P-2/E-3/_", repo_dir="/tmp/repo", json_output=True, skip_commit=False)
            printed_arg = mock_print.call_args[0][0]
            parsed = json.loads(printed_arg)
            self.assertTrue(parsed["committed"])
            self.assertIsNotNone(parsed["calibrated_branch"])
            self.assertIn("evaluated_commit_sha", parsed)


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
                repo_dir=".",
                json_output=False,
                skip_commit=False,
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
                repo_dir=".",
                json_output=True,
                skip_commit=False,
            )

    @patch("sandbox_executor.cli.run_calibrate")
    def test_cli_calibrate_invocation_with_options(self, mock_run_calibrate):
        test_args = ["holon", "calibrate", "I-123/P-456/E-789/_", "--repo-dir", "/tmp/repo", "--no-commit"]
        with patch.object(sys, "argv", test_args):
            with self.assertRaises(SystemExit) as cm:
                cli_main()
            self.assertEqual(cm.exception.code, 0)
            mock_run_calibrate.assert_called_once_with(
                execution_branch="I-123/P-456/E-789/_",
                repo_dir="/tmp/repo",
                json_output=False,
                skip_commit=True,
            )


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
            patch("sandbox_executor.calibration.read_git_file", side_effect=mock_read_git_file),
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
        # C7-I1: execution notes are committed by every execution and are flow metadata, not code.
        self.assertTrue(is_metadata_path("executions/E-1792372417-claude-agent-claude-opus-5-5.md"))
        self.assertFalse(is_metadata_path("src/executions/runner.py"))
        self.assertFalse(is_metadata_path(".beans/0001-task.md"))
        self.assertFalse(is_metadata_path("todo/scratch.py"))

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


# ---------------------------------------------------------------------------
# Stage 5 ACT-CAL ledger provenance regression tests (pytest style)
# ---------------------------------------------------------------------------

_REAL_EXECUTIONS_LEDGER = Path(__file__).resolve().parents[3] / "holon-knowledge" / "ledger" / "executions.jsonl"
_EXEC_BRANCH = "I-1-cal/P-2/E-3/_"
_GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Calibration Test",
    "GIT_AUTHOR_EMAIL": "calibration-test@example.invalid",
    "GIT_COMMITTER_NAME": "Calibration Test",
    "GIT_COMMITTER_EMAIL": "calibration-test@example.invalid",
}


def _fingerprint(path: Path) -> tuple[int, str]:
    if not path.exists():
        return (-1, "")
    data = path.read_bytes()
    return (data.count(b"\n"), hashlib.sha256(data).hexdigest())


@pytest.fixture
def guard_real_ledger():
    """Fail if a test modifies the repository's own canonical execution ledger."""
    before = _fingerprint(_REAL_EXECUTIONS_LEDGER)
    yield
    assert _fingerprint(_REAL_EXECUTIONS_LEDGER) == before, "Real executions.jsonl was modified"


@pytest.fixture
def calibration_repo(tmp_path, monkeypatch, guard_real_ledger):
    """Create a throwaway git repository with plan and execution branches."""
    for key, value in _GIT_IDENTITY.items():
        monkeypatch.setenv(key, value)

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True).stdout

    git("init", "-q", "-b", "main")
    ledger = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"
    ledger.parent.mkdir(parents=True)
    summary = {
        "execution_id": "E-3",
        "plan_branch": "I-1-cal/P-2/_",
        "agent": "test-agent",
        "model": "test-model",
        "status": "success",
        "entry_type": "summary",
        "created_at": "2026-01-01T00:00:00.000Z",
        "duration": 12.0,
        "exit_code": 0,
    }
    ledger.write_text(json.dumps(summary) + "\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    git("checkout", "-q", "-b", "I-1-cal/P-2/_")
    git("checkout", "-q", "-b", _EXEC_BRANCH)
    (tmp_path / "feature.py").write_text("print('hello')\n", encoding="utf-8")
    git("add", "feature.py")
    git("commit", "-q", "-m", "feature")
    (tmp_path / "untracked_scratch.txt").write_text("not for commit\n", encoding="utf-8")
    return tmp_path, git


def _cal_rows(ledger_text: str) -> list[dict]:
    rows = [json.loads(line) for line in ledger_text.splitlines() if line.strip()]
    return [r for r in rows if str(r.get("action_id", "")).startswith("ACT-CAL-")]


def test_run_calibrate_commit_failure_warning_redacts_credentials(calibration_repo, caplog):
    """A failed calibration commit warns without echoing credentials from the exception text."""
    repo, _ = calibration_repo
    caplog.set_level("WARNING", logger="sandbox_executor.calibration")
    sample_value = "ghp_" + "C" * 36
    with (
        patch("sandbox_executor.calibration.converge_prettier"),
        patch(
            "sandbox_executor.calibration.calibration_commit_env",
            side_effect=RuntimeError(f"credential helper {sample_value}"),
        ),
        pytest.raises(CalibrationCommitError) as excinfo,
    ):
        run_calibrate(_EXEC_BRANCH, repo_dir=str(repo), skip_commit=False)

    messages = [rec.getMessage() for rec in caplog.records]
    assert any("Git commit of calibration report" in m and "[REDACTED_SECRET]" in m for m in messages)
    assert all(sample_value not in m for m in messages)
    assert "[REDACTED_SECRET]" in str(excinfo.value)
    assert sample_value not in str(excinfo.value)


_HOOK_SECRET = "ghp_" + "H" * 36


def _install_rejecting_pre_commit_hook(repo: Path, git) -> None:
    """Install a repository-local pre-commit hook that rejects every commit (C9-N1)."""
    hooks_dir = repo / ".git" / "test-hooks"
    hooks_dir.mkdir()
    hook = hooks_dir / "pre-commit"
    hook.write_text(f"#!/bin/sh\necho 'pre-commit rejected with {_HOOK_SECRET}' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    git("config", "core.hooksPath", str(hooks_dir))


def test_run_calibrate_rejected_report_commit_raises(calibration_repo, capsys):
    """A pre-commit hook that rejects the report commit makes run_calibrate fail, redacted (C9-N1)."""
    repo, git = calibration_repo
    _install_rejecting_pre_commit_hook(repo, git)
    ledger_rel = "holon-knowledge/ledger/executions.jsonl"
    with (
        patch("sandbox_executor.calibration.converge_prettier"),
        pytest.raises(CalibrationCommitError) as excinfo,
    ):
        run_calibrate(_EXEC_BRANCH, repo_dir=str(repo), skip_commit=False)

    message = str(excinfo.value)
    assert "plans/P-2_calibration.md" in message
    assert "pre-commit rejected" in message
    assert _HOOK_SECRET not in message
    assert "committed" not in capsys.readouterr().out
    # Nothing reached git: the report is absent from HEAD and HEAD still carries no ACT-CAL row.
    head_has_report = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", "HEAD:plans/P-2_calibration.md"], capture_output=True
    )
    assert head_has_report.returncode != 0
    assert _cal_rows(git("show", f"HEAD:{ledger_rel}")) == []
    assert git("log", "--format=%s", "-1").strip() == "feature"


def test_cli_calibrate_rejected_report_commit_exits_non_zero(calibration_repo, capsys):
    """`holon calibrate` exits 1 with a redacted error when the report commit is rejected (C9-N1)."""
    repo, git = calibration_repo
    _install_rejecting_pre_commit_hook(repo, git)
    test_args = ["holon", "calibrate", _EXEC_BRANCH, "--repo-dir", str(repo)]
    with (
        patch("sandbox_executor.calibration.converge_prettier"),
        patch.object(sys, "argv", test_args),
        pytest.raises(SystemExit) as excinfo,
    ):
        cli_main()

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "Error during calibration: Git commit of calibration report plans/P-2_calibration.md failed" in captured.err
    assert _HOOK_SECRET not in captured.err
    assert "committed" not in captured.out


def test_run_calibrate_appends_and_commits_act_cal(calibration_repo):
    repo, git = calibration_repo
    with patch("sandbox_executor.calibration.converge_prettier"):
        report = run_calibrate(_EXEC_BRANCH, repo_dir=str(repo), skip_commit=False)

    ledger_rel = "holon-knowledge/ledger/executions.jsonl"
    rows = _cal_rows((repo / ledger_rel).read_text(encoding="utf-8"))
    assert len(rows) == 1
    row = rows[0]
    assert list(row.keys()) == [
        "action_id",
        "execution_id",
        "action",
        "category",
        "target",
        "outcome",
        "entry_type",
        "action_origin",
        "labels",
        "error_context",
        "plan_branch",
        "execution_branch",
        "evaluated_commit_sha",
        "predicted_metrics",
        "actual_metrics",
        "calibration_deltas",
        "calibration_report_path",
        "created_at",
    ]
    assert row["entry_type"] == "action"
    assert row["action_origin"] == "calibration"
    assert row["outcome"] == "success"
    assert row["action"] == "Calibration report generated for plan P-2"
    assert row["execution_id"].startswith("CAL-P-2-")
    assert row["plan_branch"] == "I-1-cal/P-2/_"
    assert row["execution_branch"] == _EXEC_BRANCH
    assert row["target"] == report.calibrated_branch
    assert row["predicted_metrics"] == report.predicted.to_dict()
    assert row["actual_metrics"] == report.actual.to_dict()
    assert row["calibration_deltas"] == report.deltas.to_dict()
    assert row["calibration_report_path"] == "plans/P-2_calibration.md"

    # The ACT-CAL row is committed with the report on the calibrated branch, and nothing else is.
    assert _cal_rows(git("show", f"HEAD:{ledger_rel}")) == rows
    committed = sorted(git("show", "--name-only", "--pretty=format:", "HEAD").split())
    assert committed == [ledger_rel, "plans/P-2_calibration.md"]
    assert "untracked_scratch.txt" in git("status", "--porcelain")
    # Exactly one summary row, unchanged: no duplicate summary was written.
    all_rows = [json.loads(line) for line in git("show", f"HEAD:{ledger_rel}").splitlines() if line.strip()]
    assert [r for r in all_rows if r.get("entry_type") == "summary"] == [
        json.loads((repo / ledger_rel).read_text(encoding="utf-8").splitlines()[0])
    ]


def test_run_calibrate_metrics_unaffected_by_act_cal_rows(calibration_repo):
    repo, _ = calibration_repo
    with patch("sandbox_executor.calibration.converge_prettier"):
        first = run_calibrate(_EXEC_BRANCH, repo_dir=str(repo), skip_commit=True)
        second = run_calibrate(_EXEC_BRANCH, repo_dir=str(repo), skip_commit=False)
        third = run_calibrate(_EXEC_BRANCH, repo_dir=str(repo), skip_commit=True)
    assert first.actual.to_dict() == second.actual.to_dict() == third.actual.to_dict()
    assert first.predicted.to_dict() == third.predicted.to_dict()


def test_run_calibrate_skip_commit_appends_nothing(calibration_repo):
    repo, _ = calibration_repo
    ledger = repo / "holon-knowledge" / "ledger" / "executions.jsonl"
    before = ledger.read_bytes()
    with patch("sandbox_executor.calibration.converge_prettier"):
        run_calibrate(_EXEC_BRANCH, repo_dir=str(repo), skip_commit=True)
    assert ledger.read_bytes() == before


def test_run_calibrate_ledger_failure_warns_and_still_commits_report(calibration_repo, caplog):
    repo, git = calibration_repo
    caplog.set_level("WARNING")
    with (
        patch("sandbox_executor.calibration.converge_prettier"),
        patch("sandbox_executor.ledger.LedgerLock.__enter__", side_effect=PermissionError("ledger denied")),
    ):
        run_calibrate(_EXEC_BRANCH, repo_dir=str(repo), skip_commit=False)

    ledger_path = os.path.join(str(repo), "holon-knowledge", "ledger", "executions.jsonl")
    warnings = [rec.getMessage() for rec in caplog.records if rec.levelname == "WARNING"]
    assert any("ACT-CAL" in msg and ledger_path in msg and "ledger denied" in msg for msg in warnings)
    committed = git("show", "--name-only", "--pretty=format:", "HEAD").split()
    assert committed == ["plans/P-2_calibration.md"]


def test_run_calibrate_ledger_failure_reaches_operator_log_fn(calibration_repo):
    repo, git = calibration_repo
    logs: list[str] = []
    with (
        patch("sandbox_executor.calibration.converge_prettier"),
        patch("sandbox_executor.ledger.LedgerLock.__enter__", side_effect=PermissionError("ledger denied")),
    ):
        run_calibrate(_EXEC_BRANCH, repo_dir=str(repo), skip_commit=False, log_fn=logs.append)

    ledger_path = os.path.join(str(repo), "holon-knowledge", "ledger", "executions.jsonl")
    assert any("ACT-CAL" in msg and ledger_path in msg and "ledger denied" in msg for msg in logs)
    committed = git("show", "--name-only", "--pretty=format:", "HEAD").split()
    assert committed == ["plans/P-2_calibration.md"]


def test_record_calibration_action_accepts_fallback_dict(tmp_path, guard_real_ledger):
    logs: list[str] = []
    row = record_calibration_action(
        {"plan_id": "P-9", "calibrated_branch": "I-1/P-9/E-1/calibrated", "execution_branch": "I-1/P-9/E-1/_"},
        str(tmp_path),
        "plans/P-9_calibration.md",
        outcome="failure",
        error_context="Calibration run failed: boom",
        log_fn=logs.append,
    )
    assert row is not None
    assert row["action_id"].startswith("ACT-CAL-")
    assert row["outcome"] == "failure"
    assert row["action"] == "Calibration failed for plan P-9; fallback report written"
    assert row["predicted_metrics"] is None
    written = (tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl").read_text(encoding="utf-8")
    assert json.loads(written) == row
    assert logs == []


def test_record_calibration_action_redacts_error_context(tmp_path, guard_real_ledger):
    sample_value = "ghp_" + "R" * 36
    row = record_calibration_action(
        {"plan_id": "P-9", "calibrated_branch": "I-1/P-9/E-1/calibrated", "execution_branch": "I-1/P-9/E-1/_"},
        str(tmp_path),
        "plans/P-9_calibration.md",
        outcome="failure",
        error_context=f"Calibration run failed: remote rejected {sample_value}",
    )
    assert row is not None
    assert row["error_context"] == "Calibration run failed: remote rejected [REDACTED_SECRET]"
    written = (tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl").read_text(encoding="utf-8")
    assert sample_value not in written
    assert json.loads(written) == row


def _stage5_context(repo: Path, *, dry_run: bool):
    from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus

    ctx = FlowContext(
        repo_dir=str(repo),
        execution_branch=_EXEC_BRANCH,
        plan_branch="I-1-cal/P-2/_",
        dry_run=dry_run,
        skip_push=True,
    )
    ctx.stage_results["execute"] = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS)
    return ctx


def test_stage5_dry_run_leaves_executions_ledger_unchanged(calibration_repo):
    """A dry-run Stage 5 appends nothing, including no reconciliation event."""
    from sandbox_executor.flow import StageStatus, run_calibrate_stage

    repo, _ = calibration_repo
    ledger = repo / "holon-knowledge" / "ledger" / "executions.jsonl"
    before = ledger.read_bytes()
    with patch("sandbox_executor.calibration.converge_prettier"):
        res = run_calibrate_stage(_stage5_context(repo, dry_run=True))
    assert res.status == StageStatus.SUCCESS
    assert ledger.read_bytes() == before


def test_stage5_rejected_report_commit_takes_failure_path(calibration_repo):
    """A rejected report commit leaves every Stage 5 row uncommitted and skips push / ff (C9-N1)."""
    from sandbox_executor.flow import run_calibrate_stage
    from sandbox_executor.git import run_git as real_run_git

    repo, git = calibration_repo
    _install_rejecting_pre_commit_hook(repo, git)
    ledger_rel = "holon-knowledge/ledger/executions.jsonl"
    calibrated = "I-1-cal/P-2/E-3/calibrated"
    exec_head_before = git("rev-parse", _EXEC_BRANCH).strip()
    ctx = _stage5_context(repo, dry_run=False)
    ctx.skip_push = False
    git_calls: list[list[str]] = []

    def recording_run_git(args, *a, **kw):
        git_calls.append(list(args))
        return real_run_git(args, *a, **kw)

    with (
        patch("sandbox_executor.calibration.converge_prettier"),
        patch("sandbox_executor.flow.converge_prettier"),
        patch("sandbox_executor.flow.check_and_resolve_stage_conflicts"),
        patch("sandbox_executor.flow.run_git", side_effect=recording_run_git),
    ):
        run_calibrate_stage(ctx)

    # No push, no fast-forward, no reconciliation commit.
    assert not [c for c in git_calls if c[:1] in (["push"], ["merge"]) or c[:1] == ["commit"]]
    assert git("rev-parse", _EXEC_BRANCH).strip() == exec_head_before
    assert git("rev-parse", calibrated).strip() == exec_head_before
    for ref in (calibrated, _EXEC_BRANCH):
        assert _cal_rows(git("show", f"{ref}:{ledger_rel}")) == []
    # The ACT-CAL rows stay in the working tree: the rejected success row, then the failure row.
    working = (repo / ledger_rel).read_text(encoding="utf-8")
    assert [r["outcome"] for r in _cal_rows(working)] == ["success", "failure"]
    failure = _cal_rows(working)[-1]
    assert "Git commit of calibration report plans/P-2_calibration.md failed" in failure["error_context"]
    assert failure["action"] == "Calibration failed for plan P-2; existing report and ledger rows left uncommitted"
    assert _HOOK_SECRET not in working
    recon = [json.loads(line) for line in working.splitlines() if line.strip()]
    assert [r for r in recon if r.get("event_type") == "reconciliation"]
    assert ledger_rel in git("status", "--porcelain")
    joined = "\n".join(ctx.logs)
    assert _HOOK_SECRET not in joined
    assert "left uncommitted" in joined
    assert "Stage 5 completed with a calibration failure" in joined


def test_stage5_commits_reconciliation_row_on_calibrated_branch(calibration_repo):
    """The Stage 5 reconciliation row is committed on /calibrated and reaches the execution branch."""
    from sandbox_executor.flow import StageStatus, run_calibrate_stage

    repo, git = calibration_repo
    ledger_rel = "holon-knowledge/ledger/executions.jsonl"
    ctx = _stage5_context(repo, dry_run=False)
    with (
        patch("sandbox_executor.calibration.converge_prettier"),
        patch("sandbox_executor.flow.check_and_resolve_stage_conflicts"),
    ):
        res = run_calibrate_stage(ctx)
    assert res.status == StageStatus.SUCCESS

    calibrated = "I-1-cal/P-2/E-3/calibrated"
    assert ctx.calibrated_branch == calibrated
    # The ledger has no uncommitted rows left behind.
    assert ledger_rel not in git("status", "--porcelain")
    for ref in (calibrated, _EXEC_BRANCH):
        rows = [json.loads(line) for line in git("show", f"{ref}:{ledger_rel}").splitlines() if line.strip()]
        recon = [r for r in rows if r.get("event_type") == "reconciliation"]
        assert len(recon) == 1
        assert recon[0]["parent_action_id"] == _EXEC_BRANCH
        assert recon[0]["verdict"] == "success"
        assert len(_cal_rows(git("show", f"{ref}:{ledger_rel}"))) == 1
    subjects = git("log", "--format=%s", "-2", calibrated).splitlines()
    assert subjects == [
        "chore(calibration): record stage 5 reconciliation",
        "chore(calibration): add calibration report for P-2",
    ]
    assert git("show", "--name-only", "--pretty=format:", calibrated).split() == [ledger_rel]
