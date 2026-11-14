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

import pytest
from sandbox_executor.calibration import generate_calibration

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
