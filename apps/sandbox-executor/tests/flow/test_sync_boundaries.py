"""Hermetic tests for ``sync_with_target`` wired across Stage 1-3 boundaries, turns, and prompt handoffs."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor.entrypoint.sync_conflict import SYNC_CONFLICT_STATUS
from sandbox_executor.flow import (
    FlowContext,
    FlowStage,
    StageStatus,
    at_flow_start,
    at_stage_boundary,
    at_turn_boundary,
    consume_target_merge_notice,
    format_target_merge_notice,
    sync_conflict_result,
)
from sandbox_executor.flow.target_sync_result import SyncBoundary
from sandbox_executor.ledger import read_merged_rows_at_ref

from tests.flow.helpers import _git
from tests.flow.sync_boundary_repos import (
    APP,
    CLEAN_TARGET_MOVE,
    CODE_BASE,
    FLOW_CODE,
    LEDGER,
    TARGET_CODE,
    ledger_conflict,
    sync_conflict_commit,
    sync_work,
)
from tests.flow.sync_boundary_repos import BASE_ROW as BASE_ROW  # re-exported for review/test_sync_points.py
from tests.ledger_conflict_repos import FLOW_BRANCH, commit_on

Edit = Callable[[Path], None]


def _resolve_app(work: Path) -> None:
    (work / APP).write_text("x = 1\ny = 2\n", encoding="utf-8")


class FakeResolverDispatcher:
    """Fake role dispatcher for ``conflict-resolver`` attempts."""

    def __init__(self, script: list[tuple[Edit | None, str]]) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    def __call__(self, role: str, runner: str, model: str, positional_args: list[str], **kwargs: object) -> int:
        work = Path(str(kwargs["target_worktree"]))
        result_path = Path(str(kwargs["result_file"]))
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        self.calls.append({"role": role, "model": model, "tier": positional_args[2], "payload": payload})
        edit, verdict = self.script.pop(0)
        if edit is not None:
            edit(work)
        failing = ["tests/test_app.py::test_x"] if verdict == "tests_failed" else []
        result_path.write_text(
            json.dumps({"verdict": verdict, "reason": f"{verdict} reason", "failing_tests": failing}),
            encoding="utf-8",
        )
        return 0


def _sync_rows(work: Path, branch: str = FLOW_BRANCH) -> list[dict]:
    rows = read_merged_rows_at_ref(str(work), f"origin/{branch}", "executions.jsonl")
    return [row for row in rows if row.get("action") == "target_sync"]


def _inject_ledger_conflict(work: Path, branch: str, idx: int) -> None:
    """A newer row on ``main`` and an older one on ``branch``, appended to what each holds now (also used by
    review/test_sync_points.py on repositories whose ledgers have moved on); fixed scenarios use
    ``sync_boundary_repos.ledger_conflict`` instead."""
    main_row = json.dumps({"execution_id": f"E-main-{idx}", "created_at": f"2026-11-0{idx}T02:00:00Z"}) + "\n"
    flow_row = json.dumps({"execution_id": f"E-flow-{idx}", "created_at": f"2026-11-0{idx}T01:00:00Z"}) + "\n"
    main_current = _git(work, "show", f"origin/main:{LEDGER}") + "\n"
    flow_current = _git(work, "show", f"origin/{branch}:{LEDGER}") + "\n"
    commit_on(work, "main", {LEDGER: main_current + main_row}, f"main row {idx}")
    commit_on(work, branch, {LEDGER: flow_current + flow_row}, f"flow row {idx}")


def _context_for(work: Path, stage: FlowStage, dispatcher: FakeResolverDispatcher | None = None) -> FlowContext:
    ctx = FlowContext(
        target_worktree=str(work),
        intent_data={"intent_id": "I-1", "slug": "x", "target_branch": "main"},
        agent="claude-agent",
        model="claude-opus-5-5",
        token_reduce=False,
        dispatcher=dispatcher,
    )
    if stage == FlowStage.INTENT:
        ctx.intent_branch = FLOW_BRANCH
    elif stage == FlowStage.PLAN:
        ctx.intent_branch = "I-1-x/_"
        ctx.plan_branch = FLOW_BRANCH
    else:
        ctx.intent_branch = "I-1-x/_"
        ctx.plan_branch = "I-1-x/P-2-y/_"
        ctx.execution_branch = FLOW_BRANCH
    return ctx


# The code branches on the FLOW_START boundary only (``at_flow_start``); the stage and the other two boundaries are
# data written into the recorded row. Three cases keep both code paths and round-trip every stage and every boundary.
@pytest.mark.parametrize(
    ("boundary", "stage"),
    [
        (SyncBoundary.FLOW_START, FlowStage.EXECUTE),
        (SyncBoundary.STAGE_START, FlowStage.PLAN),
        (SyncBoundary.STAGE_END, FlowStage.INTENT),
    ],
)
def test_stage_and_flow_start_boundaries_resolve_injected_conflicts(
    tmp_path_factory: pytest.TempPathFactory, tmp_path: Path, stage: FlowStage, boundary: SyncBoundary
) -> None:
    work = sync_work(tmp_path_factory, tmp_path, ledger_conflict(2))
    ctx = _context_for(work, stage)

    if boundary is SyncBoundary.FLOW_START:
        failure = at_flow_start(ctx, stage)
    else:
        failure = at_stage_boundary(ctx, stage, boundary)

    assert failure is None
    rows = _sync_rows(work)
    assert len(rows) == 1
    assert rows[0]["stage"] == stage.value
    assert rows[0]["boundary"] == boundary.value
    assert rows[0]["outcome"] == "success"
    notice = consume_target_merge_notice(ctx)
    assert "## Target branch merged" in notice
    assert rows[0]["target_sha"] in notice
    assert f"`{LEDGER}`" in notice


@pytest.mark.parametrize("stage", [FlowStage.PLAN, FlowStage.EXECUTE])
def test_non_conflicting_target_move_at_turn_boundary_is_deferred(
    tmp_path_factory: pytest.TempPathFactory, tmp_path: Path, stage: FlowStage
) -> None:
    work = sync_work(tmp_path_factory, tmp_path, (CLEAN_TARGET_MOVE,))
    head_before = _git(work, "rev-parse", f"origin/{FLOW_BRANCH}")
    ctx = _context_for(work, stage)

    failure = at_turn_boundary(ctx, stage, turn_index=1)

    assert failure is None
    assert _git(work, "rev-parse", f"origin/{FLOW_BRANCH}") == head_before
    assert _sync_rows(work) == []
    assert consume_target_merge_notice(ctx) == ""

    # At STAGE_END, the deferred clean target move is merged and recorded.
    end_failure = at_stage_boundary(ctx, stage, SyncBoundary.STAGE_END)
    assert end_failure is None
    assert _git(work, "rev-parse", f"origin/{FLOW_BRANCH}") != head_before
    rows = _sync_rows(work)
    assert len(rows) == 1 and rows[0]["boundary"] == SyncBoundary.STAGE_END.value
    notice = consume_target_merge_notice(ctx)
    assert "`docs/clean.md`" in notice


@pytest.mark.parametrize("stage", [FlowStage.PLAN, FlowStage.EXECUTE])
def test_conflicting_target_move_at_turn_boundary_merges_and_sets_prompt_notice(
    tmp_path_factory: pytest.TempPathFactory, tmp_path: Path, stage: FlowStage
) -> None:
    work = sync_work(tmp_path_factory, tmp_path, ledger_conflict(3))
    ctx = _context_for(work, stage)

    failure = at_turn_boundary(ctx, stage, turn_index=1)

    assert failure is None
    rows = _sync_rows(work)
    assert len(rows) == 1
    assert rows[0]["stage"] == stage.value
    assert rows[0]["boundary"] == SyncBoundary.TURN.value
    notice = consume_target_merge_notice(ctx)
    assert "## Target branch merged" in notice
    assert rows[0]["target_sha"] in notice
    assert f"`{LEDGER}`" in notice
    assert consume_target_merge_notice(ctx) == ""


@pytest.mark.parametrize("stage", [FlowStage.PLAN, FlowStage.EXECUTE])
def test_code_conflict_at_stage_boundary_resolves_through_conflict_resolver_role(
    tmp_path_factory: pytest.TempPathFactory, tmp_path: Path, stage: FlowStage
) -> None:
    work = sync_work(tmp_path_factory, tmp_path, (TARGET_CODE, FLOW_CODE), CODE_BASE)
    dispatcher = FakeResolverDispatcher([(_resolve_app, "resolved")])
    ctx = _context_for(work, stage, dispatcher=dispatcher)

    failure = at_stage_boundary(ctx, stage, SyncBoundary.STAGE_START)

    assert failure is None
    assert len(dispatcher.calls) == 1
    assert dispatcher.calls[0]["role"] == "conflict-resolver"
    assert _git(work, "show", f"origin/{FLOW_BRANCH}:{APP}") == "x = 1\ny = 2"
    rows = _sync_rows(work)
    assert len(rows) == 1 and rows[0]["outcome"] == "success"
    notice = consume_target_merge_notice(ctx)
    assert f"`{APP}`" in notice


@pytest.mark.parametrize("stage", [FlowStage.PLAN, FlowStage.EXECUTE])
def test_unresolvable_code_conflict_fails_stage_closed(
    tmp_path_factory: pytest.TempPathFactory, tmp_path: Path, stage: FlowStage
) -> None:
    work = sync_work(tmp_path_factory, tmp_path, (TARGET_CODE, FLOW_CODE), CODE_BASE)
    dispatcher = FakeResolverDispatcher([(None, "cannot_decide"), (None, "cannot_decide"), (None, "cannot_decide")])
    ctx = _context_for(work, stage, dispatcher=dispatcher)

    failure = at_stage_boundary(ctx, stage, SyncBoundary.STAGE_START)

    assert failure is not None
    assert failure.status == StageStatus.FAILED
    assert APP in (failure.error or "")
    assert failure.payload["conflicted_paths"] == [APP]


def test_stage3_sync_conflict_row_triggers_host_sync_and_records_supersedes(
    tmp_path_factory: pytest.TempPathFactory, tmp_path: Path
) -> None:
    conflict_row = {
        "entry_type": "summary",
        "execution_id": "E-3-z",
        "status": SYNC_CONFLICT_STATUS,
        "conflicted_paths": [APP],
        "ledger_revision": 2,
    }
    work = sync_work(tmp_path_factory, tmp_path, (TARGET_CODE, sync_conflict_commit(conflict_row)), CODE_BASE)
    dispatcher = FakeResolverDispatcher([(_resolve_app, "resolved")])
    ctx = _context_for(work, FlowStage.EXECUTE, dispatcher=dispatcher)
    base_payload = {"execution_id": "E-3-z", "execution_branch": FLOW_BRANCH, "status": SYNC_CONFLICT_STATUS}

    outcome = sync_conflict_result(ctx, "2026-11-23T00:00:00Z", conflict_row, base_payload)

    assert outcome is None
    assert conflict_row["status"] == "success"
    assert conflict_row["ledger_revision"] == 3
    assert base_payload["status"] == "success"
    assert base_payload["sync_conflict_resolved"] is True
    rows = _sync_rows(work)
    assert len(rows) == 1
    assert rows[0]["supersedes"] == "E-3-z@r2"
    assert rows[0]["outcome"] == "success"
    ledger_lines = read_merged_rows_at_ref(str(work), f"origin/{FLOW_BRANCH}", "executions.jsonl")
    summaries = [r for r in ledger_lines if r.get("entry_type") == "summary" and r.get("execution_id") == "E-3-z"]
    assert summaries[-1]["status"] == "success"
    assert summaries[-1]["ledger_revision"] == 3


def test_stage3_sync_conflict_row_fails_closed_when_host_sync_is_blocked(
    tmp_path_factory: pytest.TempPathFactory, tmp_path: Path
) -> None:
    conflict_row = {
        "entry_type": "summary",
        "execution_id": "E-3-z",
        "status": SYNC_CONFLICT_STATUS,
        "conflicted_paths": [APP],
        "ledger_revision": 2,
    }
    work = sync_work(tmp_path_factory, tmp_path, (TARGET_CODE, sync_conflict_commit(conflict_row)), CODE_BASE)
    dispatcher = FakeResolverDispatcher([(None, "cannot_decide"), (None, "cannot_decide"), (None, "cannot_decide")])
    ctx = _context_for(work, FlowStage.EXECUTE, dispatcher=dispatcher)
    base_payload = {"execution_id": "E-3-z", "execution_branch": FLOW_BRANCH, "status": SYNC_CONFLICT_STATUS}

    outcome = sync_conflict_result(ctx, "2026-11-23T00:00:00Z", conflict_row, base_payload)

    assert outcome is not None
    assert outcome.status == StageStatus.FAILED
    assert outcome.payload["sync_conflict"] is True
    assert outcome.payload["conflicted_paths"] == [APP]
    assert ctx.stage_results["execute"] is outcome


def test_target_merge_notice_reaches_planner_and_executor_turn_prompts(tmp_path: Path) -> None:
    from sandbox_executor.artefacts import PLAN_SPEC
    from sandbox_executor.budgets import ENV_REPLAN_FEEDBACK, StageTimer, TurnBudgets
    from sandbox_executor.turns import TurnResult, supervise_authoring_turn

    notice = format_target_merge_notice("abc123456789", ["src/app.py", "docs/readme.md"])
    prompts: list[str] = []
    plan_file = tmp_path / "P-1.md"

    def fake_run(cmd: list[str], **kwargs: object) -> TurnResult:
        prompts.append(cmd[-1])
        if len(prompts) == 1:
            plan_file.write_text("# Plan\n\n## Step 1\nPartial\n", encoding="utf-8")
        else:
            plan_file.write_text(
                "# Plan\n\n## Overall Plan Metrics\n\n| metric | value |\n| --- | --- |\n"
                "| p_success_pred | 0.9 |\n| entropy_pred | 1.0 |\n| impact_pred | 10.0 |\n"
                "| cost_pred | 1.0 |\n| learning_value_pred | 1.0 |\n| ev_pred | 8.0 |\n\n"
                "### Strategy Rationale\n\nDone.\n\n## Step 1\nDone.\n",
                encoding="utf-8",
            )
        return TurnResult(returncode=0)

    with patch.dict(os.environ, {ENV_REPLAN_FEEDBACK: notice}):
        supervise_authoring_turn(
            lambda text: ["agent", text],
            "Initial prompt",
            artefact_path=str(plan_file),
            artefact_rel="plans/P-1.md",
            spec=PLAN_SPEC,
            stage="plan",
            turn_budgets=TurnBudgets(),
            stage_timer=StageTimer("plan", 600.0),
            run=fake_run,
            on_turn_boundary=lambda _idx: os.environ.pop(ENV_REPLAN_FEEDBACK, "").strip() or None,
        )

    assert len(prompts) == 2
    assert "## Target branch merged" in prompts[1]
    assert "`abc123456789`" in prompts[1]
    assert "`src/app.py`" in prompts[1]
    assert "`docs/readme.md`" in prompts[1]


def test_make_turn_boundary_hook_merges_conflict_and_returns_notice(
    tmp_path_factory: pytest.TempPathFactory, tmp_path: Path
) -> None:
    from sandbox_executor.flow import make_turn_boundary_hook

    work = sync_work(tmp_path_factory, tmp_path, ledger_conflict(4))
    hook = make_turn_boundary_hook(str(work), FLOW_BRANCH, "main", FlowStage.PLAN)

    notice = hook(1)

    assert notice is not None
    assert "## Target branch merged" in notice
    assert f"`{LEDGER}`" in notice
    assert hook(2) is None


def test_make_turn_boundary_hook_raises_on_blocked_sync_and_skips_in_container(
    tmp_path_factory: pytest.TempPathFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sandbox_executor.flow.target_sync_hooks import TurnBoundarySyncError, make_turn_boundary_hook

    work = sync_work(tmp_path_factory, tmp_path, (TARGET_CODE, FLOW_CODE), CODE_BASE)
    dispatcher = FakeResolverDispatcher([(None, "cannot_decide"), (None, "cannot_decide"), (None, "cannot_decide")])
    ctx = _context_for(work, FlowStage.PLAN, dispatcher=dispatcher)
    hook = make_turn_boundary_hook(str(work), FLOW_BRANCH, "main", FlowStage.PLAN, context=ctx)

    monkeypatch.setattr("sandbox_executor.flow.target_sync_hooks.workspace_mounted", lambda: True)
    assert hook(1) is None
    assert dispatcher.calls == []

    monkeypatch.setattr("sandbox_executor.flow.target_sync_hooks.workspace_mounted", lambda: False)
    with pytest.raises(TurnBoundarySyncError) as exc_info:
        hook(1)
    assert exc_info.value.failure.status == StageStatus.FAILED
    assert exc_info.value.failure.payload["conflicted_paths"] == [APP]


def test_checkout_active_branch_preserves_unpushed_local_commits(
    tmp_path_factory: pytest.TempPathFactory, tmp_path: Path
) -> None:
    from sandbox_executor.flow.target_sync_hooks import _checkout_active_branch

    work = sync_work(tmp_path_factory, tmp_path)
    _git(work, "checkout", "-q", FLOW_BRANCH)
    (work / "local_only.txt").write_text("unpushed\n", encoding="utf-8")
    _git(work, "add", "local_only.txt")
    _git(work, "commit", "-q", "-m", "local blocked row commit")
    local_sha = _git(work, "rev-parse", f"refs/heads/{FLOW_BRANCH}")
    _git(work, "checkout", "-q", "main")

    ctx = _context_for(work, FlowStage.PLAN)
    prev, detached, err = _checkout_active_branch(ctx, FLOW_BRANCH)

    assert err is None and prev == "main" and not detached
    assert _git(work, "rev-parse", "HEAD") == local_sha
    assert _git(work, "rev-parse", f"refs/heads/{FLOW_BRANCH}") == local_sha
    assert (work / "local_only.txt").exists()


def test_child_turn_boundary_syncs_plan_branch_and_propagates_notice(
    tmp_path_factory: pytest.TempPathFactory, tmp_path: Path
) -> None:
    from sandbox_executor.flow.stages.children import _child_context, _sync_sub_intent_plan_branch

    work = sync_work(tmp_path_factory, tmp_path, ledger_conflict(5))
    ctx = FlowContext(
        target_worktree=str(work),
        intent_data={"intent_id": "I-1", "slug": "x", "target_branch": "main"},
        plan_branch=FLOW_BRANCH,
        execution_branch="I-1-x/P-2-y/E-decomposed/_",
        token_reduce=False,
    )

    err = _sync_sub_intent_plan_branch(ctx, {"plan_branch": FLOW_BRANCH}, 2)

    assert err is None
    assert ctx.execution_branch == "I-1-x/P-2-y/E-decomposed/_"
    child = _child_context(ctx, {"slug": "sub", "target_branch": FLOW_BRANCH})
    notice = consume_target_merge_notice(child)
    assert "## Target branch merged" in notice
    assert f"`{LEDGER}`" in notice
    assert consume_target_merge_notice(ctx) == ""


def test_stage5_resume_flow_start_syncs_execution_branch_not_calibrated(
    tmp_path_factory: pytest.TempPathFactory, tmp_path: Path
) -> None:
    from sandbox_executor.flow.calibrate_sync import prepare_calibrated_branch
    from sandbox_executor.flow.target_sync_hooks import active_flow_branch
    from sandbox_executor.ref_names import calibration_branch

    work = sync_work(tmp_path_factory, tmp_path)
    calibrated = calibration_branch(FLOW_BRANCH)
    exec_before = _git(work, "rev-parse", f"origin/{FLOW_BRANCH}")
    _git(work, "branch", "--no-track", calibrated, exec_before)
    _git(work, "push", "-q", "origin", calibrated)
    _inject_ledger_conflict(work, FLOW_BRANCH, 6)

    ctx = _context_for(work, FlowStage.CALIBRATE)
    ctx.calibrated_branch = calibrated

    assert active_flow_branch(ctx, FlowStage.CALIBRATE) == FLOW_BRANCH
    assert at_flow_start(ctx, FlowStage.CALIBRATE) is None
    assert _git(work, "rev-parse", f"refs/heads/{calibrated}") == exec_before
    exec_after = _git(work, "rev-parse", f"origin/{FLOW_BRANCH}")
    assert exec_after != exec_before
    assert prepare_calibrated_branch(ctx, exec_after) is True
    assert _git(work, "rev-parse", f"refs/heads/{calibrated}") == exec_after
