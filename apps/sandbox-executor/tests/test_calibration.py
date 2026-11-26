"""Unit test suite for sandbox_executor.calibration and holon calibrate CLI command."""

from __future__ import annotations

import json
import sys
import unittest
from unittest.mock import MagicMock, patch

from sandbox_executor.calibration import (
    ActualMetrics,
    CalibrationReport,
    EntropyFactors,
    PredictedMetrics,
    compute_calibration_deltas,
    extract_branch_components,
    fetch_remote_ref_if_needed,
    resolve_commit_sha,
    resolve_git_ref,
    run_calibrate,
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


class TestAppendOnlyCalibratedBranch(unittest.TestCase):
    """Test fast-forward and append-only commit logic on /calibrated branches."""

    @patch("sandbox_executor.calibration.run.format_calibration_report")
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
            report = run_calibrate("I-1/P-2/E-3/_", target_worktree="/tmp/repo", skip_commit=False)
            self.assertEqual(report.calibrated_branch, "I-1/P-2/E-3/calibrated")

        # Verify no `checkout -B` command was run
        for cmd in called_cmds:
            if isinstance(cmd, list):
                self.assertNotIn("-B", cmd)

        # Verify `git checkout I-1/P-2/E-3/calibrated` was executed
        expected_co = ["git", "checkout", "I-1/P-2/E-3/calibrated"]
        self.assertIn(expected_co, called_cmds)

    @patch("sandbox_executor.calibration.run.format_calibration_report")
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
            run_calibrate("I-1/P-2/E-3/_", target_worktree="/tmp/repo", skip_commit=False)

        expected_co = ["git", "checkout", "-b", "I-1/P-2/E-3/calibrated", "refs/remotes/origin/I-1/P-2/E-3/calibrated"]
        self.assertIn(expected_co, called_cmds)

    @patch("sandbox_executor.calibration.run.format_calibration_report")
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
            run_calibrate("I-1/P-2/E-3/_", target_worktree="/tmp/repo", skip_commit=False)

        expected_prefix = ["git", "checkout", "-b", "I-1/P-2/E-3/calibrated"]
        matching_cmds = [cmd for cmd in called_cmds if isinstance(cmd, list) and cmd[:4] == expected_prefix]
        self.assertTrue(matching_cmds, "Expected checkout -b command was not executed")

    @patch("sandbox_executor.calibration.run.format_calibration_report")
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
            report = run_calibrate("origin/I-1/P-2/E-3/_", target_worktree="/tmp/repo", skip_commit=False)

        self.assertEqual(report.calibrated_branch, "I-1/P-2/E-3/calibrated")


class TestNoCommitMessagingAndJson(unittest.TestCase):
    """Test accurate logging and schema output for --no-commit."""

    @patch("sandbox_executor.calibration.run.format_calibration_report")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    @patch("os.makedirs")
    def test_no_commit_console_output(self, mock_makedirs, mock_open, mock_prettier):
        with (
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="")),
            patch("builtins.print") as mock_print,
        ):
            report = run_calibrate("I-1/P-2/E-3/_", target_worktree="/tmp/repo", json_output=False, skip_commit=True)
            self.assertFalse(report.committed)
            self.assertIsNone(report.calibrated_branch)

            printed_msgs = [call[0][0] for call in mock_print.call_args_list if call[0]]
            self.assertTrue(any("(uncommitted)" in msg for msg in printed_msgs))
            self.assertFalse(any("Calibrated branch:" in msg for msg in printed_msgs))

    @patch("sandbox_executor.calibration.run.format_calibration_report")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    @patch("os.makedirs")
    def test_no_commit_json_output(self, mock_makedirs, mock_open, mock_prettier):
        with (
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="")),
            patch("builtins.print") as mock_print,
        ):
            run_calibrate("I-1/P-2/E-3/_", target_worktree="/tmp/repo", json_output=True, skip_commit=True)
            printed_arg = mock_print.call_args[0][0]
            parsed = json.loads(printed_arg)
            self.assertFalse(parsed["committed"])
            self.assertIsNone(parsed["calibrated_branch"])
            self.assertIn("evaluated_commit_sha", parsed)

    @patch("sandbox_executor.calibration.run.format_calibration_report")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    @patch("os.makedirs")
    def test_committed_json_output(self, mock_makedirs, mock_open, mock_prettier):
        with (
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="")),
            patch("builtins.print") as mock_print,
        ):
            run_calibrate("I-1/P-2/E-3/_", target_worktree="/tmp/repo", json_output=True, skip_commit=False)
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
                target_worktree=".",
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
                target_worktree=".",
                json_output=False,
                skip_commit=False,
            )

    @patch("sandbox_executor.cli.run_calibrate")
    def test_cli_calibrate_invocation_with_options(self, mock_run_calibrate):
        test_args = ["holon", "calibrate", "I-123/P-456/E-789/_", "--target-worktree", "/tmp/repo", "--no-commit"]
        with patch.object(sys, "argv", test_args):
            with self.assertRaises(SystemExit) as cm:
                cli_main()
            self.assertEqual(cm.exception.code, 0)
            mock_run_calibrate.assert_called_once_with(
                execution_branch="I-123/P-456/E-789/_",
                target_worktree="/tmp/repo",
                json_output=False,
                skip_commit=True,
            )
