from __future__ import annotations

from unittest.mock import patch

from sandbox_executor.cli.commands.flow import _report_review_outcome
from sandbox_executor.flow import (
    EndOfFlowSeams,
    FlowContext,
    FlowStage,
    PipelineEngine,
    StageResult,
    StageStatus,
    SyncBoundary,
    SyncRequest,
    SyncResult,
    finish_flow,
    load_checkpoint,
    sync_row,
)
from sandbox_executor.flow.end_of_flow_sync import MAX_END_OF_FLOW_REENTRIES, MAX_UNKNOWN_MERGEABILITY_PROBES

_CLEAN = {"mergeable": "MERGEABLE", "merge_state_status": "CLEAN", "is_dirty": False}
_DIRTY = {"mergeable": "CONFLICTING", "merge_state_status": "DIRTY", "is_dirty": True}
_UNKNOWN = {"mergeable": "UNKNOWN", "merge_state_status": "UNKNOWN", "is_dirty": False}
_MISSING = {"mergeable": "UNKNOWN", "merge_state_status": "MISSING_REF", "is_dirty": False}


def _approved_review() -> StageResult:
    return StageResult(
        stage=FlowStage.REVIEW,
        status=StageStatus.APPROVED,
        payload={"consensus": {"approved": True, "iterations": 1}, "approved": True},
    )


def _make_context(tmp_path) -> FlowContext:
    return FlowContext(
        repo_dir=str(tmp_path),
        intent_data={"slug": "eof-sync", "goal": "End-of-flow sync", "target_branch": "main"},
        execution_branch="I-1-eof/P-1/E-1/_",
        auto_calibrate=True,
    )


def test_conflicting_pr_syncs_reenters_stages_4_and_5_and_completes(tmp_path):
    ctx = _make_context(tmp_path)
    cp_path = str(tmp_path / "checkpoint.json")
    verdicts = iter([_DIRTY, _CLEAN])
    requests: list[SyncRequest] = []
    review_calls = 0
    calibrate_calls = 0

    def fake_review(context: FlowContext) -> StageResult:
        nonlocal review_calls
        review_calls += 1
        return _approved_review()

    def fake_calibrate(context: FlowContext) -> StageResult:
        nonlocal calibrate_calls
        calibrate_calls += 1
        return StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS, payload={"calibrated_head": "abc123"})

    def fake_sync(req: SyncRequest) -> SyncResult:
        requests.append(req)
        return SyncResult(
            status="merged",
            head_before="aaa",
            head_after="bbb",
            target_sha="ttt",
            code_paths=("src/app.py",),
            changed_paths=("src/app.py",),
            resolver_tier="tier1",
        )

    with (
        patch("sandbox_executor.flow.pipeline.run_review_stage", side_effect=fake_review),
        patch("sandbox_executor.flow.pipeline.run_calibrate_stage", side_effect=fake_calibrate),
        patch(
            "sandbox_executor.flow.end_of_flow_sync.check_pr_mergeability", side_effect=lambda *a, **k: next(verdicts)
        ),
        patch("sandbox_executor.flow.end_of_flow_sync._precheck_sync", return_value=None),
        patch("sandbox_executor.flow.end_of_flow_sync.sync_with_target", side_effect=fake_sync),
    ):
        engine = PipelineEngine(ctx, from_stage="review", checkpoint_path=cp_path)
        engine.run()

    assert review_calls == 2
    assert calibrate_calls == 2
    assert len(requests) == 1
    assert requests[0].boundary is SyncBoundary.FLOW_END
    assert requests[0].reentry_round == 1
    assert "src/app.py" in ctx.review_scope_paths
    assert ctx.stage_results["calibrate"].status == StageStatus.SUCCESS
    assert any("completed successfully" in line for line in ctx.logs)


def test_conflicting_sync_blocked_never_logs_completed_successfully_and_saves_checkpoint(tmp_path):
    ctx = _make_context(tmp_path)
    cp_path = str(tmp_path / "checkpoint.json")
    blocked_sync = SyncResult(
        status="blocked",
        head_before="aaa",
        head_after="aaa",
        target_sha="ttt",
        code_paths=("src/conflict.py",),
        conflicted_paths=("src/conflict.py",),
        error="resolver could not resolve conflict",
    )

    with (
        patch("sandbox_executor.flow.pipeline.run_review_stage", return_value=_approved_review()),
        patch(
            "sandbox_executor.flow.pipeline.run_calibrate_stage",
            return_value=StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS),
        ),
        patch("sandbox_executor.flow.end_of_flow_sync.check_pr_mergeability", return_value=_DIRTY),
        patch("sandbox_executor.flow.end_of_flow_sync._precheck_sync", return_value=None),
        patch("sandbox_executor.flow.end_of_flow_sync.sync_with_target", return_value=blocked_sync),
    ):
        engine = PipelineEngine(ctx, from_stage="review", checkpoint_path=cp_path)
        engine.run()

    assert not any("completed successfully" in line for line in ctx.logs)
    assert any("Pipeline blocked at end-of-flow sync" in line for line in ctx.logs)
    cal = ctx.stage_results["calibrate"]
    assert cal.status == StageStatus.FAILED
    assert cal.payload["end_of_flow_sync"]["blocked"] is True
    assert cal.payload["end_of_flow_sync"]["rounds"] == 1
    loaded = load_checkpoint(cp_path)
    assert loaded.context.stage_results["calibrate"].status == StageStatus.FAILED


def test_reentered_review_not_approved_blocks_without_completed_successfully(tmp_path):
    ctx = _make_context(tmp_path)
    cp_path = str(tmp_path / "checkpoint.json")
    reviews = iter(
        [
            _approved_review(),
            StageResult(stage=FlowStage.REVIEW, status=StageStatus.HALTED_FOR_HUMAN, error="needs human review"),
        ]
    )
    merged = SyncResult(status="merged", head_before="a", head_after="b", target_sha="t")

    with (
        patch("sandbox_executor.flow.pipeline.run_review_stage", side_effect=lambda _: next(reviews)),
        patch(
            "sandbox_executor.flow.pipeline.run_calibrate_stage",
            return_value=StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS),
        ),
        patch("sandbox_executor.flow.end_of_flow_sync.check_pr_mergeability", return_value=_DIRTY),
        patch("sandbox_executor.flow.end_of_flow_sync._precheck_sync", return_value=None),
        patch("sandbox_executor.flow.end_of_flow_sync.sync_with_target", return_value=merged),
    ):
        engine = PipelineEngine(ctx, from_stage="review", checkpoint_path=cp_path)
        engine.run()

    assert not any("completed successfully" in line for line in ctx.logs)
    assert ctx.stage_results["calibrate"].status == StageStatus.FAILED
    assert load_checkpoint(cp_path).context.stage_results["calibrate"].status == StageStatus.FAILED


def test_reentry_cap_exhausted_blocks_after_three_rounds(tmp_path):
    ctx = _make_context(tmp_path)
    cp_path = str(tmp_path / "checkpoint.json")
    rounds_seen: list[int] = []

    def fake_sync(req: SyncRequest) -> SyncResult:
        rounds_seen.append(req.reentry_round)
        return SyncResult(status="merged", head_before="a", head_after="b", target_sha="t")

    with (
        patch("sandbox_executor.flow.pipeline.run_review_stage", return_value=_approved_review()),
        patch(
            "sandbox_executor.flow.pipeline.run_calibrate_stage",
            return_value=StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS),
        ),
        patch("sandbox_executor.flow.end_of_flow_sync.check_pr_mergeability", return_value=_DIRTY),
        patch("sandbox_executor.flow.end_of_flow_sync._precheck_sync", return_value=None),
        patch("sandbox_executor.flow.end_of_flow_sync.sync_with_target", side_effect=fake_sync),
    ):
        engine = PipelineEngine(ctx, from_stage="review", checkpoint_path=cp_path)
        engine.run()

    assert rounds_seen == [1, 2, 3]
    assert len(rounds_seen) == MAX_END_OF_FLOW_REENTRIES
    assert not any("completed successfully" in line for line in ctx.logs)
    assert any("Pipeline blocked at end-of-flow sync" in line for line in ctx.logs)
    cal = ctx.stage_results["calibrate"]
    assert cal.status == StageStatus.FAILED
    assert cal.payload["end_of_flow_sync"]["rounds"] == MAX_END_OF_FLOW_REENTRIES
    assert load_checkpoint(cp_path).context.stage_results["calibrate"].status == StageStatus.FAILED


def test_unknown_mergeability_reprobes_and_succeeds_without_sync(tmp_path):
    ctx = _make_context(tmp_path)
    verdicts = iter([_UNKNOWN, _UNKNOWN, _CLEAN])
    sleeps: list[float] = []
    sync_called = False

    def fake_sync(_req: SyncRequest) -> SyncResult:
        nonlocal sync_called
        sync_called = True
        return SyncResult(status="unchanged", head_before="", head_after="", target_sha="")

    seams = EndOfFlowSeams(
        mergeability_probe=lambda *_: next(verdicts),
        sync_runner=fake_sync,
        sleep=sleeps.append,
    )
    res = finish_flow(ctx, lambda _: _approved_review(), lambda *_: None, seams=seams)

    assert res.clean is True
    assert res.blocked is False
    assert res.rounds == 0
    assert sync_called is False
    assert sleeps == [2, 4]


def test_persistent_unknown_mergeability_fails_closed(tmp_path):
    ctx = _make_context(tmp_path)
    probe_count = 0
    sleeps: list[float] = []
    sync_called = False

    def fake_probe(*_):
        nonlocal probe_count
        probe_count += 1
        return _UNKNOWN

    def fake_sync(_req: SyncRequest) -> SyncResult:
        nonlocal sync_called
        sync_called = True
        return SyncResult(status="unchanged", head_before="", head_after="", target_sha="")

    seams = EndOfFlowSeams(mergeability_probe=fake_probe, sync_runner=fake_sync, sleep=sleeps.append)
    res = finish_flow(ctx, lambda _: _approved_review(), lambda *_: None, seams=seams)

    assert probe_count == MAX_UNKNOWN_MERGEABILITY_PROBES
    assert sleeps == [2, 4]
    assert sync_called is False
    assert res.clean is False
    assert res.blocked is True
    assert "UNKNOWN" in res.reason
    assert f"after {MAX_UNKNOWN_MERGEABILITY_PROBES} probes" in res.reason


def test_missing_ref_fails_closed_immediately_without_reprobe(tmp_path):
    ctx = _make_context(tmp_path)
    probe_count = 0
    sleeps: list[float] = []

    def fake_probe(*_):
        nonlocal probe_count
        probe_count += 1
        return _MISSING

    seams = EndOfFlowSeams(mergeability_probe=fake_probe, sleep=sleeps.append)
    res = finish_flow(ctx, lambda _: _approved_review(), lambda *_: None, seams=seams)

    assert probe_count == 1
    assert sleeps == []
    assert res.clean is False
    assert res.blocked is True
    assert res.merge_state_status == "MISSING_REF"
    assert "probes" not in res.reason


def test_sync_row_records_reentry_round_for_flow_end_only(tmp_path):
    result = SyncResult(status="merged", head_before="aaa", head_after="bbb", target_sha="ttt")
    for round_num in (1, 2, 3):
        req = SyncRequest(
            worktree=str(tmp_path),
            branch="I-1/P-1/E-1/_",
            target_branch="main",
            stage="calibrate",
            boundary=SyncBoundary.FLOW_END,
            ledger_path=str(tmp_path / "executions.jsonl"),
            execution_id="E-1",
            reentry_round=round_num,
        )
        row = sync_row(req, result, "")
        assert row["boundary"] == "flow_end"
        assert row["reentry_round"] == round_num

    non_end_req = SyncRequest(
        worktree=str(tmp_path),
        branch="I-1/P-1/E-1/_",
        target_branch="main",
        stage="execute",
        boundary=SyncBoundary.STAGE_END,
        ledger_path=str(tmp_path / "executions.jsonl"),
        execution_id="E-1",
    )
    assert "reentry_round" not in sync_row(non_end_req, result, "")


def test_resumed_calibrate_stage_also_runs_finish_flow(tmp_path):
    ctx = _make_context(tmp_path)
    ctx.stage_results["review"] = _approved_review()
    cp_path = str(tmp_path / "checkpoint.json")
    blocked_sync = SyncResult(status="blocked", head_before="a", head_after="a", target_sha="t", error="conflict")

    with (
        patch(
            "sandbox_executor.flow.pipeline.run_calibrate_stage",
            return_value=StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS),
        ),
        patch("sandbox_executor.flow.end_of_flow_sync.check_pr_mergeability", return_value=_DIRTY),
        patch("sandbox_executor.flow.end_of_flow_sync._precheck_sync", return_value=None),
        patch("sandbox_executor.flow.end_of_flow_sync.sync_with_target", return_value=blocked_sync),
    ):
        engine = PipelineEngine(ctx, from_stage="calibrate", checkpoint_path=cp_path)
        engine.run()

    assert not any("completed successfully" in line for line in ctx.logs)
    assert ctx.stage_results["calibrate"].status == StageStatus.FAILED


def test_cli_report_review_outcome_suppresses_approved_banner_on_failure(capsys):
    approved = _approved_review()
    _report_review_outcome(approved, has_failure=True)
    assert capsys.readouterr().out == ""

    _report_review_outcome(approved, has_failure=False)
    assert "[REVIEW APPROVED]" in capsys.readouterr().out


def test_reentry_resets_exhausted_review_budget_and_carried_findings(tmp_path):
    ctx = _make_context(tmp_path)
    ctx.review_iterations_done = ctx.max_review_iterations
    ctx.carried_review_findings = [{"id": "F-1", "summary": "stale finding"}]
    verdicts = iter([_DIRTY, _CLEAN])
    observed: list[tuple[int, list]] = []

    def fake_run_stage(stage: FlowStage) -> StageResult:
        if stage is FlowStage.REVIEW:
            observed.append((ctx.review_iterations_done, list(ctx.carried_review_findings)))
            return _approved_review()
        return StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS)

    seams = EndOfFlowSeams(
        mergeability_probe=lambda *_: next(verdicts),
        sync_runner=lambda _: SyncResult(status="merged", head_before="a", head_after="b", target_sha="t"),
    )
    res = finish_flow(ctx, fake_run_stage, lambda *_: None, seams=seams)

    assert res.clean is True
    assert observed == [(0, [])]


def test_flow_end_sync_fails_closed_for_protected_or_unresolvable_branch(tmp_path):
    for branch, can_sync, resolved, expected_err in [
        ("main", True, "refs/heads/main", "protected branch"),
        ("I-1/P-1/E-1/_", False, "refs/heads/I-1/P-1/E-1/_", "fetching 'main'"),
        ("I-1/P-1/E-1/_", True, None, "could not be resolved"),
    ]:
        ctx = _make_context(tmp_path)
        ctx.execution_branch = branch
        with (
            patch("sandbox_executor.flow.end_of_flow_sync._hooks._can_sync_worktree", return_value=can_sync),
            patch("sandbox_executor.flow.end_of_flow_sync._resolve_branch_ref", return_value=resolved),
            patch("sandbox_executor.flow.end_of_flow_sync._hooks._checkout_active_branch") as mock_checkout,
            patch("sandbox_executor.flow.end_of_flow_sync.sync_with_target") as mock_sync,
        ):
            seams = EndOfFlowSeams(mergeability_probe=lambda *_: _DIRTY)
            res = finish_flow(ctx, lambda _: _approved_review(), lambda *_: None, seams=seams)

        assert res.blocked is True
        assert expected_err in res.reason
        mock_checkout.assert_not_called()
        mock_sync.assert_not_called()


def test_probe_mergeability_forwards_review_proxy_env(tmp_path):
    ctx = _make_context(tmp_path)
    expected_proxy = {"HTTP_PROXY": "http://127.0.0.1:8888"}
    with (
        patch("sandbox_executor.flow.end_of_flow_sync.review_proxy_env", return_value=expected_proxy),
        patch("sandbox_executor.flow.end_of_flow_sync.check_pr_mergeability", return_value=_CLEAN) as mock_probe,
    ):
        res = finish_flow(ctx, lambda _: _approved_review(), lambda *_: None)

    assert res.clean is True
    mock_probe.assert_called_once_with(ctx.target_worktree, str(ctx.execution_branch), "main", proxy_env=expected_proxy)
