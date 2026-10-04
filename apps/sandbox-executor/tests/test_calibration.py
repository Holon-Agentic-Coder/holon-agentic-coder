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
    fetch_remote_ref_if_needed,
    format_markdown_report,
    is_calibration_stale,
    load_metrics_physics,
    parse_actual_metrics,
    parse_evaluated_commit_sha,
    parse_predicted_metrics,
    read_git_file,
    resolve_commit_sha,
    resolve_git_ref,
    run_calibrate,
    verify_calibration_freshness,
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
            if isinstance(cmd, list) and "pull/99/head:pr-99" in cmd:
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
        md_str = "## Status\nSuccess\n"

        def mock_read_git_file(ref, rel_path, repo_dir="."):
            if "executions.jsonl" in rel_path:
                return jsonl_str
            if "E-prov-1.md" in rel_path:
                return md_str
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

        expected_co = ["git", "checkout", "-b", "I-1/P-2/E-3/calibrated", "I-1/P-2/E-3/_"]
        self.assertIn(expected_co, called_cmds)


class TestEvaluatedCommitShaAndStaleness(unittest.TestCase):
    """Test commit SHA extraction and staleness detection."""

    def test_parse_evaluated_commit_sha(self):
        sha = "fedcba9876543210fedcba9876543210fedcba98"
        md_text = f"# Report\n\n- **Evaluated Commit SHA:** `{sha}`\n"
        parsed = parse_evaluated_commit_sha(md_text)
        self.assertEqual(parsed, sha)

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
