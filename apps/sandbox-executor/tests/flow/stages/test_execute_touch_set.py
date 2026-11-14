"""Stage 3 touch-set admission and enforcement, end to end through the pipeline with fake role agents and fake gh."""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.budgets import MAX_REPLAN_ATTEMPTS
from sandbox_executor.flow import PipelineEngine, StageStatus, admission
from sandbox_executor.flow.admission import check_admission as REAL_CHECK

from tests.flow.helpers import FakeRoleAgents, _dispatch_context, _ledger_rows
from tests.flow.test_admission import no_worktrees
from tests.flow.test_in_flight import OTHER_HEAD, FakeGh


class TouchSetRoleAgents(FakeRoleAgents):
    """Fake agents whose planner records ``touch_sets`` (one per planner call, the last repeats) and whose executor
    also writes ``extra_files`` beside the usual change to src/app.py."""

    def __init__(self, root: Path, origin: Path, touch_sets: list[list[str]], extra_files: tuple[str, ...] = ()):
        super().__init__(root, origin)
        self.touch_sets = touch_sets
        self.extra_files = extra_files
        self.planner_calls = 0
        self.replan_feedback_seen: list[str | None] = []

    def _plan(self, work: Path, args: list[str], kwargs: dict[str, Any]) -> int:
        self.replan_feedback_seen.append(os.environ.get("HOLON_REPLAN_FEEDBACK"))
        self.clock_offset += 1  # keep each plan branch name unique
        exit_code = super()._plan(work, args, kwargs)
        self.planner_calls += 1
        return exit_code

    def _recorded(self, work: Path, row: dict[str, Any], plan_content: str) -> dict[str, Any]:
        paths = self.touch_sets[min(self.planner_calls, len(self.touch_sets) - 1)]
        return {**super()._recorded(work, row, plan_content), "touch_set": [{"path": path} for path in paths]}

    def _execute(self, work: Path, args: list[str], kwargs: dict[str, Any]) -> int:
        for rel in self.extra_files:
            (work / rel).parent.mkdir(parents=True, exist_ok=True)
            (work / rel).write_text("extra\n", encoding="utf-8")
        return super()._execute(work, args, kwargs)


@pytest.fixture
def fake_gh(monkeypatch: pytest.MonkeyPatch):
    """Real admission over a fake gh (no pull request until a test opens one) and no other worktree."""
    gh = FakeGh([], {})
    monkeypatch.setattr(admission, "check_admission", functools.partial(REAL_CHECK, run_gh=gh, run_git=no_worktrees))
    return gh


def open_overlapping_pr(gh: FakeGh) -> None:
    gh.prs.append({"number": 7, "headRefName": OTHER_HEAD, "baseRefName": "main"})
    gh.diffs[7] = ["src/app.py"]


def test_refusal_happens_before_the_executor_is_dispatched(flow_repos, tmp_path, fake_gh):
    host, origin = flow_repos
    open_overlapping_pr(fake_gh)
    agents = TouchSetRoleAgents(tmp_path, origin, [["src/"]])
    results = PipelineEngine(_dispatch_context(host, agents)).run()

    execute = results["execute"]
    assert execute.status == StageStatus.FAILED
    assert "executor" not in agents.roles()
    assert f"pull request #7 (branch {OTHER_HEAD}) on src/app.py" in str(execute.error)
    assert "--allow-overlap" in str(execute.error)
    assert execute.payload["admission_refused"] is True and execute.payload["dispatched"] is False


def test_allow_overlap_dispatches_once_and_records_one_override(flow_repos, tmp_path, fake_gh):
    host, origin = flow_repos
    open_overlapping_pr(fake_gh)
    agents = TouchSetRoleAgents(tmp_path, origin, [["src/"]])
    ctx = _dispatch_context(host, agents, allow_overlap=True)
    results = PipelineEngine(ctx).run()

    assert results["execute"].status == StageStatus.SUCCESS
    assert agents.roles().count("executor") == 1
    rows = _ledger_rows(host, f"origin/{ctx.plan_branch}", "executions.jsonl")
    overrides = [row for row in rows if row.get("kind") == "admission_override"]
    assert len(overrides) == 1
    assert overrides[0]["conflicts"] == [{"flow": f"pull request #7 (branch {OTHER_HEAD})", "files": ["src/app.py"]}]


def test_metadata_only_extras_stay_inside_the_touch_set(flow_repos, tmp_path, fake_gh):
    host, origin = flow_repos
    agents = TouchSetRoleAgents(tmp_path, origin, [["src/app.py"]], extra_files=("plans/notes.md",))
    results = PipelineEngine(_dispatch_context(host, agents)).run()
    assert results["execute"].status == StageStatus.SUCCESS


def test_out_of_set_diff_returns_to_stage_2_once_then_succeeds(flow_repos, tmp_path, fake_gh):
    host, origin = flow_repos
    agents = TouchSetRoleAgents(tmp_path, origin, [["src/app.py"], ["src/app.py", "lib/"]], ("lib/extra.py",))
    ctx = _dispatch_context(host, agents)
    results = PipelineEngine(ctx).run()

    assert results["execute"].status == StageStatus.SUCCESS
    assert ctx.replan_attempts == 1
    assert agents.roles() == ["intent-creator", "planner", "executor", "planner", "executor"]
    assert agents.replan_feedback_seen[0] is None
    assert "lib/extra.py" in str(agents.replan_feedback_seen[1]) and "Touch Set" in str(agents.replan_feedback_seen[1])
    assert any("outside the plan's touch set: lib/extra.py" in line for line in ctx.logs)


def test_persistent_violation_fails_clearly_after_the_replans(flow_repos, tmp_path, fake_gh):
    host, origin = flow_repos
    agents = TouchSetRoleAgents(tmp_path, origin, [["src/app.py"]], ("lib/extra.py",))
    ctx = _dispatch_context(host, agents)
    results = PipelineEngine(ctx).run()

    execute = results["execute"]
    assert execute.status == StageStatus.FAILED
    assert agents.roles().count("executor") == 1 + MAX_REPLAN_ATTEMPTS
    assert execute.error == (
        "Execute stage failed: the execution changed files outside the plan's touch set (lib/extra.py) after "
        f"{MAX_REPLAN_ATTEMPTS} re-plan attempts."
    )
    assert execute.payload["touch_set_violation"] == ["lib/extra.py"] and execute.payload["replan_requested"] is True
    rows = _ledger_rows(host, f"origin/{ctx.execution_branch}", "executions.jsonl")
    assert rows[-1]["failure_reason"] == "touch_set_violation" and rows[-1]["status"] == "failed"
    assert rows[-1]["ledger_revision"] == 2
