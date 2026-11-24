"""Tests that the flow's ledger step never leaves its temporary worktree registered and commits and pushes as before."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.flow import FlowContext, git_refs
from sandbox_executor.git import run_git as real_run_git

from tests.flow.helpers import _git, _ledger_rows

BRANCH = "I-1-x/P-2-y/E-3-z/_"
LEDGER_REL = "holon-knowledge/ledger/executions.jsonl"
EARLIER_ROW: dict[str, Any] = {"entry_type": "summary", "execution_id": "E-3-z", "plan_branch": "I-1-x/P-2-y/_"}
NEW_ROW: dict[str, Any] = {**EARLIER_ROW, "status": "success", "ledger_revision": 2}
MESSAGE = "execute: record success row for E-3-z"


@dataclass(frozen=True)
class LedgerRepos:
    """The repositories of one ledger-step test and the temp directory its ledger worktrees are created in."""

    host: Path
    origin: Path
    context: FlowContext
    temp_dir: Path

    def append(self, row: dict[str, Any] = NEW_ROW) -> str | None:
        """Run the ledger step for ``row`` on the execution branch."""
        return git_refs._append_ledger_row(self.context, BRANCH, "executions.jsonl", row, MESSAGE)

    def origin_tip(self) -> str:
        """The commit origin's execution branch points at."""
        return _git(self.origin, "rev-parse", f"refs/heads/{BRANCH}")


@pytest.fixture
def ledger_repos(flow_repos, tmp_path, monkeypatch) -> LedgerRepos:
    """An execution-shaped branch with one ledger row on origin, fetched by the host, and a hermetic temp directory."""
    host, origin = flow_repos
    _git(host, "checkout", "-q", "-b", BRANCH)
    (host / LEDGER_REL).write_text(json.dumps(EARLIER_ROW) + "\n", encoding="utf-8")
    _git(host, "commit", "-qam", "execute: E-3-z completed")
    _git(host, "push", "-q", "origin", BRANCH)
    _git(host, "checkout", "-q", "main")
    _git(host, "fetch", "-q", "origin")
    temp_dir = tmp_path / "ledger-tmp"
    temp_dir.mkdir()
    # Ledger worktrees of this test land here and nowhere else, so leftovers can be listed under xdist.
    monkeypatch.setattr(tempfile, "tempdir", str(temp_dir))
    return LedgerRepos(host, origin, FlowContext(repo_dir=str(host), token_reduce=False), temp_dir)


class RecordingGit:
    """Stands in for ``git_refs.run_git``: records each call's arguments and options, then runs the real command."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(args), dict(kwargs)))
        return real_run_git(args, **kwargs)


def _registered_worktrees(repo: Path) -> list[str]:
    """The paths ``git worktree list`` reports for ``repo``, the main worktree first."""
    listing = _git(repo, "worktree", "list", "--porcelain")
    return [line.removeprefix("worktree ") for line in listing.splitlines() if line.startswith("worktree ")]


def _ledger_worktrees(repo: Path) -> list[str]:
    """The registered worktrees of ``repo`` that sit in a ``holon-flow-ledger-*`` directory."""
    return [path for path in _registered_worktrees(repo) if "holon-flow-ledger-" in path]


def _ledger_temp_entries(temp_dir: Path) -> list[Path]:
    """The ``holon-flow-ledger-*`` entries in the test's temp directory."""
    return sorted(temp_dir.glob("holon-flow-ledger-*"))


def _show(repo: Path, spec: str) -> str:
    """The exact output of ``git show <spec>`` (the shared ``_git`` helper strips it)."""
    return subprocess.run(["git", "show", spec], cwd=repo, check=True, capture_output=True, text=True).stdout


def test_a_successful_ledger_step_leaves_no_worktree_and_no_temp_directory(ledger_repos):
    assert ledger_repos.append() is None

    assert _registered_worktrees(ledger_repos.host) == [os.path.realpath(ledger_repos.host)]
    assert _ledger_temp_entries(ledger_repos.temp_dir) == []


def test_a_successful_ledger_step_makes_the_same_commit_and_push(ledger_repos):
    host, origin = ledger_repos.host, ledger_repos.origin
    previous_tip = ledger_repos.origin_tip()
    previous_content = _show(origin, f"{previous_tip}:{LEDGER_REL}")

    assert ledger_repos.append() is None

    tip = ledger_repos.origin_tip()
    identity = {**git_refs.EXECUTOR_GIT_ENV, **os.environ}
    assert _git(origin, "log", "-1", "--format=%P%n%s%n%an%n%ae%n%cn%n%ce", tip).splitlines() == [
        previous_tip,
        MESSAGE,
        identity["GIT_AUTHOR_NAME"],
        identity["GIT_AUTHOR_EMAIL"],
        identity["GIT_COMMITTER_NAME"],
        identity["GIT_COMMITTER_EMAIL"],
    ]
    assert _git(origin, "diff", "--name-only", previous_tip, tip).splitlines() == [LEDGER_REL]
    assert _show(origin, f"{tip}:{LEDGER_REL}") == previous_content + json.dumps(NEW_ROW) + "\n"
    assert _ledger_rows(origin, tip, "executions.jsonl") == [EARLIER_ROW, NEW_ROW]
    # The host's view of origin and its local branch follow the pushed commit.
    assert _git(host, "rev-parse", f"refs/remotes/origin/{BRANCH}") == tip
    assert _git(host, "rev-parse", f"refs/heads/{BRANCH}") == tip


def test_a_successful_ledger_step_runs_the_same_git_commands_in_its_worktree(ledger_repos, monkeypatch):
    git = RecordingGit()
    monkeypatch.setattr("sandbox_executor.flow.git_refs.run_git", git)

    assert ledger_repos.append() is None

    # Other ``git worktree`` administration (listing, removing, pruning) is not part of this contract.
    adds = [(args, options) for args, options in git.calls if args[:2] == ["worktree", "add"]]
    assert len(adds) == 1
    add_args, add_options = adds[0]
    worktree = add_args[3]
    assert add_args == ["worktree", "add", "--detach", worktree, f"refs/remotes/origin/{BRANCH}"]
    assert add_options == {"cwd": str(ledger_repos.host)}
    tmp_root = os.path.dirname(worktree)
    assert os.path.basename(worktree) == "worktree"
    assert os.path.basename(tmp_root).startswith("holon-flow-ledger-")
    assert os.path.dirname(tmp_root) == str(ledger_repos.temp_dir)

    in_worktree = [(args, options) for args, options in git.calls if options.get("cwd") == worktree]
    commit_env = {**git_refs.EXECUTOR_GIT_ENV, **os.environ, "DOCKER_CLI_HINTS": "false"}
    assert in_worktree == [
        (["add", LEDGER_REL], {"cwd": worktree}),
        (["commit", "-m", MESSAGE], {"cwd": worktree, "env": commit_env}),
        (["rev-parse", "HEAD"], {"cwd": worktree}),
        (["push", "origin", f"HEAD:refs/heads/{BRANCH}"], {"cwd": worktree, "timeout": 300}),
    ]
