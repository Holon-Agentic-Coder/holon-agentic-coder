"""The fast flow history builder gives what ``build_flow_repos`` and ``commit_on`` give, and copies stay private."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.flow.helpers import _git
from tests.flow_repo_templates import (
    FlowCommit,
    build_flow_history,
    derived_template,
    flow_history_copy,
    replace_with_copy,
    template_name,
)
from tests.ledger_conflict_repos import FLOW_BRANCH, build_flow_repos, commit_on
from tests.repo_templates import shared_template

BASE = {"a.txt": "a\n", "d/b.txt": "b\n"}
COMMITS = (
    FlowCommit("main", {"a.txt": "main\n"}, "target"),
    FlowCommit(FLOW_BRANCH, {"d/b.txt": None, "c.txt": "c\n"}, "flow"),
    FlowCommit("main", {"e.txt": "e\n"}, "target again"),
)


def _state(work: Path) -> dict[str, str]:
    """Everything a flow test can see of a work clone and its origin, with the root path masked."""
    origin = work.parent / "origin.git"
    return {
        "refs": _git(work, "for-each-ref", "--format=%(refname) %(tree) %(upstream)"),
        "origin": _git(origin, "for-each-ref", "--format=%(refname) %(tree)"),
        "origin HEAD": _git(origin, "symbolic-ref", "HEAD"),
        "head": _git(work, "symbolic-ref", "HEAD"),
        "config": _git(work, "config", "--list", "--local").replace(str(work.parent), "ROOT"),
        "status": _git(work, "status", "--porcelain", "--branch"),
        # Each ref's history rather than one graph: the fast build's commits share a timestamp, so the order git
        # log gives to the tips of different branches is not the order the commits were made in.
        **{
            f"log {ref}": _git(work, "log", "--format=%s %an %ae", ref)
            for ref in _git(work, "for-each-ref", "--format=%(refname)").splitlines()
        },
    }


def test_the_history_matches_build_flow_repos_and_commit_on(tmp_path: Path):
    (tmp_path / "fresh").mkdir()
    _, fresh = build_flow_repos(tmp_path / "fresh", BASE)
    fresh_shas = [commit_on(fresh, commit.branch, dict(commit.files), commit.message) for commit in COMMITS]
    (tmp_path / "fast").mkdir()
    fast = build_flow_history(tmp_path / "fast", BASE, COMMITS)

    assert _state(fast.work) == _state(fresh)
    assert [_git(fast.work, "log", "-1", "--format=%s", sha) for sha in fast.shas] == [c.message for c in COMMITS]
    assert len(fast.shas) == len(fresh_shas) and not (tmp_path / "fast" / "fast-import.marks").exists()


def test_bytes_are_committed_as_given(tmp_path: Path):
    fast = build_flow_history(tmp_path, BASE, [FlowCommit(FLOW_BRANCH, {"blob.py": b"x = '\xff\xfe'\n"}, "bytes")])

    assert (fast.work / "blob.py").read_bytes() == b"x = '\xff\xfe'\n"
    assert _git(fast.work, "status", "--porcelain") == ""


def test_a_commit_on_a_branch_origin_lacks_is_refused(tmp_path: Path):
    with pytest.raises(ValueError, match="only main and"):
        build_flow_history(tmp_path, BASE, [FlowCommit("other", {"x": "y"}, "m")])


def test_a_copy_is_private_and_the_template_read_only(tmp_path_factory, tmp_path: Path):
    repos = flow_history_copy(tmp_path_factory, tmp_path / "one", BASE, COMMITS[:1])
    again = flow_history_copy(tmp_path_factory, tmp_path / "two", BASE, COMMITS[:1])

    assert _git(repos.work, "config", "--get", "remote.origin.url") == str(tmp_path / "one" / "origin.git")
    assert repos.shas == again.shas and _git(repos.work, "rev-parse", "origin/main") == repos.shas[0]
    commit_on(repos.work, "main", {"z.txt": "z\n"}, "mine")
    assert _git(again.origin, "rev-parse", "main") == repos.shas[0] != _git(repos.origin, "rev-parse", "main")
    # The template this test copied: shared_template hands back the root it built for the same name.
    name = template_name("flow-history", sorted(BASE.items()), list(COMMITS[:1]), [])
    template = shared_template(tmp_path_factory, name, lambda root: pytest.fail("the template was not built"))[0]
    assert (template / "work" / "a.txt").is_file() and not os.access(template / "work" / "a.txt", os.W_OK)


def test_then_runs_in_the_template_and_its_merge_state_is_copied(tmp_path_factory, tmp_path: Path):
    conflict = (FlowCommit("main", {"a.txt": "1\n"}, "t"), FlowCommit(FLOW_BRANCH, {"a.txt": "2\n"}, "f"))
    repos = flow_history_copy(
        tmp_path_factory, tmp_path, BASE, conflict, then=("merge", "--no-ff", "--no-commit", "origin/main")
    )

    assert _git(repos.work, "diff", "--name-only", "--diff-filter=U") == "a.txt"
    assert _git(repos.work, "rev-parse", "MERGE_HEAD") == repos.shas[0]


def test_a_derived_template_replaces_an_earlier_copy(tmp_path_factory, tmp_path: Path):
    base_template = shared_template(
        tmp_path_factory, "flow-repo-templates-base", lambda root: build_flow_history(root, BASE).shas
    )[0]
    base = flow_history_copy(tmp_path_factory, tmp_path / "base", BASE)

    def scenario(root: Path) -> None:
        commit_on(root / "work", FLOW_BRANCH, {"f.txt": "f\n"}, "derived")

    derived = derived_template(
        tmp_path_factory, "flow-repo-templates-derived", base_template, ["origin.git", "work"], scenario
    )
    replace_with_copy(derived, tmp_path / "base", ["origin.git", "work"])

    assert (base.work / "f.txt").read_text(encoding="utf-8") == "f\n"
    assert _git(base.work, "rev-parse", f"origin/{FLOW_BRANCH}") == _git(base.work, "rev-parse", "HEAD")
    assert _git(base.work, "config", "--get", "remote.origin.url") == str(base.origin)
    assert subprocess.run(["git", "status", "--porcelain"], cwd=base.work, capture_output=True, text=True).stdout == ""
