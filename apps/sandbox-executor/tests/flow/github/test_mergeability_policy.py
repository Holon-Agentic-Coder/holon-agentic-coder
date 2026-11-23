"""Literal-only tests for the I/O-free mergeability and merge refusal verdicts in ``mergeability_policy.py``."""

from __future__ import annotations

import pytest
from sandbox_executor.flow.github.mergeability_policy import (
    gh_view_verdict,
    local_error_result,
    merge_refusal_reason,
    merge_tree_verdict,
    mergeability_result,
    missing_ref_result,
    not_a_repository_result,
)

PLAN = "I-1-root/P-2-agent-model/_"


def local(mergeable: str, status: str, *, is_dirty: bool = False, diagnostics: dict | None = None) -> dict:
    return {
        "mergeable": mergeable,
        "merge_state_status": status,
        "is_dirty": is_dirty,
        "source": "git_local",
        "pr_number": None,
        "status_check_rollup": None,
        "diagnostics": diagnostics or {},
    }


@pytest.mark.parametrize(
    ("data", "mergeable", "status", "is_dirty", "still_computing"),
    [
        ({"mergeable": "MERGEABLE", "mergeStateStatus": "DIRTY"}, "MERGEABLE", "DIRTY", True, False),
        ({"mergeable": "CONFLICTING", "mergeStateStatus": "BLOCKED"}, "CONFLICTING", "BLOCKED", True, False),
        ({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"}, "MERGEABLE", "CLEAN", False, False),
        ({"mergeable": "UNKNOWN", "mergeStateStatus": "UNKNOWN"}, "UNKNOWN", "UNKNOWN", False, True),
        ({}, "UNKNOWN", "UNKNOWN", False, True),
        ({"mergeable": "conflicting", "mergeStateStatus": "dirty"}, "CONFLICTING", "DIRTY", True, False),
    ],
)
def test_gh_view_verdict(data, mergeable, status, is_dirty, still_computing):
    result, computing = gh_view_verdict(data)
    assert computing is still_computing
    assert result == {
        "mergeable": mergeable,
        "merge_state_status": status,
        "is_dirty": is_dirty,
        "source": "github_cli",
        "pr_number": None,
        "status_check_rollup": None,
        "diagnostics": {},
    }


def test_gh_view_verdict_carries_the_pr_number_and_rollup():
    data = {"number": 7, "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "statusCheckRollup": [{"x": 1}]}
    result, _ = gh_view_verdict(data)
    assert (result["pr_number"], result["status_check_rollup"]) == (7, [{"x": 1}])


def test_merge_tree_verdict_maps_exit_codes():
    assert merge_tree_verdict(0, "") == local("MERGEABLE", "CLEAN")
    assert merge_tree_verdict(1, "conflict") == local("CONFLICTING", "DIRTY", is_dirty=True)
    assert merge_tree_verdict(128, " fatal: refusing \n") == local(
        "UNKNOWN", "UNKNOWN", diagnostics={"merge_tree_exit_code": 128, "stderr": "fatal: refusing"}
    )
    assert merge_tree_verdict(2, None) == local(
        "UNKNOWN", "UNKNOWN", diagnostics={"merge_tree_exit_code": 2, "stderr": ""}
    )


def test_missing_ref_result():
    assert missing_ref_result("origin/feature", "branch", ["feature", "origin/feature"], "bad ref") == local(
        "UNKNOWN",
        "MISSING_REF",
        diagnostics={
            "missing_ref": "origin/feature",
            "ref_role": "branch",
            "candidates_tried": ["feature", "origin/feature"],
            "stderr": "bad ref",
            "message": "cannot evaluate mergeability for ref origin/feature",
        },
    )


def test_not_a_repository_and_local_error_results():
    assert not_a_repository_result("/tmp/x", "fatal") == local(
        "UNKNOWN", "UNKNOWN", diagnostics={"error": "not a git repository: /tmp/x", "stderr": "fatal"}
    )
    assert local_error_result(ValueError("boom")) == local("UNKNOWN", "UNKNOWN", diagnostics={"error": "boom"})


def test_mergeability_result_gives_each_result_its_own_diagnostics():
    first, second = mergeability_result("UNKNOWN", "UNKNOWN"), mergeability_result("UNKNOWN", "UNKNOWN")
    first["diagnostics"]["error"] = "x"
    assert second["diagnostics"] == {}


@pytest.mark.parametrize(
    ("base", "expected", "default", "reason"),
    [
        (
            PLAN,
            PLAN,
            None,
            "the repository default branch could not be determined, so the merge is refused (fail closed)",
        ),
        (
            PLAN,
            PLAN,
            "  ",
            "the repository default branch could not be determined, so the merge is refused (fail closed)",
        ),
        ("", PLAN, "main", "the PR base or the expected plan branch is empty"),
        (PLAN, None, "main", "the PR base or the expected plan branch is empty"),
        ("main", "main", "main", "the expected base 'main' is main, master or the repository default branch 'main'"),
        ("master", PLAN, "main", "the PR base 'master' is main, master or the repository default branch 'main'"),
        ("trunk", PLAN, "origin/trunk", "the PR base 'trunk' is main, master or the repository default branch 'trunk'"),
        ("refs/heads/feature", PLAN, "main", f"the PR base 'feature' is not the expected plan branch '{PLAN}'"),
        (f"origin/{PLAN}", f"refs/heads/{PLAN}", "main", None),
        (PLAN, PLAN, "main", None),
    ],
)
def test_merge_refusal_reason(base, expected, default, reason):
    assert merge_refusal_reason(base, expected, default) == reason
