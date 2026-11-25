"""The SHA-keyed target probe: unchanged, cached, clean and conflicting targets, against real local repositories."""

from __future__ import annotations

from pathlib import Path

import pytest
from sandbox_executor.flow import target_probe
from sandbox_executor.flow.target_probe import (
    TargetProbeError,
    parse_merge_tree_paths,
    probe_target,
)
from sandbox_executor.git import run_git

from tests.flow.helpers import _git
from tests.ledger_conflict_repos import build_flow_repos, commit_on


@pytest.fixture
def git_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record every git argument list the probe runs, while still running it."""
    calls: list[list[str]] = []

    def spy(args, *rest, **kwargs):
        calls.append(list(args))
        return run_git(args, *rest, **kwargs)

    monkeypatch.setattr(target_probe, "run_git", spy)
    return calls


def _merge_tree_calls(calls: list[list[str]]) -> list[list[str]]:
    return [call for call in calls if call[0] == "merge-tree"]


def test_unchanged_target_is_not_moved_and_runs_no_merge_tree(tmp_path: Path, git_calls):
    _, work = build_flow_repos(tmp_path)

    probe = probe_target(str(work), "main")

    assert probe.moved is False
    assert probe.conflicted_paths == ()
    assert _merge_tree_calls(git_calls) == []


def test_a_probe_of_unchanged_shas_is_returned_cached(tmp_path: Path, git_calls):
    _, work = build_flow_repos(tmp_path)
    commit_on(work, "main", {"other.txt": "x\n"}, "target moves")
    first = probe_target(str(work), "main")
    git_calls.clear()

    second = probe_target(str(work), "main", first)

    assert second.cached is True
    assert (second.head_sha, second.target_sha, second.moved) == (first.head_sha, first.target_sha, True)
    assert _merge_tree_calls(git_calls) == []


def test_a_clean_divergent_target_has_no_conflicted_paths(tmp_path: Path):
    _, work = build_flow_repos(tmp_path)
    commit_on(work, "main", {"target.txt": "t\n"}, "target")
    commit_on(work, "I-1-x/P-2-y/E-3-z/_", {"flow.txt": "f\n"}, "flow")

    probe = probe_target(str(work), "main")

    assert probe.moved is True
    assert probe.conflicted_paths == ()
    assert probe.merge_base


def test_a_conflicting_target_reports_the_exact_paths(tmp_path: Path):
    _, work = build_flow_repos(tmp_path, {"a b.txt": "base\n", "src/c.py": "x = 0\n", "keep.md": "k\n"})
    commit_on(work, "main", {"a b.txt": "main\n", "src/c.py": "x = 1\n"}, "target edits keep.md and foo.py")
    commit_on(work, "I-1-x/P-2-y/E-3-z/_", {"a b.txt": "flow\n", "src/c.py": "x = 2\n"}, "flow edits README.md")

    probe = probe_target(str(work), "main")

    assert probe.conflicted_paths == ("a b.txt", "src/c.py")


def test_parser_stops_at_the_messages_section():
    output = "0123abcd\0src/a.py\0src/a.py\0docs/x.md\0\0" + "1\0src/a.py\0CONFLICT (contents)\0Merge in keep.md\0"

    assert parse_merge_tree_paths(output) == ("src/a.py", "docs/x.md")


def test_a_broken_remote_raises(tmp_path: Path):
    _, work = build_flow_repos(tmp_path)
    _git(work, "remote", "set-url", "origin", str(tmp_path / "missing.git"))

    with pytest.raises(TargetProbeError):
        probe_target(str(work), "main")


def test_an_unresolvable_target_raises_without_fetch(tmp_path: Path):
    _, work = build_flow_repos(tmp_path)

    with pytest.raises(TargetProbeError):
        probe_target(str(work), "no-such-branch", fetch=False)
