"""Hermetic tests verifying Stage 3 partial-work and completion commits exclude proxy wire logs and cache databases."""

from __future__ import annotations

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
from sandbox_executor.flow import FlowContext
from sandbox_executor.flow.partial_salvage import leftover_paths, salvage_leftovers
from sandbox_executor.turns import TurnResult

from tests.executor_fixtures import (
    VALID_NOTES,
    _agent_turns_use_run_cmd,  # noqa: F401  (autouse fixture)
    _workspace_is_mounted,  # noqa: F401  (autouse fixture)
)
from tests.test_repository_hygiene import LOCAL_NOTES_DIR


def _write_scratch_tree(workspace: str | Path) -> list[str]:
    """Create untracked wire logs and a cache SQLite database inside ``workspace`` and return their relative paths."""
    root = Path(workspace)
    legacy_wire = root / LOCAL_NOTES_DIR / "mitm_wire_logs" / "wire_log_001.json"
    legacy_cache = root / LOCAL_NOTES_DIR / "cache" / "coherence.db"
    top_wire = root / "mitm_wire_logs" / "wire_log_002.json"
    top_cache = root / "cache" / "cache.sqlite3"
    for target, payload in (
        (legacy_wire, '{"request": "full prompt", "response": "full completion"}\n'),
        (legacy_cache, "sqlite-payload\n"),
        (top_wire, '{"request": "second prompt"}\n'),
        (top_cache, "sqlite3-payload\n"),
    ):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")
    return [
        f"{LOCAL_NOTES_DIR}/mitm_wire_logs/wire_log_001.json",
        f"{LOCAL_NOTES_DIR}/cache/coherence.db",
        "mitm_wire_logs/wire_log_002.json",
        "cache/cache.sqlite3",
    ]


class TestExecutionCommitExcludesScratch(unittest.TestCase):
    """Stage 3 partial-work and completion commits exclude wire logs, cache directories and cache databases."""

    PLAN = "I-456/P-123/_"

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="sandbox_scratch_exclude_")
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.bare = os.path.join(self.root, "origin.git")
        self.seed = os.path.join(self.root, "seed")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.bare], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", self.seed], check=True)
        for key, val in (("user.email", "t@holon.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
            self.git(self.seed, "config", key, val)
        files = {
            "src/codebase.py": "def app(): pass\n",
            "holon-knowledge/ledger/plans.jsonl": json.dumps(
                {"plan_id": "P-123", "intent_branch": "I-456/_", "entropy": 2.0, "entropy_budget": 5.0}
            )
            + "\n",
            "holon-knowledge/ledger/intents.jsonl": json.dumps({"branch": "I-456/_", "slug": "intent-456"}) + "\n",
        }
        for rel, content in files.items():
            full = os.path.join(self.seed, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)
        self.git(self.seed, "add", "-A")
        self.git(self.seed, "commit", "-q", "-m", "plan")
        self.git(self.seed, "push", "-q", self.bare, f"HEAD:refs/heads/{self.PLAN}")
        self.plan_tip = self.git(self.seed, "rev-parse", "HEAD").strip()
        self.runs = 0
        self.workspace = ""

    @staticmethod
    def git(cwd: str, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout

    def run_executor(self, first_turn, *, breach=None, returncode: int = 0) -> tuple[int, str, str]:
        self.runs += 1
        workspace = os.path.join(self.root, f"workspace-{self.runs}")
        self.git(self.root, "clone", "-q", "--branch", self.PLAN, self.bare, workspace)
        self.workspace = workspace

        def fake_run_turn(_cmd, **kwargs):
            first_turn(workspace, kwargs["watch_path"])
            return TurnResult(returncode=returncode, breach=breach)

        runner = MagicMock()
        runner.get_version.return_value = "1.0.0"
        runner.build_cmd.side_effect = lambda model, pf, inf, prompt: ["agy", prompt]
        real_time = time.time
        stdout, stderr = io.StringIO(), io.StringIO()
        code = 0
        with (
            patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": workspace}),
            patch.object(executor, "run_turn", side_effect=fake_run_turn),
            patch.object(executor, "get_runner", return_value=runner),
            patch("time.time", side_effect=lambda: real_time() + 1000 * self.runs),
            patch("sys.argv", ["executor.py", self.PLAN, "antigravity-agent", "gemini-3.5-flash"]),
            patch("sys.stderr", stderr),
            contextlib.redirect_stdout(stdout),
        ):
            try:
                executor.main()
            except SystemExit as exc:
                code = exc.code
        return code, stdout.getvalue(), stderr.getvalue()

    def exec_branch(self) -> str:
        out = self.git(self.workspace, "for-each-ref", "--format=%(refname:short)", "refs/heads/I-456/P-123/")
        branches = sorted(b for b in out.split() if "/E-" in b)
        self.assertEqual(len(branches), 1)
        return branches[0]

    def test_completion_commit_excludes_untracked_sidecar_scratch_and_logs_skipped_paths(self) -> None:
        scratch_paths: list[str] = []

        def turn(workspace: str, notes_path: str) -> None:
            with open(os.path.join(workspace, "src/codebase.py"), "a", encoding="utf-8") as f:
                f.write("def feature(): return 42\n")
            scratch_paths.extend(_write_scratch_tree(workspace))
            os.makedirs(os.path.dirname(notes_path), exist_ok=True)
            with open(notes_path, "w", encoding="utf-8") as f:
                f.write(VALID_NOTES)

        code, _stdout, stderr = self.run_executor(turn, returncode=0)
        self.assertEqual(code, 0, stderr)
        branch = self.exec_branch()
        committed_tree = set(self.git(self.workspace, "ls-tree", "-r", "--name-only", branch).splitlines())
        self.assertIn("src/codebase.py", committed_tree)
        for scratch in scratch_paths:
            self.assertNotIn(scratch, committed_tree)
            self.assertIn(scratch, stderr)
        self.assertIn("Skipping sidecar scratch path(s) from Stage 3 commit:", stderr)

    def test_completion_commit_does_not_readd_scratch_after_agent_removed_earlier_commit(self) -> None:
        """If the sidecar writes new untracked scratch files after an agent step commit, completion never stages them."""
        scratch_paths: list[str] = []

        def turn(workspace: str, notes_path: str) -> None:
            with open(os.path.join(workspace, "src/codebase.py"), "a", encoding="utf-8") as f:
                f.write("def step_one(): return 1\n")
            self.git(workspace, "add", "src/codebase.py")
            self.git(workspace, "commit", "-q", "-m", "Step 1")
            scratch_paths.extend(_write_scratch_tree(workspace))
            os.makedirs(os.path.dirname(notes_path), exist_ok=True)
            with open(notes_path, "w", encoding="utf-8") as f:
                f.write(VALID_NOTES)

        code, _stdout, stderr = self.run_executor(turn, returncode=0)
        self.assertEqual(code, 0, stderr)
        branch = self.exec_branch()
        committed_tree = set(self.git(self.workspace, "ls-tree", "-r", "--name-only", branch).splitlines())
        for scratch in scratch_paths:
            self.assertNotIn(scratch, committed_tree)
            self.assertIn(scratch, stderr)

    def test_failed_run_partial_work_commit_excludes_sidecar_scratch_and_logs_skipped_paths(self) -> None:
        scratch_paths: list[str] = []

        def turn(workspace: str, notes_path: str) -> None:
            with open(os.path.join(workspace, "src/codebase.py"), "a", encoding="utf-8") as f:
                f.write("def partial_feature(): return 0\n")
            scratch_paths.extend(_write_scratch_tree(workspace))
            os.makedirs(os.path.dirname(notes_path), exist_ok=True)
            with open(notes_path, "w", encoding="utf-8") as f:
                f.write(VALID_NOTES)

        code, _stdout, stderr = self.run_executor(
            turn, breach=BudgetBreach("execute", "stage", 2700, 2700), returncode=-15
        )
        self.assertEqual(code, 1)
        branch = self.exec_branch()
        partial_commit = f"{branch}~1"
        partial_files = set(self.git(self.workspace, "show", "--name-only", "--format=", partial_commit).splitlines())
        self.assertEqual(partial_files, {"src/codebase.py"})
        committed_tree = set(self.git(self.workspace, "ls-tree", "-r", "--name-only", branch).splitlines())
        for scratch in scratch_paths:
            self.assertNotIn(scratch, committed_tree)
            self.assertIn(scratch, stderr)
        self.assertIn("Skipping sidecar scratch path(s) from Stage 3 commit:", stderr)

    def test_host_interrupted_salvage_with_only_scratch_files_makes_no_commit_and_logs_skipped(self) -> None:
        workspace = os.path.join(self.root, "workspace-host-salvage")
        self.git(self.root, "clone", "-q", "--branch", self.PLAN, self.bare, workspace)
        for key, val in (("user.email", "t@holon.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
            self.git(workspace, "config", key, val)
        exec_branch = "I-456/P-123/E-999-ANA-G4A/_"
        self.git(workspace, "checkout", "-q", "-b", exec_branch)
        head_before = self.git(workspace, "rev-parse", "HEAD").strip()

        scratch_paths = _write_scratch_tree(workspace)
        self.assertEqual(leftover_paths(workspace), [])

        ctx = FlowContext(
            repo_dir=workspace,
            plan_branch=self.PLAN,
            intent_data={"slug": "intent-456"},
            agent="antigravity-agent",
            model="gemini-4-argon",
        )
        result = salvage_leftovers(ctx, "I-456/P-123")
        self.assertIsNone(result.sha)
        self.assertEqual(self.git(workspace, "rev-parse", "HEAD").strip(), head_before)
        joined_logs = "\n".join(ctx.logs)
        for scratch in scratch_paths:
            self.assertIn(scratch, joined_logs)
