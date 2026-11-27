"""Characterisation of `executor.main`: the ledger row, the messages and the exit code of a whole run.

Pins today's observable behaviour of the Stage 3 entry point, so a refactor of `main` can be shown not to change the
`executions.jsonl` summary row (keys, key order and values), the stdout and stderr lines, or the exit status. The run
drives a real git worktree cloned from a temporary origin; only the agent turn and the runner are faked. Volatile
values (execution ids, commit SHAs, temporary paths, timestamps and elapsed times) are normalised, each by a named
pattern, and nothing else is.
"""

import contextlib
import io
import json
import logging
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.budgets import BudgetBreach
from sandbox_executor.cli import PACKAGE_LOGGER_NAME, _CurrentStderrHandler
from sandbox_executor.entrypoint import executor
from sandbox_executor.ledger import read_merged_rows_at_ref
from sandbox_executor.turns import TurnResult

PLAN = "I-456/P-123/_"

NOTES = """# Execution Record: E-test

## Summary

Implemented the plan. Details of the change. Details of the change. Details of the change.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 1     |
| entropy_actual        | 1.0   |
| impact_actual         | 8.0   |
| cost_actual           | 2.0   |
| learning_value_actual | 2.0   |

## Steps Executed

- Step 1 completed.

## Tests Run

- `uv run pytest` passed.
"""

_EXEC_ID_RE = re.compile(r"E-\d+-ANA-G3\.5F")
_SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(?:\+00:00|Z)")
_STAGE_MINUTES_RE = re.compile(r"\(\d+\.\d min /")
_PROMPT_FILE_RE = re.compile(r"exec_(prompt|intent)-\d+")


def _normalise(text: str, tmp_root: str) -> str:
    """Replace the volatile parts of a message or row value with stable placeholders."""
    text = text.replace(tmp_root, "<tmp>")
    text = _EXEC_ID_RE.sub("E-<id>", text)
    text = _SHA_RE.sub("<sha>", text)
    text = _TIMESTAMP_RE.sub("<timestamp>", text)
    text = _PROMPT_FILE_RE.sub(r"exec_\1-<seq>", text)
    return _STAGE_MINUTES_RE.sub("(<n> min /", text)


def _git(cwd: str, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class _Run:
    """One `executor.main()` run in a host worktree cloned from a temporary origin."""

    def __init__(self, root: Path) -> None:
        self.root = str(root)
        bare = os.path.join(self.root, "origin.git")
        seed = os.path.join(self.root, "seed")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", bare], check=True)
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
            os.makedirs(os.path.dirname(os.path.join(seed, rel)), exist_ok=True)
            with open(os.path.join(seed, rel), "w") as f:
                f.write(content)
        _git(seed, "add", "-A")
        _git(seed, "commit", "-q", "-m", "plan")
        _git(seed, "push", "-q", bare, f"HEAD:refs/heads/{PLAN}")
        self.plan_tip = _git(seed, "rev-parse", "HEAD").strip()
        self.workspace = os.path.join(self.root, "workspace")
        _git(self.root, "clone", "-q", "--branch", PLAN, bare, self.workspace)

    def main(
        self, first_turn: Callable[[str, str], None], *, breach: BudgetBreach | None = None, returncode: int = 0
    ) -> tuple[int | str | None, list[str], list[str]]:
        """Run `main`; returns (exit code or None when it returned, stdout lines, stderr lines), all normalised."""
        turns = []

        def fake_run_turn(cmd: list[str], **kwargs: object) -> TurnResult:
            turns.append(cmd)
            if len(turns) == 1:
                first_turn(self.workspace, str(kwargs["watch_path"]))
                return TurnResult(returncode=returncode, stdout="agent says hi\n", breach=breach)
            return TurnResult(returncode=0)

        runner = MagicMock()
        runner.get_version.return_value = "1.0.0"
        runner.uses_stdin = False
        runner.build_cmd.side_effect = lambda model, pf, inf, prompt: ["agy", prompt]
        stdout, stderr = io.StringIO(), io.StringIO()
        code: int | str | None = None
        # An earlier test in the same process may have run `configure_cli_logging`, whose handler would put log records
        # on the captured stderr; the pins cover `main`'s own messages, so detach that handler for the run.
        package_logger = logging.getLogger(PACKAGE_LOGGER_NAME)
        handlers = [h for h in package_logger.handlers if not isinstance(h, _CurrentStderrHandler)]
        with (
            patch.object(package_logger, "handlers", handlers),
            patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": self.workspace}),
            patch.object(executor, "workspace_mounted", return_value=True),
            patch.object(executor, "run_turn", side_effect=fake_run_turn),
            patch.object(executor, "get_runner", return_value=runner),
            patch("sys.argv", ["executor.py", PLAN, "antigravity-agent", "gemini-3.5-flash"]),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            try:
                executor.main()
            except SystemExit as exc:
                code = exc.code
        return (
            code,
            # Split on newlines only: `str.splitlines` would also split on the unit separator in a git format string.
            _normalise(stdout.getvalue(), self.root).split("\n"),
            _normalise(stderr.getvalue(), self.root).split("\n"),
        )

    def exec_branch(self) -> str:
        out = _git(self.workspace, "for-each-ref", "--format=%(refname:short)", "refs/heads/I-456/P-123/")
        [branch] = [b for b in out.split() if "/E-" in b]
        return branch

    def summary_row(self) -> dict:
        rows = read_merged_rows_at_ref(str(self.workspace), self.exec_branch(), "executions.jsonl")
        return [row for row in rows if row.get("entry_type") == "summary"][-1]

    def subjects(self) -> list[str]:
        log = _git(self.workspace, "log", "--format=%s", f"{self.plan_tip}..{self.exec_branch()}")
        return [_normalise(line, self.root) for line in log.splitlines()]


def _normalised_row(row: dict, root: str) -> dict:
    """The row with every string value normalised and the measured elapsed time replaced by a marker."""
    out = {k: _normalise(v, root) if isinstance(v, str) else v for k, v in row.items()}
    if isinstance(out.get("elapsed_seconds"), float):
        out["elapsed_seconds"] = "<elapsed>"
    return out


def _dirty_turn(workspace: str, notes_path: str) -> None:
    """Edit a tracked file, delete another, create an untracked and an ignored file, and write the notes."""
    with open(os.path.join(workspace, "src/codebase.py"), "a") as f:
        f.write("def added(): pass\n")
    os.remove(os.path.join(workspace, "src/obsolete.py"))
    with open(os.path.join(workspace, "src/new_module.py"), "w") as f:
        f.write("NEW = 1\n")
    with open(os.path.join(workspace, "scratch.ignored"), "w") as f:
        f.write("ignored\n")
    os.makedirs(os.path.dirname(notes_path), exist_ok=True)
    with open(notes_path, "w") as f:
        f.write(NOTES)


ROW_KEYS = [
    "entry_type",
    "execution_id",
    "plan_branch",
    "agent",
    "agent_version",
    "model",
    "status",
    "summary",
    "created_at",
    "agent_output_truncated",
    "agent_output_bytes",
    "ledger_revision",
    "execution_notes",
    "execution_notes_valid",
    "elapsed_seconds",
    "budget_seconds",
]


def test_success_run_pins_the_row_messages_commit_and_exit(tmp_path: Path) -> None:
    run = _Run(tmp_path)
    code, out, err = run.main(_dirty_turn)

    assert code is None
    row = run.summary_row()
    assert list(row) == ROW_KEYS
    assert _normalised_row(row, run.root) == SUCCESS_ROW
    assert run.subjects() == ["execute: E-<id> completed for plan I-456/P-123/_"]
    assert out == SUCCESS_STDOUT
    assert err == SUCCESS_STDERR


def test_budget_breach_salvages_partial_work_and_exits_1(tmp_path: Path) -> None:
    run = _Run(tmp_path)
    code, out, err = run.main(_dirty_turn, breach=BudgetBreach("execute", "stage", 2700, 2700))

    assert code == 1
    row = run.summary_row()
    assert list(row) == [*ROW_KEYS, "partial_head"]
    assert row["partial_head"] == _git(run.workspace, "rev-parse", f"{run.exec_branch()}~1").strip()
    assert _normalised_row(row, run.root) == SALVAGE_ROW
    assert run.subjects() == [
        "execute: E-<id> failed for plan I-456/P-123/_",
        "execute: E-<id> partial work (failed) for plan I-456/P-123/_",
    ]
    assert out == SALVAGE_STDOUT
    assert err == SALVAGE_STDERR


def test_no_arguments_prints_the_usage_line_and_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("sys.argv", ["executor.py"]), pytest.raises(SystemExit) as exc:
        executor.main()

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == "Usage: executor.py <plan_branch> [agent_name] [model_name] [partial_execution_branch]\n"
    assert captured.err == ""


def test_unmounted_workspace_is_an_error_and_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    runner = MagicMock()
    with (
        patch("sys.argv", ["executor.py", PLAN]),
        patch.object(executor, "get_runner", return_value=runner),
        patch.object(executor, "workspace_mounted", return_value=False),
        pytest.raises(SystemExit) as exc,
    ):
        executor.main()

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Error: the host worktree is not mounted at the sandbox workspace; the executor never clones or fetches.\n"
    )
    runner.validate.assert_called_once_with()
    runner.cleanup_ephemeral_credentials.assert_not_called()


_COMMON_ROW = {
    "entry_type": "summary",
    "execution_id": "E-<id>",
    "plan_branch": "I-456/P-123/_",
    "agent": "antigravity-agent",
    "agent_version": "1.0.0",
    "model": "gemini-3.5-flash",
    "created_at": "<timestamp>",
    "agent_output_truncated": False,
    "agent_output_bytes": 14,
    "ledger_revision": 1,
    "execution_notes": "executions/E-<id>.md",
    "execution_notes_valid": True,
    "elapsed_seconds": "<elapsed>",
    "budget_seconds": 2700.0,
}

SUCCESS_ROW = {**_COMMON_ROW, "status": "success", "summary": "Plan executed successfully"}

SALVAGE_ROW = {
    **_COMMON_ROW,
    "status": "failure",
    "summary": (
        "Plan execution stopped: execute stage budget of 2700 s exceeded after 2700.0 s; the implementation turn "
        "did not finish (partial code and execution notes salvaged)"
    ),
    "partial_head": "<sha>",
}

_BRANCH_START_STDOUT = [
    "Running: git config --global --add safe.directory <tmp>/workspace",
    "Running: git rev-parse --verify HEAD^{commit}",
    "Running: git status --porcelain -- . :(exclude)holon-knowledge/ledger/intents.jsonl "
    ":(exclude)holon-knowledge/ledger/plans.jsonl :(exclude)holon-knowledge/ledger/executions.jsonl "
    ":(exclude)holon-knowledge/ledger/pending :(exclude)executions/E-*.md",
    "Running: git rev-parse --verify refs/remotes/origin/I-456/P-123/_^{commit}",
    "Running: git for-each-ref --format=%(refname) refs/heads/I-456/P-123/",
    "Running: git for-each-ref --format=%(refname) refs/remotes/origin/I-456/P-123/",
    "Running: git checkout -b I-456/P-123/E-<id>/_ <sha>",
    "Running: git config --global user.email executor-agent@holon-agentic-coder.com",
    "Running: git config --global user.name Holon Executor Agent",
    "Executing plan I-456/P-123/_ using agent antigravity-agent...",
    "Running: git rev-parse --is-inside-work-tree",
    "Running: git rev-parse --verify HEAD",
]

_IDENTITY_STDOUT = [
    "Running: git config --global user.email executor-agent@holon-agentic-coder.com",
    "Running: git config --global user.name Holon Executor Agent",
]

_RECORD_STDOUT = [
    "Running: git rev-parse --verify HEAD^",
    "Running: git log --no-merges --format=\x1e%h\x1f%B <sha>..HEAD",
    "Running: git rev-parse --verify origin/main",
    "Execution branch 'I-456/P-123/E-<id>/_' successfully committed locally; the host pushes it.",
]

SUCCESS_STDOUT = [
    *_BRANCH_START_STDOUT,
    "Running: git diff --name-only --no-renames -z <sha> --",
    "Running: git ls-files --others --exclude-standard -z",
    "Running: git status --porcelain",
    "Running: git add holon-knowledge/ledger/pending/executions",
    "Running: git add -A",
    "Running: git status --short",
    "Current git repository status:",
    "A  executions/E-<id>.md",
    "A  holon-knowledge/ledger/pending/executions/I-456.jsonl",
    "M  src/codebase.py",
    "A  src/new_module.py",
    "D  src/obsolete.py",
    "",
    "Running: git diff --cached --quiet",
    *_IDENTITY_STDOUT,
    "Running: git commit -m execute: E-<id> completed for plan I-456/P-123/_",
    *_RECORD_STDOUT[:2],
    "Notice: scripts/check_self_explaining.py not found; the pre-push self-explaining check was skipped.",
    *_RECORD_STDOUT[2:],
    "execute: ok (<n> min / 45 min)",
    "",
]

SUCCESS_STDERR = [""]

SALVAGE_STDOUT = [
    *_BRANCH_START_STDOUT,
    "Running: git status --porcelain",
    "Running: git add -A -- . :(exclude)holon-knowledge/ledger/executions.jsonl "
    ":(exclude)holon-knowledge/ledger/pending :(exclude)executions/E-<id>.md",
    "Running: git diff --cached --quiet",
    *_IDENTITY_STDOUT,
    "Running: git commit -m execute: E-<id> partial work (failed) for plan I-456/P-123/_",
    "Running: git rev-parse HEAD",
    "Running: git add holon-knowledge/ledger/pending/executions executions/E-<id>.md",
    "Running: git status --short",
    "Current git repository status:",
    "A  executions/E-<id>.md",
    "A  holon-knowledge/ledger/pending/executions/I-456.jsonl",
    "",
    "Running: git diff --cached --quiet",
    *_IDENTITY_STDOUT,
    "Running: git commit -m execute: E-<id> failed for plan I-456/P-123/_",
    *_RECORD_STDOUT,
    "execute: failed (<n> min / 45 min)",
    "",
]

SALVAGE_STDERR = [""]
