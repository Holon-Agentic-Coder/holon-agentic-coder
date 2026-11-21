"""Tests for ``entrypoint/executor/run.py``: the ``main`` execution flow and incrementally written execution notes."""

import io
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from sandbox_executor.budgets import BudgetBreach
from sandbox_executor.entrypoint import executor

from tests.executor_fixtures import (
    VALID_NOTES,
    IncrementalNotesHarness,
    _agent_turns_use_run_cmd,  # noqa: F401  (autouse fixture)
    _cuts_branch,
    _salvage_add,
    _unscoped_add_all,
    _validate_stub,
    _workspace_is_mounted,  # noqa: F401  (autouse fixture)
)


class TestExecutor(unittest.TestCase):
    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.validate_mounted_workspace", new=_validate_stub)
    def test_main_execution_flow(self, mock_get_runner, mock_run_cmd):
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.0.0"
        mock_runner.build_cmd.return_value = ["agy", "--model", "gemini-3.5-flash", "prompt"]
        mock_get_runner.return_value = mock_runner

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "OK"

        def side_effect(args, cwd=None, **kwargs):
            if _cuts_branch(args):
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(json.dumps({"plan_id": None}) + "\n")
                    f.write(
                        json.dumps(
                            {
                                "plan_id": "P-123",
                                "intent_branch": "I-456/_",
                                "entropy": 2.0,
                                "entropy_budget": 5.0,
                            }
                        )
                        + "\n"
                    )
            return mock_result

        mock_run_cmd.side_effect = side_effect

        with (
            tempfile.TemporaryDirectory(prefix="sandbox_executor_test_") as tmp_dir,
            patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": tmp_dir}),
        ):
            ledger_dir = os.path.join(tmp_dir, "holon-knowledge/ledger")

            with patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]):
                executor.main()

            mock_runner.validate.assert_called_once()
            mock_runner.build_cmd.assert_called_once()
            self.assertTrue(os.path.exists(os.path.join(ledger_dir, "executions.jsonl")))
            with open(os.path.join(ledger_dir, "executions.jsonl")) as ef:
                content = ef.read()
                self.assertIn("P-123", content)
                self.assertIn("success", content)

    @patch("sandbox_executor.entrypoint.executor.converge_prettier")
    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.validate_mounted_workspace", new=_validate_stub)
    def test_main_converges_prettier_on_markdown_artifacts(self, mock_get_runner, mock_run_cmd, mock_converge):
        """Test that executor.main() collects modified markdown files and execution record.

        Also asserts that converge_prettier is invoked before git commit.
        """
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.0.0"
        mock_runner.build_cmd.return_value = ["agy", "--model", "gemini-3.5-flash", "prompt"]
        mock_get_runner.return_value = mock_runner

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "OK"

        def side_effect(args, cwd=None, **kwargs):
            if _cuts_branch(args):
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(
                        json.dumps(
                            {
                                "plan_id": "P-123",
                                "intent_branch": "I-456/_",
                                "entropy": 2.0,
                                "entropy_budget": 5.0,
                            }
                        )
                        + "\n"
                    )
            elif "status" in args and "--porcelain" in args:
                res = MagicMock()
                res.returncode = 0
                res.stdout = " M docs/guide.md\n?? notes.md\n M src/code.py\n"
                return res
            return mock_result

        mock_run_cmd.side_effect = side_effect

        with (
            tempfile.TemporaryDirectory(prefix="sandbox_executor_test_") as tmp_dir,
            patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": tmp_dir}),
        ):
            with patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]):
                executor.main()

            mock_converge.assert_called_once()
            call_files = mock_converge.call_args[0][0]
            self.assertIn("docs/guide.md", call_files)
            self.assertIn("notes.md", call_files)
            self.assertNotIn("src/code.py", call_files)
            self.assertFalse(any(f.startswith("executions/") for f in call_files))
            self.assertEqual(mock_converge.call_args[1].get("repo_dir"), tmp_dir)

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.validate_mounted_workspace", new=_validate_stub)
    def test_main_custom_holon_repo_dir_not_deleted(self, mock_get_runner, mock_run_cmd):
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.0.0"
        mock_get_runner.return_value = mock_runner
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_run_cmd.return_value = mock_result

        with tempfile.TemporaryDirectory() as tmp_dir:
            custom_dir = os.path.join(tmp_dir, "custom_repo")
            os.makedirs(custom_dir, exist_ok=True)
            with (
                patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": custom_dir}),
                patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]),
            ):
                executor.main()

            self.assertTrue(os.path.exists(custom_dir))

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.validate_mounted_workspace", new=_validate_stub)
    def test_main_raises_exception_on_failure(self, mock_get_runner, mock_run_cmd):
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.0.0"
        mock_get_runner.return_value = mock_runner
        mock_run_cmd.side_effect = subprocess.CalledProcessError(1, ["git", "checkout"])

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": tmp_dir}),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]),
            patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                executor.main()
            self.assertIn("Execution failed:", mock_stderr.getvalue())

    @patch("sandbox_executor.entrypoint.executor.run_cmd")
    @patch("sandbox_executor.entrypoint.executor.get_runner")
    @patch("sandbox_executor.entrypoint.executor.validate_mounted_workspace", new=_validate_stub)
    def test_main_git_add_not_called_on_failure(self, mock_get_runner, mock_run_cmd):
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.0.0"
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
                                "plan_id": "P-123",
                                "intent_branch": "I-456/_",
                                "entropy": 2.0,
                                "entropy_budget": 5.0,
                            }
                        )
                        + "\n"
                    )
            if "agy" in args:
                mock_res.returncode = 1
                mock_res.stdout = "Failure stdout"
                mock_res.stderr = "Failure stderr"
            else:
                mock_res.returncode = 0
                mock_res.stdout = ""
                mock_res.stderr = ""
            return mock_res

        mock_run_cmd.side_effect = side_effect

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": tmp_dir}),
        ):
            with patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]):
                executor.main()

            mock_run_cmd_args = [call.args[0] for call in mock_run_cmd.call_args_list if call.args]
            # The failed run's changes are only salvaged through the scoped add, never staged wholesale.
            self.assertFalse(_unscoped_add_all(mock_run_cmd_args))

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


class TestExecutorIncrementalNotes(IncrementalNotesHarness, unittest.TestCase):
    """Execution notes written by the agent, truncation tolerance, re-prompt and budget failure."""

    def test_prompt_carries_the_notes_write_protocol(self):
        run = self.run_executor([self.write_notes(VALID_NOTES)])
        self.assertIn("WRITE PROTOCOL", run["prompts"][0])
        self.assertIn("executions/E-", run["prompts"][0])

    def test_valid_notes_after_truncated_final_message_count_as_success(self):
        run = self.run_executor([self.write_notes(VALID_NOTES, returncode=1, stop_reason="length")])
        summary = self.summary(run)
        self.assertEqual(summary["status"], "success")
        self.assertTrue(summary["execution_notes_valid"])
        self.assertIn("treating the exit status as informational", run["stdout"])
        self.assertTrue(_unscoped_add_all(run["git_cmds"]))
        self.assertRegex(run["stdout"], r"execute: ok \(\d+\.\d min / 45 min\)")

    def test_missing_notes_logs_warning_without_failing(self):
        run = self.run_executor([self.write_notes(None)])
        summary = self.summary(run)
        self.assertEqual(summary["status"], "success")
        self.assertFalse(summary["execution_notes_valid"])
        self.assertIn("WARNING: agent did not write executions/E-", run["stderr"])
        self.assertEqual(len(run["prompts"]), 1)

    def test_partial_notes_trigger_one_targeted_reprompt(self):
        partial = VALID_NOTES.split("## Tests Run")[0]
        run = self.run_executor([self.write_notes(partial), self.write_notes("## Tests Run\n\n- passed\n")])
        self.assertEqual(len(run["prompts"]), 2)
        reprompt = run["prompts"][1]
        self.assertIn("`## Tests Run`", reprompt)
        self.assertNotIn("`## Summary`", reprompt.split("missing required content")[-1])
        self.assertIn("Do not modify any code", reprompt)
        self.assertTrue(self.summary(run)["execution_notes_valid"])

    def test_budget_failure_records_one_act_budget_and_exits_non_zero(self):
        stall = BudgetBreach("execute", "stall", 90, 92)
        run = self.run_executor(
            [self.write_notes("# Execution Record: E-1\n", breach=stall), self.write_notes(None, breach=stall)]
        )
        self.assertEqual(run["exit_code"], 1)
        budget = [e for e in run["entries"] if str(e.get("action_id", "")).startswith("ACT-BUDGET-")]
        self.assertEqual(len(budget), 1)
        self.assertEqual(budget[0]["stage"], "execute")
        self.assertEqual(budget[0]["budget_name"], "stall")
        self.assertEqual(budget[0]["outcome"], "failure")
        self.assertEqual(self.summary(run)["status"], "failure")
        # The code is salvaged by the scoped add (which excludes the ledger and notes); the notes are staged apart.
        add_cmds = [c for c in run["git_cmds"] if c[:2] == ["git", "add"]]
        self.assertTrue(any(arg.startswith("executions/E-") for c in add_cmds if c[2] != "-A" for arg in c))
        self.assertTrue(_salvage_add(add_cmds))
        self.assertFalse(_unscoped_add_all(add_cmds))

    def test_breach_then_notes_only_resume_is_not_success(self):
        # I1: the notes are usable from the start by design, the implementation turn breaches, and the notes-only
        # resume exits 0. The execution must still be a recorded failure and the partial code must not be committed.
        turn_breach = BudgetBreach("execute", "turn", 300, 301)
        head = VALID_NOTES.split("## Steps Executed")[0]
        tail = "## Steps Executed" + VALID_NOTES.split("## Steps Executed")[1]
        run = self.run_executor(
            [self.write_notes(head, returncode=-15, breach=turn_breach), self.write_notes(tail, returncode=0)]
        )
        self.assertEqual(len(run["prompts"]), 2)
        self.assertIn("Do not modify any code", run["prompts"][1])
        summary = self.summary(run)
        self.assertEqual(summary["status"], "failure")
        self.assertIn("turn budget", summary["summary"])
        budget = [e for e in run["entries"] if str(e.get("action_id", "")).startswith("ACT-BUDGET-")]
        self.assertEqual(len(budget), 1)
        self.assertEqual(budget[0]["budget_name"], "turn")
        self.assertIn("implementation turn did not finish", budget[0]["action"])
        self.assertEqual(run["exit_code"], 1)
        add_cmds = [c for c in run["git_cmds"] if c[:2] == ["git", "add"]]
        self.assertFalse(_unscoped_add_all(add_cmds))
        commits = [c for c in run["git_cmds"] if c[:2] == ["git", "commit"]]
        self.assertTrue(all("completed for plan" not in " ".join(c) for c in commits))

    def test_breached_notes_only_reprompt_after_clean_implementation_turn_is_success(self):
        # The implementation turn finished cleanly; only the notes-only re-prompt breached. The code is complete,
        # so the execution is a success, the code is staged and no ACT-BUDGET is recorded.
        partial = VALID_NOTES.split("## Tests Run")[0]
        stall = BudgetBreach("execute", "stall", 90, 91)
        run = self.run_executor([self.write_notes(partial), self.write_notes(None, returncode=-15, breach=stall)])
        self.assertEqual(len(run["prompts"]), 2)
        summary = self.summary(run)
        self.assertEqual(summary["status"], "success")
        self.assertTrue(summary["execution_notes_valid"])
        self.assertEqual(run["exit_code"], 0)
        self.assertFalse([e for e in run["entries"] if str(e.get("action_id", "")).startswith("ACT-BUDGET-")])
        self.assertTrue(_unscoped_add_all(run["git_cmds"]))
        self.assertIn("notes-only follow-up turn stopped", run["stderr"])

    def test_breached_reprompt_with_unusable_notes_after_clean_implementation_turn_is_success(self):
        stall = BudgetBreach("execute", "stall", 90, 91)
        run = self.run_executor(
            [self.write_notes("# Execution Record: E-1\n"), self.write_notes(None, returncode=-15, breach=stall)]
        )
        summary = self.summary(run)
        self.assertEqual(summary["status"], "success")
        self.assertFalse(summary["execution_notes_valid"])
        self.assertEqual(run["exit_code"], 0)
        self.assertFalse([e for e in run["entries"] if str(e.get("action_id", "")).startswith("ACT-BUDGET-")])

    def test_breach_without_stage_time_for_resume_does_not_claim_a_resume(self):
        stage = BudgetBreach("execute", "stage", 2700, 2700)
        run = self.run_executor(
            [self.write_notes("# Execution Record: E-1\n", returncode=-15, breach=stage)],
            env={"HOLON_STAGE_BUDGET_SECONDS": "0.000001"},
        )
        self.assertEqual(len(run["prompts"]), 1)
        budget = [e for e in run["entries"] if str(e.get("action_id", "")).startswith("ACT-BUDGET-")]
        self.assertEqual(len(budget), 1)
        self.assertNotIn("resume did not recover", budget[0]["action"])
        self.assertEqual(run["exit_code"], 1)

    def test_ledger_output_and_error_context_describe_the_implementation_turn(self):
        # After a re-prompt, agent_output_bytes and the command action describe the implementation turn.
        partial = VALID_NOTES.split("## Tests Run")[0]
        impl_out = "implementation output line\n" * 20
        run = self.run_executor(
            [
                self.write_notes(partial, returncode=1, stop_reason="length", stdout=impl_out, stderr="impl err"),
                self.write_notes("## Tests Run\n\n- passed\n", returncode=0, stdout="ok\n"),
            ],
            env={"HOLON_ACTION_LOGGING": "1"},
        )
        summary = self.summary(run)
        self.assertEqual(summary["agent_output_bytes"], len((impl_out + "impl err").encode("utf-8")))
        actions = [e for e in run["entries"] if e.get("event_type") == "command_execution"]
        self.assertEqual(len(actions), 1)
        self.assertIn("impl err", actions[0]["error_context"])

    def test_missing_notes_warning_does_not_claim_output_is_in_the_ledger(self):
        run = self.run_executor([self.write_notes(None)])
        self.assertNotIn("agent output captured in the execution ledger", run["stderr"])
        self.assertIn("agent_output_bytes", run["stderr"])

    def test_breach_with_complete_notes_is_not_success(self):
        stage = BudgetBreach("execute", "stage", 2700, 2700)
        run = self.run_executor([self.write_notes(VALID_NOTES, returncode=-15, breach=stage)])
        self.assertEqual(len(run["prompts"]), 1)
        self.assertEqual(self.summary(run)["status"], "failure")
        self.assertEqual(run["exit_code"], 1)
        self.assertRegex(run["stdout"], r"execute: failed \(\d+\.\d min / 45 min\)")

    def test_generic_exit_one_with_usable_notes_is_failure(self):
        # Exit 1 without a structured truncation report is an agent error (auth/API), not a cut-off final message.
        run = self.run_executor([self.write_notes(VALID_NOTES, returncode=1)])
        summary = self.summary(run)
        self.assertEqual(summary["status"], "failure")
        self.assertNotIn("treating the exit status as informational", run["stdout"])
        self.assertFalse(_unscoped_add_all(run["git_cmds"]))
        self.assertTrue(_salvage_add(run["git_cmds"]))

    def test_truncation_with_incomplete_notes_is_failure(self):
        partial = VALID_NOTES.split("## Tests Run")[0]
        run = self.run_executor(
            [self.write_notes(partial, returncode=1, stop_reason="length"), self.write_notes(None, returncode=0)]
        )
        self.assertEqual(self.summary(run)["status"], "failure")
