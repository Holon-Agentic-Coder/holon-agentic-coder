"""Stage 4 check and mergeability resolution for a stacked pull request whose base is a plan branch.

A decomposed plan runs child intents whose pull requests target the plan branch, not ``main``. These tests pin
that the review loop opens the pull request against that branch, evaluates mergeability against it (through ``gh`` and
through the local git fallback, in a repository with no ``main`` at all), waits for CI checks to register and settle,
and records them, without ever naming ``main``. No real ``gh``, network or clock is used.
"""

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sandbox_executor.flow import FlowContext, check_pr_mergeability, probe_ci_checks, run_review_loop
from sandbox_executor.review_target import DiffVerdict

from tests.review_fakes import FakeEnsemble, install_fake_reviewers

PARENT_BRANCH = "I-1700000000-parent/_"
CHILD_EXECUTION_BRANCH = "I-1700000001-child/P-1700000002-a-b/E-1700000003-a-b/_"

_PASS = {"name": "Test - Unit", "bucket": "pass", "state": "SUCCESS", "event": "pull_request", "workflow": "unit"}
_PENDING = {**_PASS, "bucket": "pending", "state": "IN_PROGRESS"}


class FakeGh:
    """A ``gh`` stand-in playing a stacked pull request from creation to green checks; other commands run for real."""

    def __init__(self, checks_replies: list[tuple[int, list[dict[str, Any]]]], real_run: Callable[..., Any]) -> None:
        """Answer ``gh pr checks`` with ``checks_replies`` in order (the last one repeats)."""
        self.calls: list[list[str]] = []
        self.pr_created = False
        self._checks_replies = list(checks_replies)
        self._real_run = real_run

    def __call__(self, cmd: Any, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        """Dispatch ``gh`` commands to the fake and everything else to the real ``subprocess.run``."""
        if not (isinstance(cmd, list) and cmd and cmd[0] == "gh"):
            return self._real_run(cmd, *args, **kwargs)
        self.calls.append(list(cmd))
        returncode, stdout = self._reply(cmd[1:3], cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

    def _reply(self, verb: list[str], cmd: list[str]) -> tuple[int, str]:
        if verb == ["pr", "create"]:
            self.pr_created = True
            return 0, "https://github.com/example/repo/pull/7\n"
        if verb == ["pr", "view"] and not self.pr_created:
            return 1, ""
        if verb == ["pr", "view"] and "mergeable" in cmd[-1]:
            return 0, json.dumps({"number": 7, "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"})
        if verb == ["pr", "view"]:
            return 0, json.dumps({"number": 7, "title": "child"})
        if verb == ["pr", "checks"]:
            returncode, checks = (
                self._checks_replies.pop(0) if len(self._checks_replies) > 1 else self._checks_replies[0]
            )
            return returncode, json.dumps(checks) if returncode == 0 else ""
        raise AssertionError(f"unexpected gh command: {cmd}")


class FakeClock:
    """Monotonic clock that only moves when the fake sleep is called."""

    def __init__(self) -> None:
        """Start at zero with no sleeps recorded."""
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        """Return the current fake time."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Advance the fake time instead of sleeping."""
        self.sleeps.append(seconds)
        self.now += seconds


def _stacked_context(tmp_path: Path) -> FlowContext:
    ctx = FlowContext(
        repo_dir=str(tmp_path),
        plan_branch="I-1700000001-child/P-1700000002-a-b/_",
        execution_branch=CHILD_EXECUTION_BRANCH,
    )
    ctx.intent_data["target_branch"] = PARENT_BRANCH
    return ctx


def _ledger_rows(tmp_path: Path) -> list[dict[str, Any]]:
    ledger = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"
    return [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]


def _with_gh_present(monkeypatch: pytest.MonkeyPatch, fake_gh: FakeGh) -> None:
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "/usr/bin/gh" if name == "gh" else real_which(name))
    monkeypatch.setattr(subprocess, "run", fake_gh)


def test_review_loop_awaits_reads_and_records_checks_of_a_pull_request_into_a_parent_intent_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ensemble = install_fake_reviewers(monkeypatch, FakeEnsemble())
    fake_gh = FakeGh([(1, []), (0, [_PENDING]), (0, [_PASS])], subprocess.run)
    _with_gh_present(monkeypatch, fake_gh)
    clock = FakeClock()
    diff_bases: list[str] = []

    def verified_diff(repo_dir: str, target: str, base_branch: str) -> tuple[DiffVerdict, str]:
        diff_bases.append(base_branch)
        return DiffVerdict.VERIFIED, "1 changed path(s)"

    with (
        patch("sandbox_executor.review_target.verify_review_diff", side_effect=verified_diff),
        patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="abc123"),
        patch(
            "sandbox_executor.flow.review.loop.post_pr_review_consensus",
            return_value={"dual_posted": True, "official_review": {"success": True}},
        ) as post,
    ):
        res = run_review_loop(
            _stacked_context(tmp_path),
            max_iterations=3,
            ci_registration_timeout=60.0,
            ci_poll_interval=5.0,
            clock=clock,
            sleep=clock.sleep,
        )

    create = next(cmd for cmd in fake_gh.calls if cmd[1:3] == ["pr", "create"])
    assert create[create.index("--base") + 1] == PARENT_BRANCH
    assert not [cmd for cmd in fake_gh.calls if "main" in cmd]
    assert diff_bases == [PARENT_BRANCH]
    assert {call["request"].base_ref for call in ensemble.calls} == {f"origin/{PARENT_BRANCH}"}
    checks_calls = [cmd for cmd in fake_gh.calls if cmd[1:3] == ["pr", "checks"]]
    assert len(checks_calls) >= 3, "the registration wait and the pending wait must both poll"
    assert clock.sleeps, "the waits must run on the injected clock"

    assert res["consensus"]["approved"] is True
    assert res["consensus"]["status"] != "not_measured"
    assert res["mergeability"]["merge_state_status"] == "CLEAN"
    assert res["ci_checks"]["source"] == "github_cli"
    assert res["ci_checks"]["all_passed"] is True
    assert post.call_count == 1
    summaries = [row for row in _ledger_rows(tmp_path) if row.get("entry_type") == "summary"]
    assert [row["ci_checks_summary"]["checks"] for row in summaries] == [[_PASS]]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _commit(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", f"edit {name}")


@pytest.fixture
def stacked_repo(tmp_path: Path) -> Path:
    """A repository whose only branches are a parent intent branch and a child stacked on it: no ``main`` exists."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", PARENT_BRANCH)
    _commit(repo, "app.py", "value = 1\n")
    _git(repo, "checkout", "-q", "-b", CHILD_EXECUTION_BRANCH)
    _commit(repo, "app.py", "value = 2\n")
    _git(repo, "checkout", "-q", PARENT_BRANCH)
    branches = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.split()
    assert sorted(branches) == sorted([PARENT_BRANCH, CHILD_EXECUTION_BRANCH])
    return repo


def test_local_mergeability_of_a_stacked_branch_never_needs_main(
    stacked_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sandbox_executor.flow.github.pr.shutil.which", lambda name: None)

    clean = check_pr_mergeability(str(stacked_repo), CHILD_EXECUTION_BRANCH, target_branch=PARENT_BRANCH)
    assert (clean["source"], clean["mergeable"], clean["merge_state_status"]) == ("git_local", "MERGEABLE", "CLEAN")

    _commit(stacked_repo, "app.py", "value = 3\n")
    dirty = check_pr_mergeability(str(stacked_repo), CHILD_EXECUTION_BRANCH, target_branch=PARENT_BRANCH)
    assert (dirty["mergeable"], dirty["merge_state_status"], dirty["is_dirty"]) == ("CONFLICTING", "DIRTY", True)


@pytest.mark.parametrize("base", ["main", PARENT_BRANCH])
def test_ci_probe_summary_does_not_depend_on_the_pull_request_base(
    base: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checks = [{**_PASS, "baseRefName": base}, {**_PENDING, "name": "Test - Hygiene", "baseRefName": base}]
    fake_gh = FakeGh([(0, checks)], subprocess.run)
    _with_gh_present(monkeypatch, fake_gh)

    summary = probe_ci_checks(str(tmp_path), CHILD_EXECUTION_BRANCH)

    assert fake_gh.calls == [
        ["gh", "pr", "checks", CHILD_EXECUTION_BRANCH, "--json", "bucket,name,state,event,workflow,link"]
    ]
    assert {key: summary[key] for key in ("source", "total", "passed", "pending", "failed", "all_passed")} == {
        "source": "github_cli",
        "total": 2,
        "passed": 1,
        "pending": 1,
        "failed": 0,
        "all_passed": False,
    }
