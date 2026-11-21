"""Tests for ``entrypoint/executor/pre_push.py``: pre-push synchronisation and ledger conflict reconciliation."""

import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from sandbox_executor.entrypoint import executor
from sandbox_executor.entrypoint.executor import sync_and_reconcile_pre_push

from tests.executor_fixtures import (
    _agent_turns_use_run_cmd,  # noqa: F401  (autouse fixture)
    _cuts_branch,
    _remote_git_cmds,
    _validate_stub,
    _workspace_is_mounted,  # noqa: F401  (autouse fixture)
)


class TestExecutorPrePushSync(unittest.TestCase):
    """Unit tests for executor pre-push remote synchronisation and conflict isolation."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = self.temp_dir.name
        self.remote_dir = os.path.join(self.root, "remote.git")
        self.work_dir = os.path.join(self.root, "work")

        subprocess.run(["git", "init", "--bare", "-b", "main", self.remote_dir], check=True, capture_output=True)
        subprocess.run(["git", "init", "-b", "main", self.work_dir], check=True, capture_output=True)

        self._git(self.work_dir, "config", "user.email", "test@holon.com")
        self._git(self.work_dir, "config", "user.name", "Test User")
        ledger_dir = os.path.join(self.work_dir, "holon-knowledge", "ledger")
        os.makedirs(ledger_dir, exist_ok=True)
        exec_file = os.path.join(ledger_dir, "executions.jsonl")
        with open(exec_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({"execution_id": "E-base", "created_at": "2026-10-06T09:00:00Z"}) + "\n")
        self._git(self.work_dir, "add", "-A")
        self._git(self.work_dir, "commit", "-m", "initial commit")
        self._git(self.work_dir, "remote", "add", "origin", self.remote_dir)
        self._git(self.work_dir, "push", "-u", "origin", "main")

    def _git(self, cwd: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)

    def test_pre_push_sync_reconciles_ledger_only_conflicts(self):
        # 1. On work branch 'feature', append an execution record
        self._git(self.work_dir, "checkout", "-b", "feature")
        ledger_file = os.path.join(self.work_dir, "holon-knowledge", "ledger", "executions.jsonl")
        with open(ledger_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"execution_id": "E-feature", "created_at": "2026-10-06T10:00:00Z"}) + "\n")
        self._git(self.work_dir, "commit", "-am", "feature commit")

        # 2. Advance main in a separate clone with a concurrent execution record
        peer_dir = os.path.join(self.root, "peer")
        subprocess.run(["git", "clone", self.remote_dir, peer_dir], check=True, capture_output=True)
        self._git(peer_dir, "config", "user.email", "peer@holon.com")
        self._git(peer_dir, "config", "user.name", "Peer User")
        peer_ledger = os.path.join(peer_dir, "holon-knowledge", "ledger", "executions.jsonl")
        with open(peer_ledger, "a", encoding="utf-8") as f:
            f.write(json.dumps({"execution_id": "E-remote", "created_at": "2026-10-06T09:30:00Z"}) + "\n")
        self._git(peer_dir, "commit", "-am", "peer commit")
        self._git(peer_dir, "push", "origin", "main")
        # The host fetches the target branch before the container runs; the sync reads that remote-tracking ref.
        self._git(self.work_dir, "fetch", "-q", "origin")

        # 3. Pre-push sync on feature branch
        success, msg = sync_and_reconcile_pre_push(self.work_dir, target_branch="main")
        self.assertTrue(success, f"Pre-push sync failed: {msg}")
        self.assertIn("Successfully reconciled", msg)

        # 4. Verify that executions.jsonl contains all 3 entries sorted chronologically
        with open(ledger_file, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["execution_id"], "E-base")
        self.assertEqual(rows[1]["execution_id"], "E-remote")
        self.assertEqual(rows[2]["execution_id"], "E-feature")

    def test_pre_push_sync_aborts_loudly_on_non_ledger_conflict(self):
        # 1. On work branch 'feature-conflict', modify a non-ledger file
        self._git(self.work_dir, "checkout", "-b", "feature-conflict")
        code_file = os.path.join(self.work_dir, "code.py")
        with open(code_file, "w", encoding="utf-8") as f:
            f.write("x = 1\n")
        self._git(self.work_dir, "add", "code.py")
        self._git(self.work_dir, "commit", "-m", "feature code")

        # 2. Advance main on remote with conflicting change
        peer_dir = os.path.join(self.root, "peer2")
        subprocess.run(["git", "clone", self.remote_dir, peer_dir], check=True, capture_output=True)
        self._git(peer_dir, "config", "user.email", "peer@holon.com")
        self._git(peer_dir, "config", "user.name", "Peer User")
        peer_code = os.path.join(peer_dir, "code.py")
        with open(peer_code, "w", encoding="utf-8") as f:
            f.write("x = 2\n")
        self._git(peer_dir, "add", "code.py")
        self._git(peer_dir, "commit", "-m", "peer conflicting code")
        self._git(peer_dir, "push", "origin", "main")
        # The host fetches the target branch before the container runs; the sync reads that remote-tracking ref.
        self._git(self.work_dir, "fetch", "-q", "origin")

        # 3. Pre-push sync should fail and abort the merge
        success, msg = sync_and_reconcile_pre_push(self.work_dir, target_branch="main")
        self.assertFalse(success)
        self.assertIn("Non-ledger merge conflicts detected", msg)

        # 4. Ensure git merge was aborted and working tree is clean
        status = self._git(self.work_dir, "status", "--porcelain").stdout.strip()
        self.assertEqual(status, "")

    def test_pre_push_sync_without_a_fetched_target_ref_is_a_no_op(self):
        self._git(self.work_dir, "remote", "remove", "origin")
        success, msg = sync_and_reconcile_pre_push(self.work_dir, target_branch="main")
        self.assertTrue(success)
        self.assertEqual(msg, "Remote branch origin/main not found")

    def test_pre_push_sync_never_contacts_the_remote(self):
        # A peer advances the remote, but the host has not fetched: the sync sees nothing new and runs no remote verb.
        peer_dir = os.path.join(self.root, "peer-unfetched")
        subprocess.run(["git", "clone", self.remote_dir, peer_dir], check=True, capture_output=True)
        self._git(peer_dir, "config", "user.email", "peer@holon.com")
        self._git(peer_dir, "config", "user.name", "Peer User")
        self._git(peer_dir, "commit", "--allow-empty", "-m", "peer")
        self._git(peer_dir, "push", "origin", "main")
        commands: list[list[str]] = []
        real_run_cmd = executor.run_cmd

        def recording(args, **kwargs):
            commands.append(list(args))
            return real_run_cmd(args, **kwargs)

        with patch("sandbox_executor.entrypoint.executor.run_cmd", side_effect=recording):
            success, msg = sync_and_reconcile_pre_push(self.work_dir, target_branch="main")
        self.assertTrue(success, msg)
        self.assertIn("up to date", msg)
        self.assertEqual(_remote_git_cmds(commands), [])

    def test_pre_push_sync_multi_ledger_conflicts(self):
        # 1. On feature branch, append to plans.jsonl and executions.jsonl
        self._git(self.work_dir, "checkout", "-b", "feature-multi")
        ledger_dir = os.path.join(self.work_dir, "holon-knowledge", "ledger")
        plan_file = os.path.join(ledger_dir, "plans.jsonl")
        exec_file = os.path.join(ledger_dir, "executions.jsonl")

        with open(plan_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"plan_id": "P-feat", "created_at": "2026-10-06T10:00:00Z"}) + "\n")
        with open(exec_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"execution_id": "E-feat", "created_at": "2026-10-06T10:05:00Z"}) + "\n")
        self._git(self.work_dir, "add", "-A")
        self._git(self.work_dir, "commit", "-m", "feature multi ledger")

        # 2. Advance main on remote with conflicting plans and executions records
        peer_dir = os.path.join(self.root, "peer-multi")
        subprocess.run(["git", "clone", self.remote_dir, peer_dir], check=True, capture_output=True)
        self._git(peer_dir, "config", "user.email", "peer@holon.com")
        self._git(peer_dir, "config", "user.name", "Peer User")
        peer_ledger_dir = os.path.join(peer_dir, "holon-knowledge", "ledger")
        peer_plan_file = os.path.join(peer_ledger_dir, "plans.jsonl")
        peer_exec_file = os.path.join(peer_ledger_dir, "executions.jsonl")

        with open(peer_plan_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"plan_id": "P-peer", "created_at": "2026-10-06T09:30:00Z"}) + "\n")
        with open(peer_exec_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"execution_id": "E-peer", "created_at": "2026-10-06T09:35:00Z"}) + "\n")
        self._git(peer_dir, "add", "-A")
        self._git(peer_dir, "commit", "-m", "peer multi ledger")
        self._git(peer_dir, "push", "origin", "main")
        # The host fetches the target branch before the container runs; the sync reads that remote-tracking ref.
        self._git(self.work_dir, "fetch", "-q", "origin")

        # 3. Pre-push sync
        success, msg = sync_and_reconcile_pre_push(self.work_dir, target_branch="main")
        self.assertTrue(success, f"Pre-push sync failed: {msg}")

        # 4. Verify both files reconciled cleanly
        with open(plan_file, encoding="utf-8") as f:
            p_rows = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(p_rows), 2)
        self.assertEqual(p_rows[0]["plan_id"], "P-peer")
        self.assertEqual(p_rows[1]["plan_id"], "P-feat")

        with open(exec_file, encoding="utf-8") as f:
            e_rows = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(e_rows), 3)

    def test_pre_push_sync_recognizes_expanded_unmerged_prefixes(self):
        # Verify unmerged status check recognises AU, UA, and DD prefixes
        with patch("sandbox_executor.entrypoint.executor.run_cmd") as mock_run_cmd:

            def side_effect(args, **kwargs):  # noqa: PLR0911
                cmd_str = " ".join(args)
                if "remote get-url" in cmd_str:
                    return MagicMock(returncode=0, stdout="origin")
                if "rev-parse --verify origin/main" in cmd_str:
                    return MagicMock(returncode=0, stdout="origin/main")
                if "rev-parse origin/main" in cmd_str:
                    return MagicMock(returncode=0, stdout="tip123")
                if "merge-base HEAD origin/main" in cmd_str:
                    return MagicMock(returncode=0, stdout="base123")
                if "merge origin/main" in cmd_str:
                    return MagicMock(returncode=1, stdout="CONFLICT")
                if "diff --name-only --diff-filter=U" in cmd_str:
                    return MagicMock(returncode=0, stdout="")
                if "status --porcelain" in cmd_str:
                    return MagicMock(returncode=0, stdout="AU non_ledger_conflict.txt\n")
                if "merge --abort" in cmd_str:
                    return MagicMock(returncode=0, stdout="")
                return MagicMock(returncode=0, stdout="")

            mock_run_cmd.side_effect = side_effect
            success, msg = sync_and_reconcile_pre_push(self.work_dir, target_branch="main")
            self.assertFalse(success)
            self.assertIn("Non-ledger merge conflicts detected", msg)
            self.assertIn("non_ledger_conflict.txt", msg)

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.validate_mounted_workspace", new=_validate_stub)
    def test_execution_run_records_action_and_summary_records_with_entry_type(  # noqa: PLR0915
        self, mock_get_runner, mock_run_cmd
    ):
        """Test that execution run with action logging enabled records both action and summary records."""
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.3.3"
        mock_runner.build_cmd.return_value = ["agy", "run"]
        mock_get_runner.return_value = mock_runner

        def side_effect(args, cwd=None, **kwargs):
            mock_res = MagicMock()
            if _cuts_branch(args):
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(
                        json.dumps(
                            {
                                "plan_id": "P-777",
                                "intent_branch": "I-777/_",
                                "entropy": 1.0,
                                "entropy_budget": 5.0,
                            }
                        )
                        + "\n"
                    )
            if "agy" in args:
                mock_res.returncode = 0
                mock_res.stdout = "Task complete.\n"
                mock_res.stderr = ""
            elif "diff" in args and "--cached" in args and "--quiet" in args:
                mock_res.returncode = 1
                mock_res.stdout = ""
                mock_res.stderr = ""
            elif "rev-parse" in args and "--verify" in args and "HEAD^" in args:
                mock_res.returncode = 0
                mock_res.stdout = "parent-commit-sha\n"
            elif args[:3] == ["git", "rev-parse", "HEAD"]:
                mock_res.returncode = 0
                mock_res.stdout = "plan-tip-sha\n"
            elif "diff" in args and "--name-only" in args:
                # The simulated agent changed a source file, so the empty-diff guard allows success.
                mock_res.returncode = 0
                mock_res.stdout = "src/feature.py\0"
            else:
                mock_res.returncode = 0
                mock_res.stdout = ""
                mock_res.stderr = ""
            return mock_res

        mock_run_cmd.side_effect = side_effect

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(
                os.environ,
                {
                    "HOLON_TARGET_WORKTREE": tmp_dir,
                    "HOLON_ACTION_LOGGING": "1",
                    "HOLON_ACTION_ORIGIN": "pr_review",
                    "HOLON_REVIEW_SEVERITY": "critical",
                    "HOLON_REVIEW_ITERATION": "2",
                    "HOLON_REVIEW_FINDING_IDS": "FIND-777",
                },
            ),
            patch("sandbox_executor.entrypoint.executor.get_workspace_dir", return_value=tmp_dir),
            patch("sys.argv", ["executor.py", "I-777/P-777/_", "antigravity-agent", "gemini-3.8-flash"]),
        ):
            executor.main()

            ledger_file = os.path.join(tmp_dir, "holon-knowledge", "ledger", "executions.jsonl")
            self.assertTrue(os.path.exists(ledger_file))
            with open(ledger_file, encoding="utf-8") as f:
                entries = [json.loads(line) for line in f if line.strip()]

            # Should contain action records and summary record
            action_entries = [e for e in entries if e.get("entry_type") == "action"]
            summary_entries = [e for e in entries if e.get("entry_type") == "summary"]

            self.assertGreaterEqual(len(action_entries), 1)
            self.assertEqual(len(summary_entries), 1)

            # Check action entry fields
            cmd_action = action_entries[0]
            self.assertEqual(cmd_action["entry_type"], "action")
            self.assertEqual(cmd_action["event_type"], "command_execution")
            self.assertEqual(cmd_action["action_origin"], "pr_review")
            self.assertEqual(cmd_action["review_severity"], "critical")
            self.assertEqual(cmd_action["review_iteration"], 2)
            self.assertEqual(cmd_action["review_finding_ids"], ["FIND-777"])
            self.assertEqual(cmd_action["outcome"], "success")

            # Check summary entry
            summary = summary_entries[0]
            self.assertEqual(summary["entry_type"], "summary")
            self.assertEqual(summary["status"], "success")
