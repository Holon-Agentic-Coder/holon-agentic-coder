"""Tests for the plans/{intent}/ layout: plan files and calibration reports follow their branch name."""

import re

import pytest
from sandbox_executor.plan_paths import (
    PLAN_FILE_PATTERN,
    bare_branch,
    calibration_report_candidates,
    calibration_report_for_branch,
    calibration_report_rel,
    is_calibration_report_path,
    is_row_of_plan,
    legacy_plan_file_rel,
    plan_branch_from_path,
    plan_branch_of,
    plan_file_candidates,
    plan_file_for_branch,
    plan_file_rel,
    recorded_plan_file,
    split_plan_branch,
)

ROOT = "I-1794567515-sleepfix"
PLAN = "P-1794567531-CLA-CS5.5"
EXEC = "E-1794567598-CLA-CS5.5"
CHILD = f"{ROOT}--1-add-the-parser"
CHILD_UNDER_PLAN = f"{ROOT}/P-1794567520-CLA-CO5.5--2-add-the-cli-flag"
NESTED_PARENT_PLAN = "P-1794567520-CLA-CO5.5"

# (branch, parent, plan id) for the three nesting cases of the goal plus a child intent nested below a plan.
NESTING_CASES = [
    (f"{ROOT}/{PLAN}/_", ROOT, PLAN),
    (f"{CHILD}/{PLAN}/_", CHILD, PLAN),
    (f"{ROOT}/{NESTED_PARENT_PLAN}/{PLAN}/_", f"{ROOT}/{NESTED_PARENT_PLAN}", PLAN),
    (f"{CHILD_UNDER_PLAN}/{PLAN}/_", CHILD_UNDER_PLAN, PLAN),
]


@pytest.mark.parametrize(("branch", "parent", "plan_id"), NESTING_CASES)
def test_plan_file_and_report_mirror_the_branch_nesting(branch: str, parent: str, plan_id: str) -> None:
    assert split_plan_branch(branch) == (parent, plan_id)
    assert plan_file_for_branch(branch) == f"plans/{parent}/{plan_id}.md"
    assert calibration_report_for_branch(branch) == f"plans/{parent}/{plan_id}_calibration.md"
    assert plan_file_rel(parent, plan_id) == plan_file_for_branch(branch)
    assert calibration_report_rel(f"{parent}/_", plan_id) == calibration_report_for_branch(branch)
    assert re.fullmatch(PLAN_FILE_PATTERN, plan_file_for_branch(branch))
    # Round trip: the path encodes the plan branch it was derived from.
    assert plan_branch_from_path(plan_file_for_branch(branch)) == bare_branch(branch)
    assert plan_branch_from_path(calibration_report_for_branch(branch)) == bare_branch(branch)
    assert is_calibration_report_path(calibration_report_for_branch(branch))


@pytest.mark.parametrize(
    "branch",
    [
        f"{ROOT}/{PLAN}/{EXEC}/_",
        f"{ROOT}/{PLAN}/{EXEC}/calibrated",
        f"origin/{ROOT}/{PLAN}/{EXEC}/_",
        f"refs/remotes/origin/{ROOT}/{PLAN}/_",
        f"refs/heads/{ROOT}/{PLAN}/{EXEC}/_",
    ],
)
def test_every_branch_below_a_plan_resolves_to_that_plan(branch: str) -> None:
    assert plan_branch_of(branch) == f"{ROOT}/{PLAN}"
    assert plan_file_for_branch(branch) == f"plans/{ROOT}/{PLAN}.md"


@pytest.mark.parametrize("branch", [f"{ROOT}/_", f"{CHILD_UNDER_PLAN}/_", "E-1-x", "", "main"])
def test_a_branch_naming_no_plan_has_no_plan_file(branch: str) -> None:
    assert split_plan_branch(branch) == ("", "")
    assert plan_file_for_branch(branch) == ""
    assert calibration_report_for_branch(branch) == ""


def test_legacy_flat_locations_stay_readable_after_the_new_layout() -> None:
    legacy_id = "P-1794192045-c23d4516-claude-agent-claude-opus-5-5"
    assert plan_file_candidates(ROOT, legacy_id) == [f"plans/{ROOT}/{legacy_id}.md", f"plans/{legacy_id}.md"]
    assert calibration_report_candidates(ROOT, legacy_id)[-1] == f"plans/{legacy_id}_calibration.md"
    assert plan_file_rel("", legacy_id) == legacy_plan_file_rel(legacy_id)
    assert re.fullmatch(PLAN_FILE_PATTERN, legacy_plan_file_rel(legacy_id))
    assert re.fullmatch(PLAN_FILE_PATTERN, f"plans/legacy/{legacy_id}.md")
    assert plan_branch_from_path(f"plans/legacy/{legacy_id}.md") == legacy_id
    assert is_calibration_report_path(f"plans/{legacy_id}_calibration.md")


@pytest.mark.parametrize(
    "path",
    ["plans/../P-1.md", "plans/P-x/../../README.md", "plans/sub/P-1.md", "README.md", "plans/I-1-x/notes.md"],
)
def test_the_plan_file_pattern_rejects_foreign_or_escaping_paths(path: str) -> None:
    assert not re.fullmatch(PLAN_FILE_PATTERN, path)


def test_a_recorded_plan_file_wins_and_a_foreign_one_falls_back_to_the_branch() -> None:
    branch = f"{ROOT}/{PLAN}/_"
    assert recorded_plan_file(f"plans/{PLAN}.md", branch) == f"plans/{PLAN}.md"
    assert recorded_plan_file("README.md", branch) == f"plans/{ROOT}/{PLAN}.md"
    assert recorded_plan_file(None, branch) == f"plans/{ROOT}/{PLAN}.md"


def test_a_plan_row_is_matched_on_its_intent_and_plan_id_together() -> None:
    branch = f"{ROOT}/{PLAN}/_"
    assert is_row_of_plan({"plan_id": PLAN, "intent_branch": f"{ROOT}/_"}, branch)
    assert is_row_of_plan({"plan_id": PLAN, "intent_branch": ROOT}, f"{ROOT}/{PLAN}/{EXEC}/_")
    # Same plan id, planned in the same second by the same runner and model, under another intent: never a match.
    assert not is_row_of_plan({"plan_id": PLAN, "intent_branch": "I-1794567515-other/_"}, branch)
    assert not is_row_of_plan({"plan_id": "P-1-other", "intent_branch": f"{ROOT}/_"}, branch)
    assert not is_row_of_plan({"plan_id": PLAN}, branch)


def test_an_execution_row_is_matched_on_its_full_path_when_the_branch_is_known() -> None:
    from sandbox_executor.flow.git_refs import _latest_execution_summary
    from sandbox_executor.plan_paths import is_row_of_execution

    mine = {"entry_type": "summary", "execution_id": EXEC, "plan_branch": f"{ROOT}/{PLAN}/_", "status": "success"}
    other = {**mine, "plan_branch": f"I-1794567515-other/{PLAN}/_", "status": "failure", "ledger_revision": 2}
    branch = f"refs/remotes/origin/{ROOT}/{PLAN}/{EXEC}/_"
    assert is_row_of_execution(mine, EXEC, branch)
    assert not is_row_of_execution(other, EXEC, branch)
    assert is_row_of_execution(other, EXEC)  # a bare id lookup (no branch known) matches on the id
    assert is_row_of_execution({"execution_id": EXEC, "plan_branch": "85"}, EXEC, branch)  # a row naming no plan
    # The newer superseding row of another plan's same-named execution never wins.
    assert _latest_execution_summary([mine, other], EXEC, branch) == mine
