"""Property and edge-case tests for the Stage 5 patch-size base: counted files are exactly the PR's own files.

Each example builds a throwaway git repository (never the real checkout): ``main``, an intent, plan and execution
branch, PR commits interleaved with zero to three merges of an advancing ``main``, and ``origin/main`` pointing at
the final ``main``. The counted paths ``parse_actual_metrics`` reports must equal the paths the PR's own commits
change, whatever ``main`` brought in.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.configuration import set_hypothesis_home_dir
from sandbox_executor.calibration import PredictedMetrics, parse_actual_metrics
from sandbox_executor.calibration_diff import (
    DIFF_BASE_PLAN,
    DIFF_BASE_TARGET_MERGE_BASE,
    intent_target_branch,
    patch_diff_range,
    target_branch_refs,
)

from tests.repo_templates import copy_template, shared_template

INTENT = "I-1-prop/_"
PLAN = "I-1-prop/P-2-prop/_"
EXEC_ID = "E-3-prop"
EXEC = f"I-1-prop/P-2-prop/{EXEC_ID}/_"

# Hypothesis caches under ``.hypothesis/`` in the working directory (the shared checkout); set at import, before its
# pytest plugin collects this module's tests.
set_hypothesis_home_dir(Path(tempfile.gettempdir()) / "holon-test-hypothesis")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def _fast_import_commit(
    ref: str,
    message: str,
    files: dict[str, str],
    *,
    parents: tuple[str, ...] = (),
    mark: int | None = None,
) -> str:
    msg_bytes = message.encode("utf-8")
    lines = [f"commit {ref}"]
    if mark is not None:
        lines.append(f"mark :{mark}")
    lines.append("committer Holon Test <test@holon.invalid> 1700000000 +0000")
    lines.append(f"data {len(msg_bytes)}\n{message}")
    if parents:
        lines.append(f"from {parents[0]}")
        for merged in parents[1:]:
            lines.append(f"merge {merged}")
    for rel, text in files.items():
        payload = text.encode("utf-8")
        lines.append(f"M 100644 inline {rel}\ndata {len(payload)}\n{text}")
    return "\n".join(lines) + "\n"


def _fast_import_reset(ref: str, target: str) -> str:
    return f"reset {ref}\nfrom {target}\n"


def _run_fast_import(repo: Path, parts: list[str]) -> None:
    subprocess.run(["git", "fast-import", "--quiet"], cwd=repo, input="".join(parts), text=True, check=True)


def _build_flow_template(root: Path) -> None:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    _run_fast_import(
        repo,
        [
            _fast_import_commit("refs/heads/main", "base", {"base.txt": "base\n"}, mark=1),
            _fast_import_reset(f"refs/heads/{INTENT}", ":1"),
            _fast_import_commit(
                f"refs/heads/{PLAN}", "plan", {"plans/I-1-prop/P-2-prop.md": "# Plan\n"}, parents=(":1",), mark=2
            ),
            _fast_import_reset(f"refs/heads/{EXEC}", ":2"),
        ],
    )
    git(repo, "checkout", "-q", "-f", EXEC)


@pytest.fixture(scope="session")
def flow_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return shared_template(tmp_path_factory, "cal-diff-flow-template", _build_flow_template)[0]


def make_flow(flow_template: Path, target: Path) -> Path:
    copy_template(flow_template, target, names=("repo",))
    return target / "repo"


def counted_paths(repo: Path) -> tuple[set[str], str]:
    _actual, meta = parse_actual_metrics(
        EXEC_ID, PLAN, PredictedMetrics(), execution_branch=EXEC, target_worktree=str(repo)
    )
    return set(meta["changed_paths"]), meta["diff_base"]


file_sets = st.lists(st.integers(min_value=0, max_value=5), min_size=1, max_size=3, unique=True)
# Each step is a PR commit (False) or a merge of a main advance (True); at most three merges.
steps = st.lists(st.tuples(st.booleans(), file_sets), min_size=1, max_size=6).filter(
    lambda s: sum(merge for merge, _ in s) <= 3 and any(not merge for merge, _ in s)
)


@settings(max_examples=25, deadline=None, database=None, suppress_health_check=[HealthCheck.too_slow])
@given(flow_steps=steps, unmerged_main=st.booleans())
def test_counted_files_are_exactly_the_pr_files(
    flow_template: Path, flow_steps: list[tuple[bool, list[int]]], unmerged_main: bool
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_flow(flow_template, Path(tmp))
        pr_files: set[str] = set()
        main_files: dict[str, str] = {}
        parts: list[str] = []
        next_mark = 1
        main_parent = "refs/heads/main^0"
        exec_parent = f"refs/heads/{EXEC}^0"
        for index, (merge, numbers) in enumerate(flow_steps):
            if merge:
                advance_files = dict.fromkeys((f"main/m{n}.py" for n in numbers), f"advance {index}\n")
                main_files.update(advance_files)
                main_mark = next_mark
                next_mark += 1
                parts.append(
                    _fast_import_commit(
                        "refs/heads/main",
                        f"main advance {index}",
                        advance_files,
                        parents=(main_parent,),
                        mark=main_mark,
                    )
                )
                main_parent = f":{main_mark}"
                merge_mark = next_mark
                next_mark += 1
                # why: fast-import does not auto-merge the second parent's tree; main/ and src/ are disjoint
                parts.append(
                    _fast_import_commit(
                        f"refs/heads/{EXEC}",
                        f"merge main {index}",
                        dict(main_files),
                        parents=(exec_parent, main_parent),
                        mark=merge_mark,
                    )
                )
                exec_parent = f":{merge_mark}"
            else:
                files = [f"src/pr{n}.py" for n in numbers]
                pr_mark = next_mark
                next_mark += 1
                parts.append(
                    _fast_import_commit(
                        f"refs/heads/{EXEC}",
                        f"pr {index}",
                        dict.fromkeys(files, f"pr {index}\n"),
                        parents=(exec_parent,),
                        mark=pr_mark,
                    )
                )
                exec_parent = f":{pr_mark}"
                pr_files.update(files)
        if unmerged_main:
            late_mark = next_mark
            parts.append(
                _fast_import_commit(
                    "refs/heads/main", "main late", {"main/late.py": "late\n"}, parents=(main_parent,), mark=late_mark
                )
            )
            main_parent = f":{late_mark}"
        parts.append(_fast_import_reset("refs/remotes/origin/main", main_parent))
        _run_fast_import(repo, parts)
        counted, _diff_base = counted_paths(repo)
        assert counted == pr_files


def test_merged_main_is_measured_from_the_target_merge_base(flow_template: Path, tmp_path: Path) -> None:
    repo = make_flow(flow_template, tmp_path)
    _run_fast_import(
        repo,
        [
            _fast_import_commit(
                f"refs/heads/{EXEC}", "pr", {"src/own.py": "own\n"}, parents=(f"refs/heads/{EXEC}^0",), mark=1
            ),
            _fast_import_commit(
                "refs/heads/main", "main other", {"main/other.py": "other\n"}, parents=("refs/heads/main^0",), mark=2
            ),
            _fast_import_commit(f"refs/heads/{EXEC}", "merge main", {"main/other.py": "other\n"}, parents=(":1", ":2")),
            _fast_import_reset("refs/remotes/origin/main", ":2"),
        ],
    )
    counted, diff_base = counted_paths(repo)
    assert (counted, diff_base) == ({"src/own.py"}, DIFF_BASE_TARGET_MERGE_BASE)


def test_local_main_only_is_used_when_origin_main_is_missing(flow_template: Path, tmp_path: Path) -> None:
    repo = make_flow(flow_template, tmp_path)
    _run_fast_import(
        repo,
        [
            _fast_import_commit(
                "refs/heads/main", "main other", {"main/other.py": "other\n"}, parents=("refs/heads/main^0",), mark=1
            ),
            _fast_import_commit(
                f"refs/heads/{EXEC}",
                "merge main",
                {"main/other.py": "other\n"},
                parents=(f"refs/heads/{EXEC}^0", ":1"),
                mark=2,
            ),
            _fast_import_commit(f"refs/heads/{EXEC}", "pr", {"src/own.py": "own\n"}, parents=(":2",)),
        ],
    )
    assert target_branch_refs("main") == ("refs/remotes/origin/main", "refs/heads/main")
    counted, diff_base = counted_paths(repo)
    assert (counted, diff_base) == ({"src/own.py"}, DIFF_BASE_TARGET_MERGE_BASE)


def test_unmerged_branch_keeps_the_plan_base(flow_template: Path, tmp_path: Path) -> None:
    repo = make_flow(flow_template, tmp_path)
    _run_fast_import(
        repo,
        [_fast_import_commit(f"refs/heads/{EXEC}", "pr", {"src/own.py": "own\n"}, parents=(f"refs/heads/{EXEC}^0",))],
    )
    counted, diff_base = counted_paths(repo)
    assert (counted, diff_base) == ({"src/own.py"}, DIFF_BASE_PLAN)


def test_target_that_contains_the_head_falls_back_to_the_plan(flow_template: Path, tmp_path: Path) -> None:
    repo = make_flow(flow_template, tmp_path)
    _run_fast_import(
        repo,
        [
            _fast_import_commit(
                "refs/heads/main", "main other", {"main/other.py": "other\n"}, parents=("refs/heads/main^0",), mark=1
            ),
            _fast_import_commit(
                f"refs/heads/{EXEC}",
                "merge main",
                {"main/other.py": "other\n"},
                parents=(f"refs/heads/{EXEC}^0", ":1"),
                mark=2,
            ),
            _fast_import_commit(f"refs/heads/{EXEC}", "pr", {"src/own.py": "own\n"}, parents=(":2",), mark=3),
            _fast_import_commit(
                "refs/heads/main",
                "merge exec",
                {"plans/I-1-prop/P-2-prop.md": "# Plan\n", "src/own.py": "own\n"},
                parents=(":1", ":3"),
            ),
        ],
    )
    _base, _target, label = patch_diff_range(PLAN, EXEC, str(repo), (), target_branch_refs("main"))
    assert label == DIFF_BASE_PLAN


def test_child_flow_targets_its_parent_plan_branch(tmp_path: Path) -> None:
    parent_plan = "I-0-parent/P-0-parent/_"
    row = {"branch": "I-1-prop", "target_branch": parent_plan}
    git(tmp_path, "init", "-q", "-b", "main")
    _run_fast_import(
        tmp_path,
        [
            _fast_import_commit("refs/heads/main", "base", {"base.txt": "base\n"}, mark=1),
            _fast_import_commit(
                f"refs/heads/{parent_plan}",
                "sibling merged into the parent plan",
                {"src/sibling.py": "sibling\n"},
                parents=(":1",),
                mark=2,
            ),
            _fast_import_commit(
                f"refs/heads/{parent_plan}",
                "intent row",
                {"holon-knowledge/ledger/intents.jsonl": json.dumps(row) + "\n"},
                parents=(":2",),
                mark=3,
            ),
            _fast_import_reset(f"refs/heads/{INTENT}", ":3"),
            _fast_import_reset(f"refs/heads/{PLAN}", ":3"),
            _fast_import_commit(f"refs/heads/{EXEC}", "child pr", {"src/child.py": "child\n"}, parents=(":3",), mark=4),
            _fast_import_commit(
                f"refs/heads/{parent_plan}",
                "later sibling",
                {"src/later_sibling.py": "later\n"},
                parents=(":3",),
                mark=5,
            ),
            _fast_import_commit(
                f"refs/heads/{EXEC}",
                "merge parent plan",
                {"src/later_sibling.py": "later\n"},
                parents=(":4", ":5"),
            ),
        ],
    )
    assert intent_target_branch(INTENT, EXEC, str(tmp_path)) == parent_plan
    counted, diff_base = counted_paths(tmp_path)
    assert (counted, diff_base) == ({"src/child.py"}, DIFF_BASE_TARGET_MERGE_BASE)


def test_intent_target_branch_defaults_to_main(flow_template: Path, tmp_path: Path) -> None:
    repo = make_flow(flow_template, tmp_path)
    assert intent_target_branch(INTENT, EXEC, str(repo)) == "main"
    assert intent_target_branch("", EXEC, str(repo)) == "main"
    assert target_branch_refs("") == ()


def _intents_ledger(repo: Path, *lines: str) -> None:
    _run_fast_import(
        repo,
        [
            _fast_import_commit(
                f"refs/heads/{EXEC}",
                "intents",
                {"holon-knowledge/ledger/intents.jsonl": "".join(f"{line}\n" for line in lines)},
                parents=(f"refs/heads/{EXEC}^0",),
            )
        ],
    )


def test_intent_target_branch_reads_the_matching_row(flow_template: Path, tmp_path: Path) -> None:
    repo = make_flow(flow_template, tmp_path)
    _intents_ledger(
        repo,
        "not json",
        "[1]",
        json.dumps({"target_branch": "no-branch-key"}),
        json.dumps({"branch": "I-9-other/_", "target_branch": "other"}),
        json.dumps({"branch": INTENT, "target_branch": "I-0-parent/P-0-parent/_"}),
    )
    assert intent_target_branch(INTENT, EXEC, str(repo)) == "I-0-parent/P-0-parent/_"
    assert intent_target_branch("", EXEC, str(repo)) == "main"


def test_intent_target_branch_without_a_matching_target_is_main(flow_template: Path, tmp_path: Path) -> None:
    repo = make_flow(flow_template, tmp_path)
    _intents_ledger(repo, json.dumps({"branch": "I-1-prop"}), json.dumps({"branch": "I-9-other", "target_branch": "x"}))
    assert intent_target_branch(INTENT, EXEC, str(repo)) == "main"


def test_target_branch_refs_strip_qualifiers() -> None:
    expected = ("refs/remotes/origin/main", "refs/heads/main")
    assert target_branch_refs("origin/main") == target_branch_refs("refs/heads/main") == expected


def test_target_with_unrelated_history_is_skipped_for_the_next_target(flow_template: Path, tmp_path: Path) -> None:
    repo = make_flow(flow_template, tmp_path)
    _run_fast_import(
        repo,
        [
            _fast_import_commit(
                f"refs/heads/{EXEC}", "pr", {"src/own.py": "own\n"}, parents=(f"refs/heads/{EXEC}^0",), mark=1
            ),
            _fast_import_commit(
                "refs/heads/main", "main other", {"main/other.py": "other\n"}, parents=("refs/heads/main^0",), mark=2
            ),
            _fast_import_commit(f"refs/heads/{EXEC}", "merge main", {"main/other.py": "other\n"}, parents=(":1", ":2")),
            _fast_import_commit("refs/heads/unrelated", "unrelated root", {"other.txt": "other\n"}),
        ],
    )
    refs = ("refs/heads/unrelated", *target_branch_refs("main"))
    base, _target, label = patch_diff_range(PLAN, EXEC, str(repo), (), refs)
    assert (base, label) == ("refs/heads/main", DIFF_BASE_TARGET_MERGE_BASE)
    _base, _target, label = patch_diff_range(PLAN, EXEC, str(repo), (), ("refs/heads/unrelated",))
    assert label == DIFF_BASE_PLAN


def test_gone_plan_branch_keeps_a_fallback_that_contains_the_target_merge_base(
    flow_template: Path, tmp_path: Path
) -> None:
    repo = make_flow(flow_template, tmp_path)
    _run_fast_import(
        repo,
        [_fast_import_commit(f"refs/heads/{EXEC}", "pr", {"src/own.py": "own\n"}, parents=(f"refs/heads/{EXEC}^0",))],
    )
    git(repo, "update-ref", "-d", f"refs/heads/{PLAN}")
    base, _target, label = patch_diff_range(PLAN, EXEC, str(repo), (INTENT, "main"), target_branch_refs("main"))
    assert (base, label) == (f"refs/heads/{INTENT}", "merge_base_fallback")
