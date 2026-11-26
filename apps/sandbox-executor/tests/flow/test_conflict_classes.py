"""The mechanical conflict classes and their self-verifying resolvers, on real conflicting merges."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from sandbox_executor.flow import conflict_classes_resolve
from sandbox_executor.flow.conflict_classes import ConflictClass, classify_merge_conflicts
from sandbox_executor.flow.conflict_classes_lists import appended_list_union
from sandbox_executor.flow.conflict_classes_resolve import resolve_mechanical
from sandbox_executor.ledger import read_merged_rows

from tests.flow.helpers import _git
from tests.flow_repo_templates import FlowCommit, build_flow_history
from tests.ledger_conflict_repos import FLOW_BRANCH

LEDGER = "holon-knowledge/ledger/executions.jsonl"
FRAGMENT = "holon-knowledge/ledger/pending/executions/I-1.jsonl"
KB = "holon-knowledge/kb/entry.json"
INIT = "pkg/__init__.py"


def _conflicted(tmp_path: Path, base: dict[str, str], flow: dict, target: dict) -> tuple[Path, list[str]]:
    """A flow worktree midway through a conflicting merge of ``origin/main``, and its unmerged paths."""
    commits = [FlowCommit("main", target, "target"), FlowCommit(FLOW_BRANCH, flow, "flow")]
    work = build_flow_history(tmp_path, base, commits).work
    merge = subprocess.run(["git", "merge", "--no-ff", "--no-edit", "origin/main"], cwd=work, capture_output=True)
    assert merge.returncode == 1
    unmerged = _git(work, "diff", "--name-only", "--diff-filter=U").splitlines()
    return work, unmerged


def _classify_and_resolve(work: Path, paths: list[str]):
    classified = classify_merge_conflicts(str(work), paths)
    return classified, resolve_mechanical(str(work), classified)


def _row(execution_id: str, created_at: str) -> str:
    return json.dumps({"execution_id": execution_id, "created_at": created_at}) + "\n"


def test_an_older_branch_ledger_row_is_inserted_before_the_newer_target_tail(tmp_path: Path):
    base = _row("E-base", "2026-11-01T00:00:00Z")
    target = base + _row("E-main", "2026-11-04T00:00:00Z")
    flow = base + _row("E-flow", "2026-11-03T00:00:00Z")
    work, paths = _conflicted(tmp_path, {LEDGER: base}, {LEDGER: flow}, {LEDGER: target})

    classified, outcome = _classify_and_resolve(work, paths)

    assert classified == [(LEDGER, ConflictClass.LEDGER)]
    assert outcome.resolved == [(LEDGER, ConflictClass.LEDGER)] and outcome.code_paths == []
    expected = base + _row("E-flow", "2026-11-03T00:00:00Z") + _row("E-main", "2026-11-04T00:00:00Z")
    assert (work / LEDGER).read_text(encoding="utf-8") == expected
    assert _git(work, "diff", "--name-only", "--diff-filter=U") == ""


def test_a_fragment_promoted_on_the_target_keeps_the_branch_copy(tmp_path: Path):
    promoted = _row("E-base", "2026-11-01T00:00:00Z")
    flow = promoted + _row("E-flow", "2026-11-03T00:00:00Z")
    target = {LEDGER: promoted, FRAGMENT: None}
    work, paths = _conflicted(tmp_path, {LEDGER: "", FRAGMENT: promoted}, {FRAGMENT: flow}, target)

    classified, outcome = _classify_and_resolve(work, paths)

    assert classified == [(FRAGMENT, ConflictClass.LEDGER)]
    assert outcome.resolved == [(FRAGMENT, ConflictClass.LEDGER)] and outcome.code_paths == []
    assert (work / FRAGMENT).read_text(encoding="utf-8") == flow
    assert _git(work, "diff", "--name-only", "--diff-filter=U") == ""
    rows = read_merged_rows(str(work / LEDGER))
    assert [row["execution_id"] for row in rows] == ["E-base", "E-flow"]


def _kb(title: str, updated_at: str) -> str:
    return json.dumps({"title": title, "meta": {"updated_at": updated_at}, "created_at": "2026-01-01"}, indent=2)


def test_a_timestamp_only_kb_entry_keeps_the_target_copy(tmp_path: Path):
    work, paths = _conflicted(tmp_path, {KB: _kb("t", "1")}, {KB: _kb("t", "2")}, {KB: _kb("t", "3")})

    classified, outcome = _classify_and_resolve(work, paths)

    assert classified == [(KB, ConflictClass.KB_TIMESTAMP_ONLY)]
    assert outcome.code_paths == []
    assert (work / KB).read_text(encoding="utf-8") == _kb("t", "3")


@pytest.mark.parametrize(
    ("rel", "payload"),
    [
        (
            "holon-knowledge/kb/patterns/KB-pattern-1234.json",
            lambda ts: json.dumps(
                {
                    "kb_id": "KB-pattern-1234",
                    "kb_type": "pattern",
                    "version": "1.0",
                    "created_ts": ts,
                    "updated_ts": ts,
                    "status": "active",
                    "payload": {"name": "Proven approach"},
                },
                indent=2,
            ),
        ),
        (
            "holon-knowledge/wisdom/heuristics/WB-heuristic-1234.json",
            lambda ts: json.dumps(
                {
                    "wb_id": "WB-heuristic-1234",
                    "wb_type": "heuristic",
                    "version": "1.0",
                    "created_ts": ts,
                    "scope": "global",
                    "status": "active",
                    "payload": {"name": "Universal heuristic"},
                },
                indent=2,
            ),
        ),
    ],
    ids=["kb_pattern_created_updated_ts", "wisdom_heuristic_created_ts"],
)
def test_real_kb_and_wisdom_ts_only_conflicts_keep_the_target_copy(tmp_path: Path, rel, payload):
    base = payload("2026-11-06T08:50:16+00:00")
    ours = payload("2026-11-07T09:00:00+00:00")
    theirs = payload("2026-11-08T10:00:00+00:00")
    work, paths = _conflicted(tmp_path, {rel: base}, {rel: ours}, {rel: theirs})

    classified, outcome = _classify_and_resolve(work, paths)

    assert classified == [(rel, ConflictClass.KB_TIMESTAMP_ONLY)]
    assert outcome.code_paths == []
    assert (work / rel).read_text(encoding="utf-8") == theirs


def test_a_kb_entry_with_a_different_title_is_code(tmp_path: Path):
    work, paths = _conflicted(tmp_path, {KB: _kb("t", "1")}, {KB: _kb("flow", "2")}, {KB: _kb("main", "3")})

    classified, outcome = _classify_and_resolve(work, paths)

    assert classified == [(KB, ConflictClass.CODE)]
    assert outcome.code_paths == [KB]


def test_a_target_deletion_beats_a_whitespace_only_branch_edit(tmp_path: Path):
    source = "import os\nimport json\n\nx = 1\n"
    edited = "import json\nimport os\n\n\nx  =  1\n"
    work, paths = _conflicted(tmp_path, {"old.py": source}, {"old.py": edited}, {"old.py": None})

    classified, outcome = _classify_and_resolve(work, paths)

    assert classified == [("old.py", ConflictClass.DELETE_VS_TOUCH)]
    assert outcome.code_paths == []
    assert not (work / "old.py").exists()


def test_a_target_deletion_against_a_real_branch_edit_is_code(tmp_path: Path):
    work, paths = _conflicted(tmp_path, {"old.py": "x = 1\n"}, {"old.py": "x = 2\n"}, {"old.py": None})

    classified, outcome = _classify_and_resolve(work, paths)

    assert classified == [("old.py", ConflictClass.CODE)]
    assert outcome.code_paths == ["old.py"]
    assert (work / "old.py").exists()


ALL_BASE = '"""Pkg."""\n\nVALUE = 1\n\n__all__ = [\n    "a",\n]\n'


def test_both_sides_appending_to_all_give_an_ordered_union_that_imports(tmp_path: Path):
    flow = ALL_BASE.replace('    "a",\n', '    "a",\n    "flow_one",\n    "shared",\n')
    target = ALL_BASE.replace('    "a",\n', '    "a",\n    "main_one",\n    "shared",\n')
    work, paths = _conflicted(tmp_path, {INIT: ALL_BASE}, {INIT: flow}, {INIT: target})

    classified, outcome = _classify_and_resolve(work, paths)

    assert classified == [(INIT, ConflictClass.APPENDED_LIST)]
    assert outcome.code_paths == []
    probe = "import pkg; print(pkg.__all__)"
    printed = subprocess.run([sys.executable, "-c", probe], cwd=work, capture_output=True, text=True, check=True)
    assert printed.stdout.strip() == "['a', 'main_one', 'shared', 'flow_one']"


def test_an_appended_list_plus_a_body_change_is_code(tmp_path: Path):
    flow = ALL_BASE.replace('    "a",\n', '    "a",\n    "flow_one",\n').replace("VALUE = 1", "VALUE = 2")
    target = ALL_BASE.replace('    "a",\n', '    "a",\n    "main_one",\n')
    work, paths = _conflicted(tmp_path, {INIT: ALL_BASE}, {INIT: flow}, {INIT: target})

    classified, outcome = _classify_and_resolve(work, paths)

    assert classified == [(INIT, ConflictClass.CODE)]
    assert outcome.code_paths == [INIT]
    assert "<<<<<<<" in (work / INIT).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("base", "ours", "theirs", "expected"),
    [
        ("X = ['a']\n", "X = ['a', 'b']\n", "X = ['a', 'c']\n", "X = ['a', 'c', 'b']\n"),
        (
            'M = {\n    "a": "1",\n    **R,\n}\n',
            'M = {\n    "a": "1",\n    **R,\n    "b": "2",\n}\n',
            'M = {\n    "a": "1",\n    **R,\n    "c": "3"\n}\n',
            'M = {\n    "a": "1",\n    **R,\n    "c": "3",\n    "b": "2",\n}\n',
        ),
        ("X: list[str] = ['a']\n", "X: list[str] = ['a', 'b']\n", "X: list[str] = ['a', 'b']\n", None),
        (
            "X = [\n    'a',\n]\n",
            "X = [\n    'a',\n    'b',\n]\n",
            "X = [\n    'a',\n    f(\n        1),\n]\n",
            "X = [\n    'a',\n    f(\n        1),\n    'b',\n]\n",
        ),
        (
            "X = [\n    'a',\n]\n",
            "X = [\n    'a',\n    'b',\n]\n",
            "X = [\n    'a',\n    'c'  # note\n]\n",
            "X = [\n    'a',\n    'c',  # note\n    'b',\n]\n",
        ),
    ],
)
def test_list_union_layouts(base, ours, theirs, expected):
    result = appended_list_union(base, ours, theirs)

    assert result == (theirs if expected is None else expected)


@pytest.mark.parametrize(
    "ours",
    [
        "# flow note\nX = [\n    'a',\n    'b',\n]\n",
        "X = [\n    'a',  # flow note\n    'b',\n]\n",
        "X = [\n    'a',\n    'b',  # flow note\n]\n",
        "X = [  # pragma: no cover\n    'a',\n    'b',\n]\n",
    ],
)
def test_list_union_refuses_to_drop_a_flow_side_comment(ours):
    assert appended_list_union("X = [\n    'a',\n]\n", ours, "X = [\n    'a',\n    'c',\n]\n") is None


def test_list_union_keeps_a_base_comment_after_the_last_entry():
    base = "X = [\n    'a',  # note\n]\n"
    ours = "X = [\n    'a',  # note\n    'b',\n]\n"
    theirs = "X = [\n    'a',  # note\n    'c',\n]\n"

    assert appended_list_union(base, ours, theirs) == "X = [\n    'a',  # note\n    'c',\n    'b',\n]\n"


def test_list_union_refuses_a_removed_entry():
    assert appended_list_union("X = ['a', 'b']\n", "X = ['a']\n", "X = ['a', 'b', 'c']\n") is None


def test_a_target_deletion_against_a_branch_reindent_is_code(tmp_path: Path):
    source = "if flag:\n    x = 1\ny = 2\n"
    reindented = "if flag:\n    x = 1\n    y = 2\n"
    work, paths = _conflicted(tmp_path, {"old.py": source}, {"old.py": reindented}, {"old.py": None})

    classified, outcome = _classify_and_resolve(work, paths)

    assert classified == [("old.py", ConflictClass.CODE)]
    assert outcome.code_paths == ["old.py"]
    assert (work / "old.py").read_text(encoding="utf-8") == reindented


@pytest.mark.parametrize(
    ("path", "source", "edited"),
    [
        ("old.py", "x = 'a b'\n", "x = 'a  b'\n"),
        ("old.py", "x = 1  # keep\n", "x = 1  # changed\n"),
        ("conf.yaml", "a:\n  b: 1\nc: 2\n", "a:\n  b: 1\n  c: 2\n"),
        ("Makefile", "all:\n\techo hi\n", "all:\necho hi\n"),
    ],
)
def test_a_target_deletion_against_a_string_comment_or_indent_edit_is_code(tmp_path: Path, path, source, edited):
    work, paths = _conflicted(tmp_path, {path: source}, {path: edited}, {path: None})

    classified, outcome = _classify_and_resolve(work, paths)

    assert classified == [(path, ConflictClass.CODE)]
    assert outcome.code_paths == [path]


def test_a_failed_delete_resolution_restores_the_unmerged_stages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = "import os\nimport json\n"
    work, paths = _conflicted(tmp_path, {"old.py": source}, {"old.py": "import json\nimport os\n"}, {"old.py": None})

    def rm_then_doubt(worktree: str, sides) -> bool:
        _git(Path(worktree), "rm", "-q", "--", sides.path)
        return False

    monkeypatch.setitem(conflict_classes_resolve._RESOLVERS, ConflictClass.DELETE_VS_TOUCH, rm_then_doubt)

    _, outcome = _classify_and_resolve(work, paths)

    assert outcome.code_paths == ["old.py"]
    assert _git(work, "diff", "--name-only", "--diff-filter=U") == "old.py"
    assert (work / "old.py").exists()


def test_a_non_utf8_conflict_is_code(tmp_path: Path):
    commits = [
        FlowCommit("main", {"blob.py": b"x = '\xff\xfe main'\n"}, "main bytes"),
        FlowCommit(FLOW_BRANCH, {"blob.py": b"x = '\xff\xfe flow'\n"}, f"{FLOW_BRANCH} bytes"),
    ]
    work = build_flow_history(tmp_path, {"blob.py": "x = 0\n"}, commits).work
    merge = subprocess.run(["git", "merge", "--no-ff", "--no-edit", "origin/main"], cwd=work, capture_output=True)
    assert merge.returncode == 1

    classified, outcome = _classify_and_resolve(work, ["blob.py"])

    assert classified == [("blob.py", ConflictClass.CODE)]
    assert outcome.code_paths == ["blob.py"]


@pytest.mark.parametrize(
    ("base", "ours", "theirs"),
    [
        ("M = {'a': 1}\n", "M = {'a': 1, 'k': 2}\n", "M = {'a': 1, 'k': 3}\n"),
        ("M = {'a': 1}\n", "M = {'a': 1, 'a': 2}\n", "M = {'a': 1, 'c': 3}\n"),
        ("M = {'a': 1}\n", "M = {'a': 1, **R}\n", "M = {'a': 1, 'c': 3}\n"),
        ("X = []\n", "X = ['b']\n", "X = []\n"),
    ],
)
def test_list_union_refuses_overlapping_keys_splats_and_empty_targets(base, ours, theirs):
    assert appended_list_union(base, ours, theirs) is None
