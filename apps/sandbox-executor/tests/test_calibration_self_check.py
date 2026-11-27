"""The calibration report self-check: one failing case per condition, a consistent report, and fail-closed wiring."""

from __future__ import annotations

import dataclasses
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.calibration import run_calibrate
from sandbox_executor.calibration_ev import evaluate_dual_ev_outcomes
from sandbox_executor.calibration_self_check import (
    FALLBACK_REPORT_MARKER,
    CalibrationSelfCheckError,
    SelfCheckInputs,
    reaches_through_metadata,
    same_commit,
    self_check_report,
    target_only_paths,
)

from tests.calibrate_scenarios import build_self_check_flow_repo, own_path_copy
from tests.repo_templates import shared_template

PLAN = "I-1-sc/P-2-sc/_"
EXEC_ID = "E-3-sc"
EXEC = f"I-1-sc/P-2-sc/{EXEC_ID}/_"


def _row(execution_id: str, status: str, minute: int, **extra: Any) -> dict[str, Any]:
    return {
        "entry_type": "summary",
        "execution_id": execution_id,
        "plan_branch": PLAN,
        "status": status,
        "exit_code": 0 if status in ("success", "approved") else 1,
        "created_at": f"2026-11-20T01:{minute:02d}:00Z",
        **extra,
    }


CONSISTENT = SelfCheckInputs(
    bug_ev_drag=0.0,
    records=[
        _row(EXEC_ID, "success", 1),
        _row("REV-1", "failed", 2, summary="Reviewer ensemble container dispatch exited with code 1"),
        _row("REV-2", "approved", 3),
    ],
    execution_id=EXEC_ID,
    plan_branch=PLAN,
    markdown="# Plan Calibration Report: P-2-sc\n",
    counted_paths=("src/own.py",),
    target_only_paths=frozenset({"main/other.py"}),
    evaluated_is_calibration_commit=False,
    approved_head="abc1234",
    evaluated_sha="abc1234def5678",
    evaluated_reaches_approved=True,
)


def test_consistent_report_passes() -> None:
    assert self_check_report(CONSISTENT) == []


def test_drag_without_an_unresolved_execution_failure() -> None:
    assert self_check_report(dataclasses.replace(CONSISTENT, bug_ev_drag=80.0)) == ["drag_without_failure"]


def test_drag_with_an_unresolved_execution_failure_is_consistent() -> None:
    records = [_row(EXEC_ID, "success", 1), _row("E-4-sc", "failed", 2, summary="3 tests failed")]
    assert self_check_report(dataclasses.replace(CONSISTENT, bug_ev_drag=50.0, records=records)) == []


def test_drag_from_the_legacy_fallback_rows_is_consistent() -> None:
    # No execution row of the plan: the dual EV scores every execution row, so the self-check must see the same rows
    other_plan = {"plan_branch": "I-8-o/P-9-o/_"}
    records = [
        {**_row("E-7-other", "success", 1), **other_plan},
        _row("REV-1", "approved", 2),
        {**_row("E-9-other", "failed", 3, summary="3 tests failed"), **other_plan},
    ]
    metrics = evaluate_dual_ev_outcomes(records, EXEC_ID, PLAN, predicted_impact=50.0, cost_effort=5.0)
    assert metrics.bug_ev_drag > 0
    inputs = dataclasses.replace(CONSISTENT, bug_ev_drag=metrics.bug_ev_drag, records=records)
    assert self_check_report(inputs) == []


def test_counted_target_only_file() -> None:
    inputs = dataclasses.replace(CONSISTENT, counted_paths=("src/own.py", "main/other.py"))
    assert self_check_report(inputs) == ["counted_target_only_file"]


def test_evaluated_calibration_commit() -> None:
    inputs = dataclasses.replace(CONSISTENT, evaluated_is_calibration_commit=True)
    assert self_check_report(inputs) == ["evaluated_ledger_only"]


def test_evaluated_not_the_approved_head() -> None:
    inputs = dataclasses.replace(CONSISTENT, approved_head="fff0000", evaluated_reaches_approved=False)
    assert self_check_report(inputs) == ["evaluated_not_approved"]


def test_no_approved_head_skips_the_approval_condition() -> None:
    inputs = dataclasses.replace(CONSISTENT, approved_head=None, evaluated_reaches_approved=False)
    assert self_check_report(inputs) == []


def test_fallback_stub() -> None:
    inputs = dataclasses.replace(CONSISTENT, markdown=f"# Calibration Report for P-2-sc\n\n{FALLBACK_REPORT_MARKER}\n")
    assert self_check_report(inputs) == ["fallback_stub"]


def test_every_condition_is_named_in_order() -> None:
    inputs = dataclasses.replace(
        CONSISTENT,
        bug_ev_drag=1.0,
        counted_paths=("main/other.py",),
        evaluated_is_calibration_commit=True,
        evaluated_reaches_approved=False,
        approved_head="fff0000",
        markdown=FALLBACK_REPORT_MARKER,
    )
    violations = self_check_report(inputs)
    assert violations == [
        "drag_without_failure",
        "counted_target_only_file",
        "evaluated_ledger_only",
        "evaluated_not_approved",
        "fallback_stub",
    ]
    error = CalibrationSelfCheckError(violations)
    assert error.condition == "drag_without_failure"
    assert "fallback_stub" in str(error)


def test_same_commit_accepts_abbreviations() -> None:
    assert same_commit("ABC1234", "abc1234def")
    assert not same_commit("abc1234", "")
    assert not same_commit("abc1234", "abd1234")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def commit(repo: Path, message: str, files: dict[str, str]) -> str:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git(repo, "add", "--", *files)
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture(scope="session")
def flow_repo_template(tmp_path_factory: pytest.TempPathFactory, hermetic_git_config: str) -> Path:
    root, _ = shared_template(
        tmp_path_factory,
        "calibrate-self-check-flow-repo",
        lambda r: build_self_check_flow_repo(r, PLAN, EXEC, _row(EXEC_ID, "success", 1)),
    )
    return root


@pytest.fixture
def flow_repo(flow_repo_template: Path, tmp_path: Path) -> Path:
    return own_path_copy(flow_repo_template, tmp_path)


def test_target_only_paths_excludes_the_pr_own_files(flow_repo: Path) -> None:
    merge_base = git(flow_repo, "merge-base", "main", EXEC)
    paths = target_only_paths(str(flow_repo), f"refs/heads/{PLAN}", merge_base, git(flow_repo, "rev-parse", EXEC))
    assert paths == frozenset({"main/other.py"})


def test_reaches_through_metadata_and_merges_only(flow_repo: Path) -> None:
    approved = git(flow_repo, "rev-parse", EXEC)
    git(flow_repo, "checkout", "-q", EXEC)
    rows = commit(
        flow_repo, "chore(review): record review ledger rows", {"holon-knowledge/ledger/reviews.jsonl": "x\n"}
    )
    assert reaches_through_metadata(str(flow_repo), approved, rows)
    code = commit(flow_repo, "feat: after approval", {"src/late.py": "late\n"})
    assert not reaches_through_metadata(str(flow_repo), approved, code)
    assert not reaches_through_metadata(str(flow_repo), code, approved)


def test_run_calibrate_fails_closed_before_touching_the_repository(flow_repo: Path) -> None:
    head = git(flow_repo, "rev-parse", EXEC)
    pr_one = git(flow_repo, "rev-parse", f"{EXEC}~2")
    git(flow_repo, "checkout", "-q", "main")
    with pytest.raises(CalibrationSelfCheckError) as excinfo:
        run_calibrate(EXEC, target_worktree=str(flow_repo), approved_head=pr_one)
    assert excinfo.value.conditions == ("evaluated_not_approved",)
    assert git(flow_repo, "rev-parse", EXEC) == head
    assert git(flow_repo, "branch", "--list", "*calibrated") == ""
    assert git(flow_repo, "status", "--porcelain") == ""


def test_run_calibrate_passes_a_consistent_report(flow_repo: Path) -> None:
    head = git(flow_repo, "rev-parse", EXEC)
    git(flow_repo, "checkout", "-q", "main")
    report = run_calibrate(EXEC, target_worktree=str(flow_repo), skip_commit=True, approved_head=head)
    assert report.evaluated_commit_sha == head
    assert report.diff_base == "target_merge_base"
    assert report.changed_paths == ["src/own.py"]


@pytest.fixture
def adapted_merge_repo(tmp_path: Path) -> Path:
    """As ``flow_repo``, but main adds a file that the execution branch's merge commit itself adapts."""
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "base", {"base.txt": "base\n"})
    git(tmp_path, "checkout", "-qb", PLAN)
    commit(tmp_path, "plan", {"plans/I-1-sc/P-2-sc.md": "# Plan\n"})
    git(tmp_path, "checkout", "-qb", EXEC)
    commit(tmp_path, "pr one", {"src/own.py": "one\n"})
    git(tmp_path, "checkout", "-q", "main")
    commit(tmp_path, "main adds", {"main/added.py": "first\nsecond\n"})
    git(tmp_path, "checkout", "-q", EXEC)
    git(tmp_path, "merge", "-q", "--no-ff", "--no-commit", "main")
    (tmp_path / "main/added.py").write_text("first\nadapted\n", encoding="utf-8")
    git(tmp_path, "add", "--", "main/added.py")
    git(tmp_path, "commit", "-qm", "merge main, adapting main/added.py")
    ledger = json.dumps(_row(EXEC_ID, "success", 1)) + "\n"
    commit(tmp_path, "ledger", {"holon-knowledge/ledger/executions.jsonl": ledger})
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "refs/heads/main")
    # The adaptation lives inside the two-parent merge commit, not in a later commit
    assert len(git(tmp_path, "rev-list", "--parents", "-n1", "HEAD~1").split()) == 3
    assert "main/added.py" in git(tmp_path, "diff-tree", "--cc", "--name-only", "HEAD~1").splitlines()
    return tmp_path


def test_merge_commit_adapting_a_target_added_file_is_the_pr_own(adapted_merge_repo: Path) -> None:
    repo = adapted_merge_repo
    merge_base = git(repo, "merge-base", "main", EXEC)
    head = git(repo, "rev-parse", EXEC)
    assert "main/added.py" not in target_only_paths(str(repo), f"refs/heads/{PLAN}", merge_base, head)
    git(repo, "checkout", "-q", "main")
    report = run_calibrate(EXEC, target_worktree=str(repo), skip_commit=True, approved_head=head)
    assert "main/added.py" in report.changed_paths
    assert "src/own.py" in report.changed_paths


def test_file_changed_only_on_target_is_still_target_only(flow_repo: Path) -> None:
    merge_base = git(flow_repo, "merge-base", "main", EXEC)
    paths = target_only_paths(str(flow_repo), f"refs/heads/{PLAN}", merge_base, git(flow_repo, "rev-parse", EXEC))
    assert "main/other.py" in paths
    inputs = dataclasses.replace(CONSISTENT, counted_paths=("src/own.py", "main/other.py"), target_only_paths=paths)
    assert self_check_report(inputs) == ["counted_target_only_file"]


def test_clean_merge_without_adaptation_reports_no_violation(flow_repo: Path) -> None:
    merge_base = git(flow_repo, "merge-base", "main", EXEC)
    head = git(flow_repo, "rev-parse", EXEC)
    paths = target_only_paths(str(flow_repo), f"refs/heads/{PLAN}", merge_base, head)
    counted = tuple(git(flow_repo, "diff", "--name-only", f"{merge_base}...{head}").splitlines())
    assert paths.isdisjoint(counted)
    assert self_check_report(dataclasses.replace(CONSISTENT, counted_paths=counted, target_only_paths=paths)) == []
