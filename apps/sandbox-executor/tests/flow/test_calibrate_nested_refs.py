"""Stage 5 calibration on a nested sub-intent branch layout: local bare origin only, no network, no model call.

A decomposed flow calibrates a child execution branch such as ``I-1-root--1-child/P-2-ab-a-m/E-3-cd-a-m/_``. The
patch-size diff compares the child plan branch with that execution branch, so a plan ref git cannot resolve must
never reach ``git diff`` as a bare name (git then reports exit 128 "ambiguous argument").
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.calibration import PredictedMetrics, generate_calibration, parse_actual_metrics
from sandbox_executor.flow import FlowContext, StageStatus, run_calibrate_stage
from sandbox_executor.ref_names import calibration_branch

ROOT_INTENT = "I-1794000000-root/_"
CHILD_INTENT = "I-1794000000-root--1-child/_"
PLAN_ID = "P-1794000001-ab12cd34-a-m"
EXEC_ID = "E-1794000002-ef56ab78-a-m"
PLAN = f"I-1794000000-root--1-child/{PLAN_ID}/_"
EXEC = f"I-1794000000-root--1-child/{PLAN_ID}/{EXEC_ID}/_"


def git(cwd: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return res.stdout.strip()


def commit_file(repo: Path, rel: str, text: str) -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(repo, "add", rel)
    git(repo, "commit", "-qm", f"add {rel}")
    return git(repo, "rev-parse", "HEAD")


def _summary_row() -> str:
    row = {"entry_type": "summary", "execution_id": EXEC_ID, "plan_branch": PLAN, "exit_code": 0, "duration": 12.0}
    return json.dumps(row) + "\n"


def _plan_markdown() -> str:
    return (
        f"# Plan\n\n- **Plan ID:** {PLAN_ID}\n\n## Overall Plan Metrics\n\n| metric | value |\n| --- | --- |\n"
        "| p_success_pred | 0.8 |\n| entropy_pred | 2.0 |\n| impact_pred | 40.0 |\n| cost_pred | 5.0 |\n"
        "| learning_value_pred | 2.0 |\n"
    )


def make_nested_layout(tmp_path: Path) -> SimpleNamespace:
    """A bare origin and a host clone holding root intent, child intent, child plan and child execution branches."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    host = tmp_path / "host"
    git(tmp_path, "clone", "-q", str(origin), str(host))
    git(host, "checkout", "-qb", "main")
    commit_file(host, "holon-knowledge/ledger/executions.jsonl", "")
    for branch, rel in (
        (ROOT_INTENT, "intents/root.md"),
        (CHILD_INTENT, "intents/child.md"),
        (PLAN, f"plans/{PLAN_ID}.md"),
    ):
        git(host, "checkout", "-qb", branch)
        commit_file(host, rel, _plan_markdown() if rel.startswith("plans/") else "intent\n")
    git(host, "checkout", "-qb", EXEC)
    commit_file(host, "holon-knowledge/ledger/executions.jsonl", _summary_row())
    commit_file(host, "src/child.py", "print('child')\nprint('nested')\n")
    git(host, "push", "-q", "origin", "main", ROOT_INTENT, CHILD_INTENT, PLAN, EXEC)
    git(host, "checkout", "-q", "main")
    return SimpleNamespace(origin=origin, host=host)


def drop_plan_ref(layout: SimpleNamespace, case: str) -> None:
    """Remove the child plan branch: locally only (``remote_only``) or locally and on origin (``absent``)."""
    if case == "local":
        return
    git(layout.host, "branch", "-D", PLAN)
    git(layout.host, "update-ref", "-d", f"refs/remotes/origin/{PLAN}")
    if case == "absent":
        git(layout.host, "push", "-q", "origin", "--delete", PLAN)


@pytest.fixture
def layout(tmp_path: Path) -> SimpleNamespace:
    return make_nested_layout(tmp_path)


@pytest.mark.parametrize(
    ("case", "diff_base"),
    [("local", "plan"), ("remote_only", "plan"), ("absent", "first_parent_fallback")],
)
def test_generate_calibration_on_nested_layout(layout: SimpleNamespace, case: str, diff_base: str) -> None:
    drop_plan_ref(layout, case)
    report = generate_calibration(EXEC, target_worktree=str(layout.host))
    assert report.plan_id == PLAN_ID
    assert report.execution_id == EXEC_ID
    assert report.actual.files_changed == 1
    assert report.actual.insertions == 2
    assert ("first_parent_fallback" in report.markdown_content) is (diff_base == "first_parent_fallback")
    if diff_base == "plan":
        assert report.base_sha == git(layout.host, "rev-parse", f"origin/{PLAN}")


def test_absent_plan_ref_degrades_with_a_warning(layout: SimpleNamespace, caplog: pytest.LogCaptureFixture) -> None:
    drop_plan_ref(layout, "absent")
    with caplog.at_level("WARNING"):
        report = generate_calibration(EXEC, target_worktree=str(layout.host))
    assert report.base_sha is None
    assert any(PLAN in rec.getMessage() and "first parent" in rec.getMessage() for rec in caplog.records)


@pytest.mark.parametrize(
    ("plan_ref", "diff_base"),
    [("I-1-a/P-2-b/_", "plan"), ("I-9-gone/P-9-gone/_", "first_parent_fallback")],
)
def test_parse_actual_metrics_diff_argv_ends_with_a_separator(plan_ref: str, diff_base: str) -> None:
    calls: list[list[str]] = []

    def fake_git(args, **_kwargs):
        calls.append(list(args))
        verifies = args[:2] == ["rev-parse", "--verify"] and "gone" not in args[-1]
        if args[:2] == ["rev-parse", "--verify"]:
            return MagicMock(returncode=0 if verifies else 1, stdout=f"{'c' * 40}\n", stderr="")
        return MagicMock(returncode=0, stdout="1\n" if args[0] in ("rev-list", "merge-base") else "", stderr="")

    with (
        patch("sandbox_executor.calibration.run_git", side_effect=fake_git),
        patch("sandbox_executor.calibration_diff.run_git", side_effect=fake_git),
        patch("sandbox_executor.git_revisions.run_git", side_effect=fake_git),
        patch("sandbox_executor.calibration.read_git_file", return_value=None),
        patch("os.path.exists", return_value=False),
    ):
        _actual, meta = parse_actual_metrics(EXEC_ID, plan_ref, PredictedMetrics(), execution_branch=EXEC)
    diff = next(c for c in calls if c[0] == "diff")
    assert diff[-1] == "--"
    assert meta["diff_base"] == diff_base
    if diff_base == "plan":
        assert diff[2] == f"refs/heads/{plan_ref}...refs/heads/{EXEC}"
        assert ["rev-list", "--count", "--merges", f"refs/heads/{plan_ref}..refs/heads/{EXEC}", "--"] in calls
    else:
        assert diff[2] == f"refs/heads/{EXEC}~1..refs/heads/{EXEC}"
        assert not [c for c in calls if c[0] == "merge-base"]


@pytest.mark.parametrize("case", ["local", "absent"])
def test_stage_5_commits_the_report_on_a_nested_layout(layout: SimpleNamespace, case: str) -> None:
    drop_plan_ref(layout, case)
    git(layout.host, "checkout", "-qb", "operator-work", "main")
    ctx = FlowContext(target_worktree=str(layout.host), execution_branch=EXEC, agent="a", model="m", skip_push=True)
    with (
        patch("sandbox_executor.flow.stages.calibrate.check_and_resolve_stage_conflicts"),
        patch("sandbox_executor.flow.stages.calibrate.check_final_head_ci", return_value={"state": "success"}),
    ):
        result = run_calibrate_stage(ctx)
    assert result.status == StageStatus.SUCCESS, result.error
    calibrated = result.payload["calibrated_branch"]
    assert calibrated == calibration_branch(EXEC)
    assert calibrated.endswith("/calibrated")
    report_rel = f"plans/{PLAN_ID}_calibration.md"
    subjects = git(layout.host, "log", "--format=%s", f"refs/heads/{EXEC}").splitlines()
    assert f"chore(calibration): add calibration report for {PLAN_ID}" in subjects
    report = git(layout.host, "show", f"refs/heads/{EXEC}:{report_rel}")
    assert "Fallback calibration generated" not in report
    assert git(layout.host, "rev-parse", f"refs/heads/{EXEC}") == git(
        layout.host, "rev-parse", f"refs/heads/{calibrated}"
    )
    assert git(layout.host, "symbolic-ref", "--short", "HEAD") == "operator-work"
