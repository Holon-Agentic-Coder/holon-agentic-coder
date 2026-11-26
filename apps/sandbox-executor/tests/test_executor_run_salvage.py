"""Tests for ``entrypoint/executor/run.py`` partial-work salvage, no-push and resume on a failed execution."""

import contextlib
import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sandbox_executor.budgets import BudgetBreach
from sandbox_executor.entrypoint import executor
from sandbox_executor.ledger import read_merged_rows_at_ref
from sandbox_executor.turns import TurnResult

from tests.executor_fixtures import (
    VALID_NOTES,
    _agent_turns_use_run_cmd,  # noqa: F401  (autouse fixture)
    _workspace_is_mounted,  # noqa: F401  (autouse fixture)
)
from tests.repo_templates import copy_template, finish_template


class TestExecutorPartialWorkSalvage(unittest.TestCase):
    """An execution's code is never lost: salvage on failure, identity before dispatch, no push, resume."""

    PLAN = "I-456/P-123/_"

    @classmethod
    def setUpClass(cls):
        """Build the bare origin and the seed with the pushed plan commit once; ``setUp`` copies them per test."""
        tmp = tempfile.TemporaryDirectory(prefix="sandbox_salvage_template_")
        cls.addClassCleanup(tmp.cleanup)
        cls.template = tmp.name
        bare = os.path.join(cls.template, "origin.git")
        seed = os.path.join(cls.template, "seed")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", bare], check=True)
        cls.git(bare, "config", "core.hooksPath", os.path.join(bare, "hooks"))
        subprocess.run(["git", "init", "-q", "-b", "main", seed], check=True)
        files = {
            ".gitignore": "*.ignored\n",
            "src/codebase.py": "def app(): pass\n",
            "src/obsolete.py": "OLD = 1\n",
            "holon-knowledge/ledger/plans.jsonl": json.dumps(
                {"plan_id": "P-123", "intent_branch": "I-456/_", "entropy": 2.0, "entropy_budget": 5.0}
            )
            + "\n",
            "holon-knowledge/ledger/intents.jsonl": json.dumps({"branch": "I-456/_", "slug": "intent-456"}) + "\n",
        }
        for rel, content in files.items():
            os.makedirs(os.path.dirname(os.path.join(seed, rel)) or seed, exist_ok=True)
            with open(os.path.join(seed, rel), "w") as f:
                f.write(content)
        cls.git(seed, "add", "-A")
        cls.git(seed, "commit", "-q", "-m", "plan")
        cls.git(seed, "push", "-q", bare, f"HEAD:refs/heads/{cls.PLAN}")
        cls.plan_tip = cls.git(seed, "rev-parse", "HEAD").strip()
        finish_template(Path(cls.template))

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sandbox_salvage_test_")
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        copy_template(Path(self.template), Path(self.root))
        self.bare = os.path.join(self.root, "origin.git")
        self.seed = os.path.join(self.root, "seed")
        self.workspace = ""
        self.runs = 0

    @staticmethod
    def git(cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout

    @staticmethod
    def dirty_turn(workspace, path):
        """Edit a tracked file, delete another, create an untracked and an ignored file, and write the notes."""
        with open(os.path.join(workspace, "src/codebase.py"), "a") as f:
            f.write("def added(): pass\n")
        os.remove(os.path.join(workspace, "src/obsolete.py"))
        with open(os.path.join(workspace, "src/new_module.py"), "w") as f:
            f.write("NEW = 1\n")
        with open(os.path.join(workspace, "scratch.ignored"), "w") as f:
            f.write("ignored\n")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(VALID_NOTES)

    def run_main(self, first_turn, *, extra_args=(), breach=None, returncode=0):
        """Run executor.main() in a worktree the host cloned from the bare origin; returns (code, stderr, prompts)."""
        self.runs += 1
        workspace = os.path.join(self.root, f"workspace-{self.runs}")
        # The host fetched every branch of the origin into the worktree before the container started.
        self.git(self.root, "clone", "-q", "--branch", self.PLAN, self.bare, workspace)
        self.workspace = workspace
        prompts = []

        def fake_run_turn(cmd, **kwargs):
            prompts.append(cmd[-1])
            if len(prompts) == 1:
                first_turn(workspace, kwargs["watch_path"])
                return TurnResult(returncode=returncode, breach=breach)
            return TurnResult(returncode=0)

        runner = MagicMock()
        runner.get_version.return_value = "1.0.0"
        runner.build_cmd.side_effect = lambda model, pf, inf, prompt: ["agy", prompt]
        real_time = time.time
        stderr = io.StringIO()
        code = 0
        with (
            patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": workspace}),
            patch.object(executor, "run_turn", side_effect=fake_run_turn),
            patch.object(executor, "get_runner", return_value=runner),
            # Distinct execution ids for several runs in one test.
            patch("time.time", side_effect=lambda: real_time() + 1000 * self.runs),
            patch("sys.argv", ["executor.py", self.PLAN, "antigravity-agent", "gemini-3.5-flash", *extra_args]),
            patch("sys.stderr", stderr),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            try:
                executor.main()
            except SystemExit as exc:
                code = exc.code
        return code, stderr.getvalue(), prompts

    def exec_branches(self):
        """The execution branches the last run committed in its worktree."""
        out = self.git(self.workspace, "for-each-ref", "--format=%(refname:short)", "refs/heads/I-456/P-123/")
        return sorted(b for b in out.split() if "/E-" in b)

    def origin_exec_branches(self):
        out = self.git(self.bare, "for-each-ref", "--format=%(refname:short)", "refs/heads/I-456/P-123/")
        return sorted(b for b in out.split() if "/E-" in b)

    def host_push(self, branch):
        """What the host does after a container: push the branch the container committed."""
        self.git(self.workspace, "push", "-q", self.bare, f"{branch}:refs/heads/{branch}")

    def summary(self, branch):
        rows = read_merged_rows_at_ref(str(self.workspace), branch, "executions.jsonl")
        return [r for r in rows if r.get("entry_type") == "summary"][-1]

    def files_in(self, commit):
        return set(self.git(self.workspace, "show", "--name-only", "--format=", commit).split())

    def subject(self, commit):
        return self.git(self.workspace, "log", "-1", "--format=%s", commit).strip()

    def assert_salvaged(self, branch):
        self.assertIn("failed for plan", self.subject(branch))
        self.assertIn("partial work (failed) for plan", self.subject(f"{branch}~1"))
        self.assertEqual(self.git(self.workspace, "rev-parse", f"{branch}~2").strip(), self.plan_tip)
        partial = self.files_in(f"{branch}~1")
        self.assertEqual(partial, {"src/codebase.py", "src/obsolete.py", "src/new_module.py"})
        failure = self.files_in(branch)
        self.assertIn("holon-knowledge/ledger/pending/executions/I-456.jsonl", failure)
        self.assertTrue(all(f.startswith(("executions/", "holon-knowledge/ledger/")) for f in failure))
        tree = self.git(self.workspace, "ls-tree", "-r", "--name-only", branch).split()
        self.assertNotIn("scratch.ignored", tree)
        self.assertNotIn("src/obsolete.py", tree)
        row = self.summary(branch)
        self.assertEqual(row["status"], "failure")
        self.assertEqual(row["partial_head"], self.git(self.workspace, "rev-parse", f"{branch}~1").strip())

    def test_agent_exit_one_salvages_dirty_tree_into_partial_work_commit(self):
        code, _stderr, _ = self.run_main(self.dirty_turn, returncode=1)
        self.assertEqual(code, 0)
        [branch] = self.exec_branches()
        self.assert_salvaged(branch)

    def test_agent_commits_with_clean_tree_record_the_agent_head(self):
        def committing_agent(workspace, path):
            self.dirty_turn(workspace, path)
            os.remove(os.path.join(workspace, "scratch.ignored"))
            self.git(workspace, "add", "src")
            self.git(workspace, "commit", "-q", "-m", "step 1")

        self.run_main(committing_agent, breach=BudgetBreach("execute", "stage", 2700, 2700))
        [branch] = self.exec_branches()
        self.assertEqual(self.subject(f"{branch}~1"), "step 1")
        author = self.git(self.workspace, "log", "-1", "--format=%ae", f"{branch}~1").strip()
        self.assertEqual(author, executor.EXECUTOR_GIT_EMAIL)
        self.assertEqual(
            self.summary(branch)["partial_head"], self.git(self.workspace, "rev-parse", f"{branch}~1").strip()
        )

    def test_success_path_configures_the_identity_before_the_agent_and_has_one_completed_commit(self):
        seen = {}

        def probe(workspace, path):
            # The mounted worktree's own config is the host's, so the identity goes in the container's global config.
            seen["email"] = self.git(workspace, "config", "--global", "user.email").strip()
            seen["name"] = self.git(workspace, "config", "--global", "user.name").strip()
            seen["local"] = subprocess.run(
                ["git", "config", "--local", "user.email"], cwd=workspace, capture_output=True, text=True
            ).stdout.strip()
            seen["branch"] = self.git(workspace, "rev-parse", "--abbrev-ref", "HEAD").strip()
            self.dirty_turn(workspace, path)

        code, _stderr, _ = self.run_main(probe)
        # The identity was configured on the execution branch before the agent ran.
        self.assertEqual(seen["email"], executor.EXECUTOR_GIT_EMAIL)
        self.assertEqual(seen["name"], executor.EXECUTOR_GIT_NAME)
        self.assertEqual(seen["local"], "")
        self.assertRegex(seen["branch"], r"^I-456/P-123/E-\d+-ANA-G3\.5F/_$")
        # The success path has one completed commit and no partial head.
        self.assertEqual(code, 0)
        [branch] = self.exec_branches()
        log = self.git(self.workspace, "log", "--format=%s", f"{self.plan_tip}..{branch}").splitlines()
        self.assertEqual(len(log), 1)
        self.assertIn("completed for plan", log[0])
        self.assertNotIn("partial_head", self.summary(branch))
        self.assertNotIn("scratch.ignored", self.git(self.workspace, "ls-tree", "-r", "--name-only", branch).split())

    def _reject_pushes(self):
        hook = os.path.join(self.bare, "hooks", "pre-receive")
        with open(hook, "w") as f:
            f.write("#!/bin/sh\necho 'push refused by test hook' >&2\nexit 1\n")
        os.chmod(hook, 0o755)

    def test_failed_run_commits_locally_and_never_pushes(self):
        self._reject_pushes()
        code, stderr, _ = self.run_main(self.dirty_turn, breach=BudgetBreach("execute", "stage", 2700, 2700))
        # The budget breach alone sets the exit status: a rejecting origin is never consulted.
        self.assertEqual(code, 1)
        [branch] = self.exec_branches()
        self.assert_salvaged(branch)
        self.assertEqual(self.origin_exec_branches(), [])
        self.assertNotIn("push refused by test hook", stderr)
        self.assertNotIn("was refused", stderr)

    def test_salvaged_commit_is_kept_when_the_failure_commit_has_nothing_staged(self):
        def code_only(workspace, _path):
            with open(os.path.join(workspace, "src/codebase.py"), "a") as f:
                f.write("def added(): pass\n")

        def failing_append(_path):
            raise OSError("ledger unavailable")

        with patch.object(executor, "atomic_ledger_append", side_effect=failing_append):
            _code, _stderr, _ = self.run_main(code_only, returncode=1)
        [branch] = self.exec_branches()
        self.assertIn("partial work (failed) for plan", self.subject(branch))
        self.assertEqual(self.git(self.workspace, "rev-parse", f"{branch}~1").strip(), self.plan_tip)
        self.assertEqual(self.files_in(branch), {"src/codebase.py"})

    def _failed_branch_with_partial_head(self):
        self.run_main(self.dirty_turn, breach=BudgetBreach("execute", "stage", 2700, 2700))
        [branch] = self.exec_branches()
        # The host pushes it, and fetches it into the worktree of the resuming run.
        self.host_push(branch)
        return branch, self.summary(branch)["partial_head"]

    def test_resume_cuts_the_new_branch_from_a_descending_partial_head(self):
        failed, partial_head = self._failed_branch_with_partial_head()
        seen = {}

        def continue_turn(workspace, path):
            # The partial work is already in the tree: the edit and the deletion survive.
            seen["deleted"] = not os.path.exists(os.path.join(workspace, "src/obsolete.py"))
            with open(os.path.join(workspace, "src/new_module.py"), "a") as f:
                f.write("MORE = 2\n")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(VALID_NOTES)

        code, stderr, prompts = self.run_main(continue_turn, extra_args=(failed,))
        self.assertEqual(seen, {"deleted": True})
        self.assertEqual(code, 0, stderr)
        [new] = [b for b in self.exec_branches() if b != failed]
        self.assertEqual(self.git(self.workspace, "rev-parse", f"{new}~1").strip(), partial_head)
        self.assertIn(executor.continuation_note(failed, partial_head), prompts[0])
        self.assertEqual(self.summary(new)["status"], "success")

    def test_resume_continues_from_an_unverified_diff_failed_row(self):
        """A row recorded as failed (unverified diff) with a partial_head is resumed like a failure row."""
        with patch("sandbox_executor.git.has_effective_diff", side_effect=RuntimeError("diff unavailable")):
            self.run_main(self.dirty_turn, returncode=0)
        [failed] = self.exec_branches()
        self.host_push(failed)
        row = self.summary(failed)
        self.assertEqual((row["status"], row["failure_reason"]), ("failed", "diff_unverified"))
        partial_head = row["partial_head"]

        def continue_turn(workspace, path):
            with open(os.path.join(workspace, "src/new_module.py"), "a") as f:
                f.write("MORE = 2\n")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(VALID_NOTES)

        code, stderr, prompts = self.run_main(continue_turn, extra_args=(failed,))
        self.assertEqual(code, 0, stderr)
        [new] = [b for b in self.exec_branches() if b != failed]
        self.assertEqual(self.git(self.workspace, "rev-parse", f"{new}~1").strip(), partial_head)
        self.assertIn(executor.continuation_note(failed, partial_head), prompts[0])

    def test_nothing_to_salvage_records_no_partial_head_and_its_resume_starts_from_the_plan_tip(self):
        def notes_only(_workspace, path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(VALID_NOTES)

        self.run_main(notes_only, returncode=1)
        [failed] = self.exec_branches()
        # Nothing to salvage: the failure commit sits on the plan tip and records no partial head.
        self.assertNotIn("partial_head", self.summary(failed))
        self.assertEqual(self.git(self.workspace, "rev-parse", f"{failed}~1").strip(), self.plan_tip)
        log = self.git(self.workspace, "log", "--format=%s", f"{self.plan_tip}..{failed}")
        self.assertNotIn("partial work", log)
        self.host_push(failed)
        _code, stderr, prompts = self.run_main(self.dirty_turn, extra_args=(failed,))
        self.assertIn(f"Starting from the plan tip of {self.PLAN}: no partial_head", stderr)
        [new] = [b for b in self.exec_branches() if b != failed]
        self.assertEqual(self.git(self.workspace, "rev-parse", f"{new}~1").strip(), self.plan_tip)
        self.assertNotIn("Continuation:", prompts[0])

    def test_resume_from_a_partial_head_off_the_plan_tip_starts_from_the_plan_tip(self):
        failed, _partial_head = self._failed_branch_with_partial_head()
        # The plan branch moved on, so the old partial head no longer descends from the plan tip.
        with open(os.path.join(self.seed, "src/codebase.py"), "a") as f:
            f.write("# plan revision\n")
        self.git(self.seed, "commit", "-q", "-am", "plan revision")
        self.git(self.seed, "push", "-q", self.bare, f"HEAD:refs/heads/{self.PLAN}")
        new_tip = self.git(self.seed, "rev-parse", "HEAD").strip()
        _code, stderr, _ = self.run_main(self.dirty_turn, extra_args=(failed,))
        self.assertIn(f"Starting from the plan tip of {self.PLAN}: not a descendant of the plan tip", stderr)
        [new] = [b for b in self.exec_branches() if b != failed]
        self.assertEqual(self.git(self.workspace, "rev-parse", f"{new}~1").strip(), new_tip)

    def test_resume_refuses_a_branch_outside_the_plan(self):
        _code, stderr, _ = self.run_main(self.dirty_turn, extra_args=("I-9/P-9/E-1-x-y/_",))
        self.assertIn("is not an execution branch of this plan", stderr)
