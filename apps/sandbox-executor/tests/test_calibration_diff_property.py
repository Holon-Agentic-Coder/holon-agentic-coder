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

INTENT = "I-1-prop/_"
PLAN = "I-1-prop/P-2-prop/_"
EXEC_ID = "E-3-prop"
EXEC = f"I-1-prop/P-2-prop/{EXEC_ID}/_"

# Hypothesis caches under ``.hypothesis/`` in the working directory (the shared checkout); set at import, before its
# pytest plugin collects this module's tests.
set_hypothesis_home_dir(Path(tempfile.gettempdir()) / "holon-test-hypothesis")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def commit(repo: Path, files: dict[str, str], message: str) -> str:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git(repo, "add", "--", *files)
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def make_flow(repo: Path) -> None:
    """``main`` with a base commit, then intent, plan (with its plan markdown) and execution branches."""
    git(repo, "init", "-q", "-b", "main")
    commit(repo, {"base.txt": "base\n"}, "base")
    git(repo, "checkout", "-qb", INTENT)
    git(repo, "checkout", "-qb", PLAN)
    commit(repo, {"plans/I-1-prop/P-2-prop.md": "# Plan\n"}, "plan")
    git(repo, "checkout", "-qb", EXEC)


def advance_main(repo: Path, files: list[str], tag: str) -> None:
    git(repo, "checkout", "-q", "main")
    commit(repo, dict.fromkeys(files, f"{tag}\n"), f"main {tag}")
    git(repo, "checkout", "-q", EXEC)


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
def test_counted_files_are_exactly_the_pr_files(flow_steps: list[tuple[bool, list[int]]], unmerged_main: bool) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        make_flow(repo)
        pr_files: set[str] = set()
        for index, (merge, numbers) in enumerate(flow_steps):
            if merge:
                advance_main(repo, [f"main/m{n}.py" for n in numbers], f"advance {index}")
                git(repo, "merge", "-q", "--no-edit", "main")
            else:
                files = [f"src/pr{n}.py" for n in numbers]
                commit(repo, dict.fromkeys(files, f"pr {index}\n"), f"pr {index}")
                pr_files.update(files)
        if unmerged_main:
            advance_main(repo, ["main/late.py"], "late")
        git(repo, "update-ref", "refs/remotes/origin/main", "refs/heads/main")
        counted, _diff_base = counted_paths(repo)
        assert counted == pr_files


def test_merged_main_is_measured_from_the_target_merge_base(tmp_path: Path) -> None:
    make_flow(tmp_path)
    commit(tmp_path, {"src/own.py": "own\n"}, "pr")
    advance_main(tmp_path, ["main/other.py"], "other")
    git(tmp_path, "merge", "-q", "--no-edit", "main")
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "refs/heads/main")
    counted, diff_base = counted_paths(tmp_path)
    assert (counted, diff_base) == ({"src/own.py"}, DIFF_BASE_TARGET_MERGE_BASE)


def test_local_main_only_is_used_when_origin_main_is_missing(tmp_path: Path) -> None:
    make_flow(tmp_path)
    advance_main(tmp_path, ["main/other.py"], "other")
    git(tmp_path, "merge", "-q", "--no-edit", "main")
    commit(tmp_path, {"src/own.py": "own\n"}, "pr")
    assert target_branch_refs("main") == ("refs/remotes/origin/main", "refs/heads/main")
    counted, diff_base = counted_paths(tmp_path)
    assert (counted, diff_base) == ({"src/own.py"}, DIFF_BASE_TARGET_MERGE_BASE)


def test_unmerged_branch_keeps_the_plan_base(tmp_path: Path) -> None:
    """An execution branch that never merged main is measured from its (newer) plan branch."""
    make_flow(tmp_path)
    commit(tmp_path, {"src/own.py": "own\n"}, "pr")
    counted, diff_base = counted_paths(tmp_path)
    assert (counted, diff_base) == ({"src/own.py"}, DIFF_BASE_PLAN)


def test_target_that_contains_the_head_falls_back_to_the_plan(tmp_path: Path) -> None:
    make_flow(tmp_path)
    advance_main(tmp_path, ["main/other.py"], "other")
    git(tmp_path, "merge", "-q", "--no-edit", "main")
    commit(tmp_path, {"src/own.py": "own\n"}, "pr")
    git(tmp_path, "checkout", "-q", "main")
    git(tmp_path, "merge", "-q", "--no-edit", EXEC)
    git(tmp_path, "checkout", "-q", EXEC)
    _base, _target, label = patch_diff_range(PLAN, EXEC, str(tmp_path), (), target_branch_refs("main"))
    assert label == DIFF_BASE_PLAN


def test_child_flow_targets_its_parent_plan_branch(tmp_path: Path) -> None:
    """A child intent's ledger row names the parent plan branch, so main is never the base."""
    parent_plan = "I-0-parent/P-0-parent/_"
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, {"base.txt": "base\n"}, "base")
    git(tmp_path, "checkout", "-qb", parent_plan)
    commit(tmp_path, {"src/sibling.py": "sibling\n"}, "sibling merged into the parent plan")
    row = {"branch": "I-1-prop", "target_branch": parent_plan}
    commit(tmp_path, {"holon-knowledge/ledger/intents.jsonl": json.dumps(row) + "\n"}, "intent row")
    git(tmp_path, "checkout", "-qb", INTENT)
    git(tmp_path, "checkout", "-qb", PLAN)
    git(tmp_path, "checkout", "-qb", EXEC)
    commit(tmp_path, {"src/child.py": "child\n"}, "child pr")
    git(tmp_path, "checkout", "-q", parent_plan)
    commit(tmp_path, {"src/later_sibling.py": "later\n"}, "later sibling")
    git(tmp_path, "checkout", "-q", EXEC)
    git(tmp_path, "merge", "-q", "--no-edit", parent_plan)
    assert intent_target_branch(INTENT, EXEC, str(tmp_path)) == parent_plan
    counted, diff_base = counted_paths(tmp_path)
    assert (counted, diff_base) == ({"src/child.py"}, DIFF_BASE_TARGET_MERGE_BASE)


def test_intent_target_branch_defaults_to_main(tmp_path: Path) -> None:
    make_flow(tmp_path)
    assert intent_target_branch(INTENT, EXEC, str(tmp_path)) == "main"
    assert intent_target_branch("", EXEC, str(tmp_path)) == "main"
    assert target_branch_refs("") == ()
