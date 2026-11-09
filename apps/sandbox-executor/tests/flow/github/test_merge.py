"""Tests for the guarded child merge: the pure refusal check and ``merge_child_pr`` against a fake ``gh``."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sandbox_executor.flow.github import merge as merge_module
from sandbox_executor.flow.github.merge import (
    lookup_default_branch,
    merge_child_pr,
    merge_refusal_reason,
    run_gh,
)

from tests.flow.fake_gh import FakeGitHub
from tests.flow.helpers import _git

PLAN = "I-1-root/P-2-agent-model/_"
CHILD_EXEC = "I-1-root/P-2-agent-model--1-add-the-parser/P-3-agent-model/E-4-agent-model/_"
FORBIDDEN_FLAGS = ("--squash", "--rebase", "--auto", "--admin", "--delete-branch")


@pytest.mark.parametrize(
    ("base", "expected", "default", "fragment"),
    [
        ("main", PLAN, "main", "'main' is main, master or the repository default"),
        ("refs/heads/master", PLAN, "main", "'master' is main, master"),
        ("origin/trunk", PLAN, "trunk", "'trunk' is main, master or the repository default branch 'trunk'"),
        ("I-9-other/_", PLAN, "main", "is not the expected plan branch"),
        (PLAN, PLAN, None, "default branch could not be determined"),
        (PLAN, PLAN, "  ", "default branch could not be determined"),
        (PLAN, "main", "main", "expected base 'main'"),
        ("", PLAN, "main", "empty"),
    ],
    ids=["main", "master", "default", "unexpected", "lookup-failed", "lookup-blank", "expected-main", "empty"],
)
def test_merge_refusal_reason_refuses(base, expected, default, fragment):
    reason = merge_refusal_reason(base, expected, default)
    assert reason is not None and fragment in reason


@pytest.mark.parametrize("base", [PLAN, f"refs/heads/{PLAN}", f"origin/{PLAN}"])
def test_merge_refusal_reason_allows_the_expected_plan_branch(base):
    assert merge_refusal_reason(base, PLAN, "main") is None


def _commit(work: Path, branch: str, start: str, path: str, text: str) -> None:
    _git(work, "checkout", "-q", "-B", branch, start)
    target = work / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", f"{branch}: {path}")
    _git(work, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Bare origin holding main, the plan branch and a child execution branch cut from it; plus a host clone."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
    work = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True, capture_output=True)
    _git(work, "remote", "add", "origin", str(origin))
    (work / "README.md").write_text("seed\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "seed")
    _git(work, "push", "-q", "origin", "main")
    _commit(work, PLAN, "main", "plans/P-2.md", "# Plan\n")
    _commit(work, CHILD_EXEC, PLAN, "src/parser.py", "PARSER = 1\n")
    host = tmp_path / "host"
    subprocess.run(["git", "clone", "-q", str(origin), str(host)], check=True, capture_output=True)
    return origin, host, work


def _fake(tmp_path: Path, origin: Path, base: str = PLAN) -> tuple[FakeGitHub, int]:
    gh = FakeGitHub(origin, tmp_path)
    return gh, gh.open_pr(CHILD_EXEC, base)


def test_a_pr_whose_base_is_main_is_refused_and_never_merged(repos, tmp_path):
    origin, host, _ = repos
    gh, number = _fake(tmp_path, origin, base="main")
    main_before = gh.sha("main")

    result = merge_child_pr(str(host), number, PLAN, runner=gh)

    assert result.merged is False
    assert "'main' is main, master or the repository default" in (result.reason or "")
    assert gh.merge_calls() == []
    assert gh.sha("main") == main_before


@pytest.mark.parametrize(
    ("base", "default", "fragment"),
    [
        ("master", "main", "'master'"),
        ("develop", "develop", "'develop' is main, master or the repository default"),
        ("I-9-other/_", "main", "not the expected plan branch"),
        (PLAN, None, "could not be determined"),
    ],
    ids=["master", "default-branch", "unexpected-base", "lookup-failed"],
)
def test_unsafe_bases_and_a_failed_lookup_are_refused_before_any_git_write(repos, tmp_path, base, default, fragment):
    origin, host, _ = repos
    gh, number = _fake(tmp_path, origin, base=base)
    gh.default_branch = default
    head_before = gh.sha(CHILD_EXEC)

    result = merge_child_pr(str(host), number, PLAN, runner=gh)

    assert result.merged is False and fragment in (result.reason or "")
    assert gh.merge_calls() == []
    assert gh.sha(CHILD_EXEC) == head_before


@pytest.mark.parametrize(
    "case",
    [
        ("fail", "MERGEABLE", "checks red"),
        ("pending", "MERGEABLE", "checks pending"),
        (None, "MERGEABLE", "checks unmeasured"),
        ("pass", "CONFLICTING", "mergeable=CONFLICTING"),
        ("pass", "UNKNOWN", "mergeable=UNKNOWN"),
    ],
    ids=["red", "pending", "no-checks", "conflicting", "unknown"],
)
def test_red_pending_or_missing_ci_and_an_unmergeable_pr_are_refused(repos, tmp_path, monkeypatch, case):
    bucket, mergeable, fragment = case
    monkeypatch.setattr(merge_module, "MERGE_SETTLE_SECONDS", 0.0)
    origin, host, _ = repos
    gh, number = _fake(tmp_path, origin)
    gh.checks_bucket, gh.mergeable = bucket, mergeable

    result = merge_child_pr(str(host), number, PLAN, runner=gh)

    assert result.merged is False and fragment in (result.reason or "")
    assert gh.merge_calls() == []


def test_a_green_mergeable_pr_is_merged_with_a_merge_commit_only(repos, tmp_path):
    origin, host, _ = repos
    gh, number = _fake(tmp_path, origin)
    head = gh.sha(CHILD_EXEC)

    result = merge_child_pr(str(host), number, PLAN, runner=gh)

    assert result.merged is True and result.already_merged is False
    assert result.merge_commit == gh.sha(PLAN)
    # A two-parent merge commit whose second parent is the child head.
    assert _git(origin, "rev-list", "--parents", "-n", "1", gh.sha(PLAN)).split()[1:] == [
        _git(origin, "rev-parse", f"{gh.sha(PLAN)}^1"),
        head,
    ]
    (call,) = gh.merge_calls()
    assert call == ["pr", "merge", str(number), "--merge", "--match-head-commit", head]
    assert not any(flag in call for flag in FORBIDDEN_FLAGS)


def test_an_already_merged_pr_is_an_idempotent_success(repos, tmp_path):
    origin, host, _ = repos
    gh, number = _fake(tmp_path, origin)
    first = merge_child_pr(str(host), number, PLAN, runner=gh)

    second = merge_child_pr(str(host), number, PLAN, runner=gh)

    assert second.merged is True and second.already_merged is True
    assert second.merge_commit == first.merge_commit
    assert len(gh.merge_calls()) == 1


def test_a_stale_child_gets_the_plan_branch_merged_in_before_it_is_merged(repos, tmp_path):
    origin, host, work = repos
    # The plan branch moved on (an earlier sibling merged) after this child was cut.
    _commit(work, PLAN, f"origin/{PLAN}", "src/sibling.py", "SIBLING = 1\n")
    sibling_tip = _git(origin, "rev-parse", f"refs/heads/{PLAN}")
    gh, number = _fake(tmp_path, origin)

    result = merge_child_pr(str(host), number, PLAN, runner=gh)

    assert result.merged is True
    child_head = gh.sha(CHILD_EXEC)
    assert subprocess.run(["git", "merge-base", "--is-ancestor", sibling_tip, child_head], cwd=origin).returncode == 0
    # The merge was matched against the head that already contains the plan branch.
    assert gh.merge_calls()[0][-1] == child_head


def test_checks_that_register_late_on_the_pushed_child_head_are_waited_for(repos, tmp_path, monkeypatch):
    monkeypatch.setattr(merge_module, "MERGE_POLL_SECONDS", 0.0)
    origin, host, work = repos
    _commit(work, PLAN, f"origin/{PLAN}", "src/sibling.py", "SIBLING = 1\n")
    gh, number = _fake(tmp_path, origin)
    # CI on the head that brought the plan branch in has not registered for the first two polls.
    gh.unregistered_checks_calls = 2

    result = merge_child_pr(str(host), number, PLAN, runner=gh)

    assert result.merged is True
    assert [c[:2] for c in gh.calls].count(["pr", "checks"]) == 3
    assert gh.merge_calls()[0][-1] == gh.sha(CHILD_EXEC)


def test_a_non_ledger_conflict_with_the_plan_branch_is_refused(repos, tmp_path):
    origin, host, work = repos
    _commit(work, PLAN, f"origin/{PLAN}", "src/parser.py", "PARSER = 2\n")
    gh, number = _fake(tmp_path, origin)
    head_before = gh.sha(CHILD_EXEC)

    result = merge_child_pr(str(host), number, PLAN, runner=gh)

    assert result.merged is False and "conflicts outside holon-knowledge/ledger" in (result.reason or "")
    assert gh.merge_calls() == []
    assert gh.sha(CHILD_EXEC) == head_before


def test_a_ledger_only_conflict_with_the_plan_branch_is_interleaved(repos, tmp_path):
    origin, host, work = repos
    ledger = "holon-knowledge/ledger/executions.jsonl"
    _commit(work, PLAN, f"origin/{PLAN}", ledger, '{"id": "plan", "created_at": "2026-11-01T00:00:00Z"}\n')
    _commit(work, CHILD_EXEC, f"origin/{CHILD_EXEC}", ledger, '{"id": "child", "created_at": "2026-11-02T00:00:00Z"}\n')
    gh, number = _fake(tmp_path, origin)

    result = merge_child_pr(str(host), number, PLAN, runner=gh)

    assert result.merged is True
    rows = _git(origin, "show", f"refs/heads/{PLAN}:{ledger}").splitlines()
    assert [r.split('"')[3] for r in rows] == ["plan", "child"]


def test_an_unreadable_pr_and_a_closed_pr_are_refused(repos, tmp_path):
    origin, host, _ = repos
    gh, number = _fake(tmp_path, origin)
    assert "could not be read" in (merge_child_pr(str(host), 999, PLAN, runner=gh).reason or "")
    gh.prs[number]["state"] = "CLOSED"
    assert "is CLOSED" in (merge_child_pr(str(host), number, PLAN, runner=gh).reason or "")
    assert gh.merge_calls() == []


def test_a_pr_already_merged_into_another_base_is_not_a_success(repos, tmp_path):
    origin, host, _ = repos
    gh, number = _fake(tmp_path, origin, base="I-9-other/_")
    gh.prs[number].update(state="MERGED", oid="abc")
    result = merge_child_pr(str(host), number, PLAN, runner=gh)
    assert result.merged is False and "merged into 'I-9-other/_'" in (result.reason or "")


def test_the_default_branch_lookup_fails_closed(tmp_path):
    gh = FakeGitHub(tmp_path, tmp_path, default_branch=None)
    assert lookup_default_branch(str(tmp_path), runner=gh) is None
    gh.default_branch = "trunk"
    assert lookup_default_branch(str(tmp_path), runner=gh) == "trunk"


def test_run_gh_reports_a_missing_binary_as_a_failed_process(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    proc = run_gh(["repo", "view"], str(tmp_path))
    assert proc.returncode == 127
