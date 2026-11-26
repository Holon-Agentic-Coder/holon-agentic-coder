"""The empty-diff guard compares a combined (decomposed) execution branch with its merge base on the target branch."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.flow import _execution_effective_diff
from sandbox_executor.review_target import DiffVerdict, classify_review_diff

from tests.flow_repo_templates import GraphCommit, fast_import, template_name
from tests.repo_templates import copy_template, shared_template

PLAN = "I-1-guard/P-1-claude-agent-m/_"
CHILD = "I-1-guard/P-1-claude-agent-m/I-2-part/_"
EXEC = "I-1-guard/P-1-claude-agent-m/E-2-claude-agent-m/_"
LEDGER = "holon-knowledge/ledger/executions.jsonl"
EMPTY_PREFIX = "Execution produced no changes outside holon-knowledge/ledger/, plans/ and executions/ relative to "


def _git(cwd: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return res.stdout.strip()


def _commit_files(repo: Path, files: dict[str, str], msg: str) -> None:
    for rel, content in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)


def _integrated_row(plan_branch: str = PLAN, children: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "entry_type": "summary",
        "execution_id": "E-2-claude-agent-m",
        "status": "decomposed",
        "integrated": True,
        "plan_branch": plan_branch,
        "children": [{"slug": "part", "intent_branch": CHILD}] if children is None else children,
    }


def _build_into(
    tmp_path: Path, child_files: dict[str, str] | None, row: dict[str, Any] | None, squash: bool = False
) -> None:
    """Push main, the plan branch, a child merged into it and the execution branch to a bare origin from ``seed``.

    The execution branch forks off the plan branch before the child merges, then (when ``child_files`` is given)
    integrates the plan branch with a merge commit. ``row`` is appended to the execution ledger when given; otherwise
    the execution commits a plain ledger row. main advances after the plan forks, so the merge base is not its tip.
    All commits are written by one ``git fast-import``; ``host`` is an unfetched clone (an origin remote only).
    """
    seed = tmp_path / "seed"
    _git(tmp_path, "init", "-q", "-b", "main", str(seed))
    _git(seed, "config", "user.email", "test@holon.com")
    _git(seed, "config", "user.name", "Holon Test")
    graph = [
        GraphCommit("main", {"app.py": "print('hi')\n", LEDGER: ""}, "base"),
        GraphCommit(PLAN, {"plans/P-1.md": "# Plan\n"}, "plan", (0,)),
        GraphCommit(EXEC, {"executions/E-2.md": "# Notes\n"}, "execute: decomposed", (1,)),
        GraphCommit("main", {"src/upstream.py": "u = 1\n"}, "main advances", (0,)),
    ]
    if child_files is not None:
        graph.append(GraphCommit(CHILD, child_files, "child work", (1,)))
        merged = GraphCommit(PLAN, child_files, "merge child", (1, 4))
        graph.append(GraphCommit(PLAN, child_files, "squash child", (1,)) if squash else merged)
        graph.append(GraphCommit(EXEC, child_files, f"integrate: merge {PLAN} into {EXEC}", (2, 5)))
    exec_tip = len(graph) - 1 if child_files is not None else 2
    ledger_row = row if row is not None else {"entry_type": "summary", "status": "success"}
    graph.append(GraphCommit(EXEC, {LEDGER: json.dumps(ledger_row) + "\n"}, "execute: ledger row", (exec_tip,)))
    fast_import(seed, graph, identity="Holon Test <test@holon.com>")
    _git(seed, "checkout", "-q", "-f", EXEC)
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "--all")
    host = tmp_path / "host"
    _git(tmp_path, "init", "-q", "-b", "main", str(host))
    _git(host, "remote", "add", "origin", str(origin))


Build = Callable[..., Path]


@pytest.fixture
def build(tmp_path_factory: pytest.TempPathFactory, tmp_path: Path) -> Build:
    """Makes the repositories of ``_build_into(child_files, row, squash)`` in ``into`` (``tmp_path``); returns the seed.

    Each distinct history is built once per run as a template; the test works in its own copy.
    """

    def make(
        child_files: dict[str, str] | None, row: dict[str, Any] | None, squash: bool = False, into: Path = tmp_path
    ) -> Path:
        def build_template(root: Path) -> None:
            _build_into(root, child_files, row, squash)

        name = template_name("combined-base", child_files, row, squash)
        copy_template(shared_template(tmp_path_factory, name, build_template)[0], into)
        return into / "seed"

    return make


def _host(tmp_path: Path) -> Path:
    """The unfetched host clone ``_build_into`` made: only an origin remote, no tracking refs yet."""
    return tmp_path / "host"


def _merge_base(seed: Path) -> str:
    return _git(seed, "merge-base", "main", EXEC)


@pytest.fixture(autouse=True)
def unmounted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOLON_TARGET_WORKTREE", raising=False)


def test_combined_execution_with_merged_children_passes(build: Build, tmp_path: Path) -> None:
    # A merge commit and a squash merge of the child take the same path: one copy each, every assertion kept. The
    # merge-commit case also carries test_compared_on_origin_from_unfetched_host's assertions (same lines and arcs).
    for squash in (False, True):
        root = tmp_path / f"squash-{squash}"
        seed = build({"src/feature.py": "f = 1\n"}, _integrated_row(), squash=squash, into=root)
        host = _host(root)
        has_diff, detail = _execution_effective_diff(str(host), EXEC, target_branch="main")
        assert has_diff is True, squash
        assert detail == (
            f"1 changed path(s) against main@{_merge_base(seed)[:12]} (merge base of '{EXEC}' with main) on origin"
        ), squash
        assert detail.endswith("on origin")
        for branch in (PLAN, EXEC, "main"):
            assert _git(host, "rev-parse", "--verify", f"refs/remotes/origin/{branch}"), (squash, branch)


def test_combined_execution_with_only_bookkeeping_is_refused(build: Build, tmp_path: Path) -> None:
    seed = build({"executions/E-9.md": "# Notes\n", "plans/P-9.md": "# Plan\n"}, _integrated_row())
    has_diff, detail = _execution_effective_diff(str(_host(tmp_path)), EXEC, target_branch="main")
    assert has_diff is False
    assert detail.startswith(f"{EMPTY_PREFIX}main@")
    assert _merge_base(seed)[:12] in detail
    assert EXEC in detail
    assert detail.endswith("(compared on origin)")


def test_plain_execution_is_unchanged(build: Build, tmp_path: Path) -> None:
    seed = build(None, None)
    host = _host(tmp_path)
    assert _execution_effective_diff(str(host), EXEC, target_branch="main") == (
        False,
        f"{EMPTY_PREFIX}{PLAN} (compared on origin)",
    )
    _commit_files(seed, {"src/work.py": "w = 1\n"}, "real work")
    _git(seed, "push", "-q", "origin", EXEC)
    assert _execution_effective_diff(str(host), EXEC, target_branch="main") == (True, "1 changed path(s) on origin")


@pytest.mark.parametrize(
    "row", [_integrated_row(plan_branch="I-1-guard/P-9-other/_"), _integrated_row(children=[])], ids=["other", "none"]
)
def test_integrated_row_for_another_plan_branch_keeps_plan_base(
    build: Build, tmp_path: Path, row: dict[str, Any]
) -> None:
    build({"src/feature.py": "f = 1\n"}, row)
    assert _execution_effective_diff(str(_host(tmp_path)), EXEC, target_branch="main") == (
        False,
        f"{EMPTY_PREFIX}{PLAN} (compared on origin)",
    )


def test_mounted_container_without_target_ref_is_unverifiable(
    build: Build, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build({"src/feature.py": "f = 1\n"}, _integrated_row())
    host = _host(tmp_path)
    _git(host, "fetch", "-q", "origin", f"+refs/heads/{PLAN}:refs/remotes/origin/{PLAN}")
    _git(host, "fetch", "-q", "origin", f"+refs/heads/{EXEC}:refs/remotes/origin/{EXEC}")
    monkeypatch.setattr("sandbox_executor.flow.git_refs.workspace_mounted", lambda: True)
    has_diff, reason = _execution_effective_diff(str(host), EXEC, target_branch="main")
    assert has_diff is None
    assert reason == "'main' is not fetched from origin; the host must fetch it before dispatch"


def test_missing_merge_base_is_unverifiable(build: Build, tmp_path: Path) -> None:
    seed = build({"src/feature.py": "f = 1\n"}, _integrated_row())
    _git(seed, "checkout", "-q", "--orphan", "orphan")
    _commit_files(seed, {"other.txt": "o\n"}, "orphan root")
    _git(seed, "push", "-q", "origin", "orphan")
    has_diff, reason = _execution_effective_diff(str(_host(tmp_path)), EXEC, target_branch="orphan")
    assert has_diff is None
    assert reason == f"no merge base between 'orphan' and '{EXEC}'"


def test_local_branches_without_origin_use_local_target(build: Build, tmp_path: Path) -> None:
    seed = build({"src/feature.py": "f = 1\n"}, _integrated_row())
    _git(seed, "remote", "remove", "origin")
    has_diff, detail = _execution_effective_diff(str(seed), EXEC, target_branch="main")
    assert has_diff is True
    assert detail.endswith("on local branches (no 'origin' remote)")


def test_review_classification_threads_target_branch(build: Build, tmp_path: Path) -> None:
    build({"src/feature.py": "f = 1\n"}, _integrated_row())
    verdict, detail = classify_review_diff(str(_host(tmp_path)), EXEC, "main")
    assert verdict is DiffVerdict.VERIFIED
    assert "against main@" in detail
