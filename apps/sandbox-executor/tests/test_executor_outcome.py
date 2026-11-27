"""Tests for ``entrypoint/executor/outcome.py``: the empty-diff guard, over-budget and non-execute outcomes."""

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.entrypoint import executor
from sandbox_executor.ledger import read_merged_rows, read_merged_rows_at_ref

from tests.executor_fixtures import (
    _agent_turns_use_run_cmd,  # noqa: F401  (autouse fixture)
    _cuts_branch,
    _run_executor_main,
    _salvage_add,
    _unscoped_add_all,
    _validate_stub,
    _workspace_is_mounted,  # noqa: F401  (autouse fixture)
)


class TestExecutor(unittest.TestCase):
    def _run_main_with_agent_diff(self, diff_stdout: str, untracked_stdout: str = "", tip_stdout: str = "tip-sha\n"):
        """Run executor.main() with a successful agent whose working-tree diff is diff_stdout.

        Returns (summary_rows, run_cmd_argv_list).
        """
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.0.0"
        mock_runner.build_cmd.return_value = ["agy", "--model", "gemini-3.5-flash", "prompt"]

        def side_effect(args, cwd=None, **kwargs):
            if _cuts_branch(args):
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(json.dumps({"plan_id": "P-123", "intent_branch": "I-456/_", "entropy": 2.0}) + "\n")
            if args[:3] == ["git", "rev-parse", "HEAD"]:
                return MagicMock(returncode=0 if tip_stdout else 1, stdout=tip_stdout, stderr="")
            if "diff" in args and "--name-only" in args:
                return MagicMock(returncode=0, stdout=diff_stdout, stderr="")
            if "ls-files" in args and "--others" in args:
                return MagicMock(returncode=0, stdout=untracked_stdout, stderr="")
            return MagicMock(returncode=0, stdout="OK", stderr="")

        with (
            patch("sandbox_executor.entrypoint.executor.run_cmd", side_effect=side_effect) as mock_run_cmd,
            patch("sandbox_executor.entrypoint.executor.get_runner", return_value=mock_runner),
            patch(
                "sandbox_executor.entrypoint.executor.validate_mounted_workspace",
                new=lambda *_args: tip_stdout.strip(),
            ),
            tempfile.TemporaryDirectory(prefix="sandbox_executor_test_") as tmp_dir,
            patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": tmp_dir}),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]),
        ):
            executor.main()
            rows = read_merged_rows(os.path.join(tmp_dir, "holon-knowledge/ledger/executions.jsonl"))
            argv = [c.args[0] for c in mock_run_cmd.call_args_list if c.args]
        return [r for r in rows if r.get("entry_type") == "summary"], argv

    def test_main_empty_diff_records_failed_and_skips_code_commit(self):
        """An agent run that only touches ledgers and plans is recorded as failed, never success."""
        summaries, argv = self._run_main_with_agent_diff(
            "holon-knowledge/ledger/executions.jsonl\0plans/P-123.md\0", untracked_stdout="plans/notes.md\0"
        )
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["status"], "failed")
        self.assertEqual(summaries[0]["failure_reason"], "empty_diff")
        self.assertIn("no changes outside holon-knowledge/ledger/, plans/ and executions/", summaries[0]["summary"])
        self.assertNotIn("test_pass_rate", summaries[0])
        # Only the scoped salvage add runs; the success staging never does.
        self.assertFalse(_unscoped_add_all(argv))
        self.assertTrue(_salvage_add(argv))
        self.assertFalse(any(c[:2] == ["git", "commit"] and "completed for plan" in " ".join(c) for c in argv))
        diff_calls = [cmd for cmd in argv if "diff" in cmd and "--name-only" in cmd]
        self.assertTrue(diff_calls and "tip-sha" in diff_calls[0])

    def test_main_non_empty_diff_still_succeeds(self):
        """A real source change keeps the existing success path, including git add -A."""
        summaries, argv = self._run_main_with_agent_diff("holon-knowledge/ledger/executions.jsonl\0src/app.py\0")
        self.assertEqual(summaries[0]["status"], "success")
        self.assertNotIn("failure_reason", summaries[0])
        self.assertTrue(_unscoped_add_all(argv))

    def test_main_records_measured_test_fields_on_success_only(self):
        """The summary row of a successful run carries the measurement; a failed (empty-diff) row stays unmeasured."""
        fields = {"test_pass_rate": 0.8, "tests_passed": 8, "tests_failed": 2, "tests_errored": 0, "test_exit_code": 1}
        with patch(
            "sandbox_executor.entrypoint.executor.measure_executor_tests",
            side_effect=lambda _dir, _repo, status, **_kw: fields if status == "success" else {},
        ):
            ok, _ = self._run_main_with_agent_diff("src/app.py\0")
            empty, _ = self._run_main_with_agent_diff("plans/P-123.md\0")
        self.assertEqual(ok[0]["test_pass_rate"], 0.8)
        self.assertEqual(ok[0]["test_exit_code"], 1)
        self.assertNotIn("test_pass_rate", empty[0])

    def test_main_empty_diff_guard_fails_closed_without_plan_tip(self):
        """If the plan tip cannot be resolved the diff is unverifiable, so the run is recorded as failed."""
        summaries, argv = self._run_main_with_agent_diff("src/app.py\0", tip_stdout="")
        self.assertEqual(summaries[0]["status"], "failed")
        self.assertEqual(summaries[0]["failure_reason"], "diff_unverified")
        # Without a plan tip there is no diff base, so nothing is salvaged and nothing is staged wholesale.
        self.assertFalse(any(cmd[:3] == ["git", "add", "-A"] for cmd in argv))

    def _run_over_budget_main(self, sync_result, *, staged=True, has_parent=True, git_calls=None, stdout=None):
        """Run executor.main() on an over-budget plan whose ledger row is staged; return its exit code and records.

        ``staged`` and ``has_parent`` set what the staged-changes check and the parent-commit check report;
        ``git_calls`` collects every command run; ``stdout`` captures standard output.
        """
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.0.0"
        plan_row = {"plan_id": "P-123", "intent_branch": "I-456/_", "entropy": 8.0, "entropy_budget": 5.0}

        def side_effect(args, cwd=None, **kwargs):
            if git_calls is not None:
                git_calls.append(list(args))
            mock_result = MagicMock(returncode=0, stdout="", stderr="")
            if _cuts_branch(args):
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(json.dumps(plan_row) + "\n")
                with open(os.path.join(ledger_dir, "intents.jsonl"), "w") as f:
                    f.write(json.dumps({"branch": "I-456/_", "slug": "parent-intent"}) + "\n")
            elif args[:4] == ["git", "diff", "--cached", "--quiet"]:
                mock_result.returncode = 1 if staged else 0
            elif args == ["git", "rev-parse", "--verify", "HEAD^"]:
                mock_result.returncode = 0 if has_parent else 1
            return mock_result

        stderr = io.StringIO()
        with (
            tempfile.TemporaryDirectory(prefix="sandbox_executor_test_") as tmp_dir,
            patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": tmp_dir}),
            patch("sandbox_executor.entrypoint.executor.run_cmd", side_effect=side_effect),
            patch("sandbox_executor.entrypoint.executor.get_runner", return_value=mock_runner),
            patch("sandbox_executor.entrypoint.executor.validate_mounted_workspace", new=_validate_stub),
            patch("sandbox_executor.entrypoint.executor.sync_and_reconcile_pre_push", return_value=sync_result),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]),
            patch("sys.stderr", stderr),
            contextlib.redirect_stdout(stdout if stdout is not None else io.StringIO()),
        ):
            with self.assertRaises(SystemExit) as refused:
                executor.main()
            ledger_dir = os.path.join(tmp_dir, "holon-knowledge/ledger")
            rows = read_merged_rows(os.path.join(ledger_dir, "executions.jsonl"))
            intent_slugs = [row["slug"] for row in read_merged_rows(os.path.join(ledger_dir, "intents.jsonl"))]
        return refused.exception.code, stderr.getvalue().splitlines(), rows, intent_slugs, mock_runner

    def test_main_over_budget_flow_writes_no_intent_rows(self):
        code, stderr_lines, rows, intent_slugs, mock_runner = self._run_over_budget_main((True, "ok"))

        # A refused plan is a failure for a direct run too, with an exit status the flow can tell apart.
        self.assertEqual(code, executor.OVER_BUDGET_EXIT_CODE)
        self.assertNotIn(executor.OVER_BUDGET_EXIT_CODE, (0, 1))
        # The status is a documented interface: budgets.md, git_flow.md and the specification say exit code 3.
        self.assertEqual(executor.OVER_BUDGET_EXIT_CODE, 3)
        self.assertIn(
            "Error: plan I-456/P-123/_ was not executed: its entropy 8.0 exceeds the entropy budget 5.0 and it lists "
            "no sub-intent list. Re-plan it within the budget, or add an explicit sub-intent list.",
            stderr_lines,
        )
        mock_runner.validate.assert_called_once()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "over_budget")
        self.assertEqual(rows[0]["entropy"], 8.0)
        self.assertEqual(rows[0]["entropy_budget"], 5.0)
        self.assertNotIn("sub_intents", rows[0])
        self.assertIn("exceeds the entropy budget", rows[0]["summary"])
        # The agent never ran, and the intents ledger still holds only the parent row.
        mock_runner.build_cmd.assert_not_called()
        self.assertEqual(intent_slugs, ["parent-intent"])

    def test_main_over_budget_with_a_failed_pre_push_sync_exits_1_without_claiming_a_recorded_row(self):
        code, stderr_lines, rows, intent_slugs, mock_runner = self._run_over_budget_main(
            (False, "unresolvable conflicts")
        )

        # No execution branch records the over_budget row, so the run is a plain failure, not the refusal exit code.
        self.assertEqual(code, 1)
        self.assertIn(
            "Error: plan I-456/P-123/_ was not executed: its entropy 8.0 exceeds the entropy budget 5.0 and it lists "
            "no sub-intent list. Its over_budget row was not recorded on an execution branch.",
            stderr_lines,
        )
        self.assertIn("Refusing to publish execution branch: safety or sync verification failed.", stderr_lines)
        self.assertFalse(any("Re-plan it within the budget" in line for line in stderr_lines))
        self.assertEqual([row["status"] for row in rows], ["over_budget", "failure"])
        self.assertIn("Pre-push sync failure", rows[1]["summary"])
        self.assertTrue(rows[1]["summary"].endswith("The branch was not published."))
        mock_runner.build_cmd.assert_not_called()
        self.assertEqual(intent_slugs, ["parent-intent"])

    NOT_RECORDED_LINE = (
        "Error: plan I-456/P-123/_ was not executed: its entropy 8.0 exceeds the entropy budget 5.0 and it lists "
        "no sub-intent list. Its over_budget row was not recorded on an execution branch."
    )

    def test_main_over_budget_with_nothing_staged_exits_1(self):
        stdout = io.StringIO()
        git_calls: list[list[str]] = []
        code, stderr_lines, rows, _, _ = self._run_over_budget_main(
            (True, "ok"), staged=False, git_calls=git_calls, stdout=stdout
        )

        # Nothing was committed, so no branch records the over_budget row: a plain failure, not the refusal code.
        self.assertEqual(code, 1)
        self.assertIn("No staged changes to commit.", stdout.getvalue().splitlines())
        self.assertIn(self.NOT_RECORDED_LINE, stderr_lines)
        self.assertFalse(any("Re-plan it within the budget" in line for line in stderr_lines))
        self.assertFalse(any(call[:2] in (["git", "commit"], ["git", "push"]) for call in git_calls))
        self.assertEqual([row["status"] for row in rows], ["over_budget"])

    def test_main_over_budget_on_a_parentless_commit_is_not_recorded_and_exits_1(self):
        git_calls: list[list[str]] = []
        code, stderr_lines, rows, _, _ = self._run_over_budget_main((True, "ok"), has_parent=False, git_calls=git_calls)

        # A branch with no parent is never published, so the over_budget row is not recorded and the run fails.
        self.assertEqual(code, 1)
        self.assertIn(["git", "rev-parse", "--verify", "HEAD^"], git_calls)
        self.assertFalse(any(call[:2] == ["git", "push"] for call in git_calls))
        self.assertIn("Refusing to publish execution branch: safety or sync verification failed.", stderr_lines)
        self.assertIn(self.NOT_RECORDED_LINE, stderr_lines)
        self.assertEqual([row["status"] for row in rows], ["over_budget"])

    def test_main_over_budget_commits_locally_exits_with_the_refusal_code_and_never_pushes(self):
        """Run executor.main() unmocked in a worktree cloned from a local origin that holds an over-budget plan."""
        plan_branch = "I-456/P-123/_"
        plan_row = {"plan_id": "P-123", "intent_branch": "I-456/_", "entropy": 8.0, "entropy_budget": 5.0}
        mock_runner = MagicMock()
        mock_runner.get_version.return_value = "1.0.0"

        def git(cwd, *args):
            return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)

        with tempfile.TemporaryDirectory(prefix="sandbox_executor_test_") as root:
            origin = os.path.join(root, "origin.git")
            seed = os.path.join(root, "seed")
            workspace = os.path.join(root, "workspace")
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", origin], check=True, capture_output=True)
            subprocess.run(["git", "init", "-q", "-b", "main", seed], check=True, capture_output=True)
            git(seed, "config", "user.email", "test@example.test")
            git(seed, "config", "user.name", "Test")
            ledger_dir = os.path.join(seed, "holon-knowledge", "ledger")
            os.makedirs(ledger_dir)
            with open(os.path.join(ledger_dir, "intents.jsonl"), "w") as f:
                f.write(json.dumps({"branch": "I-456/_", "slug": "parent-intent"}) + "\n")
            git(seed, "add", "-A")
            git(seed, "commit", "-q", "-m", "intent")
            with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                f.write(json.dumps(plan_row) + "\n")
            git(seed, "add", "-A")
            git(seed, "commit", "-q", "-m", "plan")
            git(seed, "push", "-q", origin, f"HEAD:refs/heads/{plan_branch}")
            # The host fetched the plan branch into the worktree it mounts.
            git(root, "clone", "-q", "--branch", plan_branch, origin, workspace)
            git(workspace, "checkout", "-q", "--detach")
            origin_refs = git(origin, "for-each-ref", "--format=%(refname)").stdout

            stderr = io.StringIO()
            with (
                patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": workspace}),
                patch("sandbox_executor.entrypoint.executor.get_runner", return_value=mock_runner),
                patch("sandbox_executor.entrypoint.executor.sync_and_reconcile_pre_push", return_value=(True, "ok")),
                patch("sys.argv", ["executor.py", plan_branch, "antigravity-agent", "gemini-3.5-flash"]),
                patch("sys.stderr", stderr),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaises(SystemExit) as refused,
            ):
                executor.main()
            heads = git(
                workspace, "for-each-ref", "--format=%(refname:short)", "refs/heads/I-456/P-123/"
            ).stdout.split()
            local = [ref for ref in heads if ref != plan_branch]
            rows = read_merged_rows_at_ref(workspace, local[0], "executions.jsonl")
            origin_after = git(origin, "for-each-ref", "--format=%(refname)").stdout

        mock_runner.build_cmd.assert_not_called()
        # The refusal exit code tells the flow the over_budget row is recorded on a local execution branch.
        self.assertEqual(refused.exception.code, executor.OVER_BUDGET_EXIT_CODE)
        self.assertIn("Re-plan it within the budget", stderr.getvalue())
        self.assertNotIn(self.NOT_RECORDED_LINE, stderr.getvalue().splitlines())
        self.assertEqual(len(local), 1)
        self.assertRegex(local[0], r"^I-456/P-123/E-[^/]+/_$")
        self.assertEqual([row["status"] for row in rows], ["over_budget"])
        # The executor never pushes: the host does that after the container returns.
        self.assertEqual(origin_after, origin_refs)


def test_main_over_budget_reads_intent_budget_when_plan_row_has_none(tmp_path):
    plan_row = {"plan_id": "P-123", "intent_branch": "I-456/_", "entropy": 12.6, "plan_file": "plans/P-123.md"}
    intent_row = {"branch": "I-456", "slug": "parent", "entropy_budget": 5.0}

    rows = _run_executor_main(tmp_path, plan_row, "# Plan\n", intent_row)

    summaries = [r for r in rows if r.get("entry_type") == "summary"]
    assert [s["status"] for s in summaries] == ["over_budget"]
    assert summaries[0]["entropy_budget"] == 5.0
    assert "sub_intents" not in summaries[0]


def test_main_over_budget_without_a_plan_row_judges_the_fallback_entropy_against_the_intent_budget(tmp_path):
    # No plans.jsonl row matches the plan branch: the fallback plan carries entropy 3.0 and no budget of its own, so the
    # intent's budget (2.0) decides and the plan is refused rather than judged against any fixed fallback budget.
    plan_row = {"plan_id": "P-999", "intent_branch": "I-456/_", "entropy": 1.0}
    intent_row = {"branch": "I-456", "slug": "parent", "entropy_budget": 2.0}

    rows = _run_executor_main(tmp_path, plan_row, None, intent_row)

    (summary,) = [r for r in rows if r.get("entry_type") == "summary"]
    assert (summary["status"], summary["entropy"], summary["entropy_budget"]) == ("over_budget", 3.0, 2.0)


def test_main_fallback_plan_of_a_nested_child_derives_the_child_intent_branch(tmp_path):
    # A child's plan branch is I-456/P-9--1-child/P-999/_, so its intent branch is I-456/P-9--1-child, not I-456.
    plan_row = {"plan_id": "P-000", "intent_branch": "I-456/_", "entropy": 1.0}
    intent_row = {"branch": "I-456/P-9--1-child", "slug": "child", "entropy_budget": 2.0}

    rows = _run_executor_main(tmp_path, plan_row, None, intent_row, plan_branch="I-456/P-9--1-child/P-999/_")

    (summary,) = [r for r in rows if r.get("entry_type") == "summary"]
    assert (summary["status"], summary["entropy_budget"]) == ("over_budget", 2.0)


@pytest.mark.parametrize(
    ("plan_md", "stdout_line", "stderr_line"),
    [
        (
            "# Plan\n\n## Sub-intent list\n\n1. Add the parser\n2. Add the CLI flag\n",
            "The plan records an explicit sub-intent list of 2 item(s); referring to it.",
            None,
        ),
        (
            "# Plan\n",
            "Plan entropy 12.6 exceeds the entropy budget 5.0 and the plan lists no sub-intent list; the plan was not "
            "executed.",
            "Error: plan I-456/P-123/_ was not executed: its entropy 12.6 exceeds the entropy budget 5.0 and it lists "
            "no sub-intent list. Re-plan it within the budget, or add an explicit sub-intent list.",
        ),
    ],
    ids=["decomposed", "over-budget"],
)
def test_main_non_execute_outcome_reports_on_stdout_and_stderr(tmp_path, capsys, plan_md, stdout_line, stderr_line):
    plan_row = {"plan_id": "P-123", "intent_branch": "I-456/_", "entropy": 12.6, "plan_file": "plans/P-123.md"}
    intent_row = {"branch": "I-456", "slug": "parent", "entropy_budget": 5.0}

    _run_executor_main(tmp_path, plan_row, plan_md, intent_row)

    out, err = capsys.readouterr()
    assert stdout_line in out.splitlines()
    if stderr_line is None:
        assert "was not executed" not in err
    else:
        assert stderr_line in err.splitlines()


@pytest.mark.parametrize(
    ("plan_md", "expected_status", "expected_outcome"),
    [
        ("# Plan\n\n## Sub-intent list\n\n1. Add the parser\n2. Add the CLI flag\n", "decomposed", "success"),
        ("# Plan\n", "over_budget", "failure"),
    ],
    ids=["decomposed-success", "over-budget-failure"],
)
def test_main_plan_decomposition_action_outcome(tmp_path, monkeypatch, plan_md, expected_status, expected_outcome):
    monkeypatch.setenv("HOLON_ACTION_LOGGING", "1")
    plan_row = {"plan_id": "P-123", "intent_branch": "I-456/_", "entropy": 12.6, "plan_file": "plans/P-123.md"}
    intent_row = {"branch": "I-456", "slug": "parent", "entropy_budget": 5.0}

    rows = _run_executor_main(tmp_path, plan_row, plan_md, intent_row)

    assert [r["status"] for r in rows if r.get("entry_type") == "summary"] == [expected_status]
    actions = [r for r in rows if r.get("event_type") == "plan_decomposition"]
    assert len(actions) == 1
    assert actions[0]["entry_type"] == "action"
    assert actions[0]["outcome"] == expected_outcome
    assert actions[0]["target"] == "I-456/P-123/_"


@pytest.mark.parametrize(
    ("plan_md", "summary", "action", "commit"),
    [
        (
            "# Plan\n\n## Sub-intent list\n\n1. Add the parser\n2. Add the CLI flag\n",
            "The plan I-456/P-123/_ records 2 sub-intent(s); the flow runs each one through the full pipeline.",
            "The plan I-456/P-123/_ records 2 sub-intent(s)",
            "execute: the plan I-456/P-123/_ records 2 sub-intent(s)",
        ),
        (
            "# Plan\n",
            "Plan entropy 12.6 exceeds the entropy budget 5.0 and the plan lists no sub-intent list; the plan was not "
            "executed.",
            "Refused over-budget plan I-456/P-123/_",
            "execute: plan I-456/P-123/_ is over its entropy budget",
        ),
    ],
    ids=["decomposed", "over-budget"],
)
def test_main_non_execute_row_summary_action_and_commit(tmp_path, monkeypatch, plan_md, summary, action, commit):  # noqa: PLR0913
    monkeypatch.setenv("HOLON_ACTION_LOGGING", "1")
    plan_row = {"plan_id": "P-123", "intent_branch": "I-456/_", "entropy": 12.6, "plan_file": "plans/P-123.md"}
    intent_row = {"branch": "I-456", "slug": "parent", "entropy_budget": 5.0}
    commands: list = []

    rows = _run_executor_main(tmp_path, plan_row, plan_md, intent_row, commands)

    (row,) = [r for r in rows if r.get("entry_type") == "summary"]
    assert row["summary"] == summary
    assert row["entropy"] == 12.6
    (recorded,) = [r for r in rows if r.get("event_type") == "plan_decomposition"]
    assert recorded["action"] == action
    # Only the executions ledger is staged; the commit carries the outcome-specific message.
    assert ["git", "add", "holon-knowledge/ledger/pending/executions"] in commands
    assert not any(
        cmd[:2] == ["git", "add"]
        and any(
            arg.startswith(("holon-knowledge/ledger/intents.jsonl", "holon-knowledge/ledger/pending/intents"))
            for arg in cmd
        )
        for cmd in commands
    )
    assert ["git", "commit", "-m", commit] in commands
