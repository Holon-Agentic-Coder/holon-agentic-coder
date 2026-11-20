"""Stage 3 refuses an unauthorised change to the frozen module budget configuration (``module_budget_change_refused``).

Unit tests call the guard directly with a stubbed touch set and ledger writer; the end-to-end test runs the pipeline
with fake role agents whose executor edits the configuration.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.flow import FlowContext, PipelineEngine, StageStatus, admission
from sandbox_executor.flow.admission import check_admission as REAL_CHECK
from sandbox_executor.flow.stages import touch_set_guard
from sandbox_executor.module_budget_guard import MODULE_BUDGET_CHANGE_REFUSED, MODULE_BUDGET_CONFIG
from sandbox_executor.touch_set import TouchSet, TouchSetEntry

from tests.flow.helpers import FakeGh, _dispatch_context, _ledger_rows, no_worktrees
from tests.flow.stages.test_execute_touch_set import TouchSetRoleAgents

AUTHORISED = {"module_budget_change": "Maintainer, 2026-11-20: raise pipeline.py to 440 lines."}
SUMMARY_ROW = {"entry_type": "summary", "execution_id": "E-1-x", "status": "success"}
BASE_PAYLOAD = {"execution_branch": "I-100-own/P-101-x/E-102-y/_", "status": "success"}


@pytest.fixture
def no_other_flows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real admission over a fake gh with no pull request and no other worktree."""
    monkeypatch.setattr(
        admission, "check_admission", functools.partial(REAL_CHECK, run_gh=FakeGh([], {}), run_git=no_worktrees)
    )


@pytest.fixture
def written_rows(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    monkeypatch.setattr(touch_set_guard, "_append_superseding_execution_row", lambda ctx, br, row: rows.append(row))
    return rows


def _guard(monkeypatch: pytest.MonkeyPatch, declared: TouchSet | None, intent_data: dict, changed: list[str]):
    monkeypatch.setattr(touch_set_guard, "read_plan_touch_set", lambda worktree, plan_branch: declared)
    context = FlowContext(target_worktree=".", plan_branch="I-100-own/P-101-x/_", intent_data=intent_data)
    return touch_set_guard.touch_set_violation_result(
        context, "2026-11-20T00:00:00Z", SUMMARY_ROW, changed, BASE_PAYLOAD
    )


DECLARED = TouchSet((TouchSetEntry("src/app.py"), TouchSetEntry(MODULE_BUDGET_CONFIG)))


@pytest.mark.parametrize("declared", [DECLARED, None], ids=["declared-touch-set", "no-touch-set"])
def test_an_unauthorised_config_change_is_refused_without_a_replan(monkeypatch, written_rows, declared):
    result = _guard(monkeypatch, declared, {}, ["src/app.py", MODULE_BUDGET_CONFIG])
    assert result is not None and result.status == StageStatus.FAILED
    assert result.payload["failure_reason"] == MODULE_BUDGET_CHANGE_REFUSED
    assert result.payload[MODULE_BUDGET_CHANGE_REFUSED] == [MODULE_BUDGET_CONFIG]
    assert "replan_requested" not in result.payload
    assert "split the module" in str(result.error)
    assert [row["failure_reason"] for row in written_rows] == [MODULE_BUDGET_CHANGE_REFUSED]


def test_an_authorised_config_change_goes_on_to_the_touch_set_check(monkeypatch, written_rows):
    assert _guard(monkeypatch, DECLARED, AUTHORISED, ["src/app.py", MODULE_BUDGET_CONFIG]) is None
    narrow = TouchSet((TouchSetEntry("src/app.py"),))
    result = _guard(monkeypatch, narrow, AUTHORISED, ["src/app.py", MODULE_BUDGET_CONFIG])
    assert result is not None and result.payload["failure_reason"] == "touch_set_violation"
    assert result.payload["replan_requested"] is True


def test_a_diff_that_leaves_the_config_alone_is_unchanged(monkeypatch, written_rows):
    assert _guard(monkeypatch, DECLARED, {}, ["src/app.py"]) is None
    assert written_rows == []


def test_the_pipeline_refuses_an_execution_that_edits_the_budget_configuration(
    flow_repos, tmp_path: Path, no_other_flows
):
    host, origin = flow_repos
    agents = TouchSetRoleAgents(tmp_path, origin, [["src/app.py", MODULE_BUDGET_CONFIG]], (MODULE_BUDGET_CONFIG,))
    ctx = _dispatch_context(host, agents)
    results = PipelineEngine(ctx).run()

    execute = results["execute"]
    assert execute.status == StageStatus.FAILED
    assert execute.payload["failure_reason"] == MODULE_BUDGET_CHANGE_REFUSED
    assert agents.roles().count("executor") == 1
    assert ctx.replan_attempts == 0
    rows = _ledger_rows(host, f"origin/{ctx.execution_branch}", "executions.jsonl")
    assert rows[-1]["failure_reason"] == MODULE_BUDGET_CHANGE_REFUSED
