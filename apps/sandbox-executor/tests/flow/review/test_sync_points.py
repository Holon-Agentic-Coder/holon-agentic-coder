"""Unit and boundary tests for Stage 4/5 ``sync_with_target`` points, carry-over, scoped re-review, and wording."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sandbox_executor.flow import (
    FlowContext,
    FlowStage,
    StageResult,
    StageStatus,
    compute_rereview_scope,
    format_conflict_block_reason,
    is_ledger_only_sync,
    is_mechanical_only,
    make_calibrate_retry_sync,
    sync_after_resolver_turn,
    sync_before_calibration,
    sync_iteration_mergeability,
)
from sandbox_executor.flow.calibrate_retry import call_with_retries
from sandbox_executor.flow.calibrate_sync import CalibrationSyncError
from sandbox_executor.flow.review import approval_carry_over, ensemble, sync_points
from sandbox_executor.flow.review.posting import PostRequest, post_review_consensus
from sandbox_executor.flow.review_restart import stage5_merged_head_gate
from sandbox_executor.flow.target_sync_result import SyncResult
from sandbox_executor.ledger import read_merged_rows_at_ref
from sandbox_executor.review_ensemble import build_review_request
from sandbox_executor.review_target import ReviewTarget

from tests.flow.helpers import _git
from tests.flow.sync_boundary_repos import (
    APP,
    BASE,
    CODE_BASE,
    FLOW_CODE,
    LEDGER,
    TARGET_CODE,
    ledger_conflict,
    sync_work,
)
from tests.flow.test_sync_boundaries import FakeResolverDispatcher, _inject_ledger_conflict
from tests.flow_repo_templates import FlowCommit, flow_history_copy
from tests.ledger_conflict_repos import FLOW_BRANCH


def _resolve_app(work: Path) -> None:
    (work / APP).write_text("x = 1\ny = 2\n", encoding="utf-8")


def _ctx(work: Path, *, dispatcher: FakeResolverDispatcher | None = None) -> FlowContext:
    return FlowContext(
        target_worktree=str(work),
        intent_data={"intent_id": "I-1", "slug": "x", "target_branch": "main"},
        execution_branch=FLOW_BRANCH,
        agent="claude-agent",
        model="claude-opus-5-5",
        token_reduce=False,
        dispatcher=dispatcher,
    )


def test_mechanical_vs_agent_helpers_and_rereview_scope() -> None:
    mech = SyncResult("merged", "a" * 40, "b" * 40, "c" * 40, resolutions=((LEDGER, "ledger", "mechanical"),))
    assert is_mechanical_only(mech) is True
    assert is_ledger_only_sync(mech) is True
    assert compute_rereview_scope(mech) == ()

    agent = SyncResult(
        "merged",
        "a" * 40,
        "b" * 40,
        "c" * 40,
        resolutions=((APP, "code", "tier-1"),),
        code_paths=(APP,),
        resolver_tier="tier-1",
        changed_paths=(APP, "tests/test_app.py", "src/target_only.py", LEDGER),
        resolver_test_edits=("tests/test_app.py",),
    )
    assert is_mechanical_only(agent) is False
    assert is_ledger_only_sync(agent) is False
    assert compute_rereview_scope(agent) == (APP, "tests/test_app.py")


@pytest.mark.parametrize(
    ("result", "must_contain", "must_not_contain"),
    [
        (
            SyncResult("blocked", "a", "a", "b", code_paths=(APP,), conflicted_paths=(APP,), error="unresolved"),
            "code conflicts with main in: src/app.py",
            "merge conflicts",
        ),
        (
            SyncResult(
                "blocked",
                "a",
                "a",
                "b",
                conflicted_paths=(LEDGER,),
                error="ledger append failed",
            ),
            "merge conflicts with main in:",
            "code conflicts",
        ),
        (
            SyncResult(
                "blocked",
                "a",
                "a",
                "b",
                resolutions=(("holon-knowledge/kb/x.json", "kb_timestamp_only", "mechanical"),),
                error="push rejected",
            ),
            "kb_timestamp_only",
            "code conflicts",
        ),
    ],
)
def test_format_conflict_block_reason_labels_code_conflicts_only_for_code(
    result: SyncResult, must_contain: str, must_not_contain: str
) -> None:
    reason = format_conflict_block_reason(result, "main")
    assert must_contain in reason
    assert must_not_contain not in reason


def test_stage4_iteration_and_resolver_turn_boundaries_resolve_injected_conflicts(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    repos = flow_history_copy(tmp_path_factory, tmp_path, CODE_BASE, (TARGET_CODE, FLOW_CODE))
    work, before = repos.work, repos.shas[1]
    dispatcher = FakeResolverDispatcher([(_resolve_app, "resolved")])
    ctx = _ctx(work, dispatcher=dispatcher)
    target = ReviewTarget(raw=FLOW_BRANCH, head_branch=FLOW_BRANCH, head_sha=before)

    probes = iter(
        [
            {"mergeable": "CONFLICTING", "merge_state_status": "DIRTY", "is_dirty": True},
            {"mergeable": "MERGEABLE", "merge_state_status": "BLOCKED", "is_dirty": False},
        ]
    )
    updated, mergeability, is_dirty, err = sync_iteration_mergeability(ctx, target, "main", 1, lambda: next(probes))

    assert err is None
    assert is_dirty is False and mergeability["merge_state_status"] == "BLOCKED"
    assert updated is not None and updated.head_sha != before
    assert ctx.review_scope_paths == []  # Unapproved PR must never be narrowed to conflict-resolved paths.

    # When already in a post-approval scoped re-review, a further agent merge unions into review_scope_paths.
    repos_u = flow_history_copy(tmp_path_factory, tmp_path / "union", CODE_BASE, (TARGET_CODE, FLOW_CODE))
    before_union = repos_u.shas[1]
    ctx_u = _ctx(repos_u.work, dispatcher=FakeResolverDispatcher([(_resolve_app, "resolved")]))
    ctx_u.review_scope_paths = ["src/other.py"]
    probes_u = iter(
        [
            {"mergeable": "CONFLICTING", "merge_state_status": "DIRTY", "is_dirty": True},
            {"mergeable": "MERGEABLE", "merge_state_status": "CLEAN", "is_dirty": False},
        ]
    )
    updated_u, _, _, err_u = sync_iteration_mergeability(
        ctx_u,
        ReviewTarget(raw=FLOW_BRANCH, head_branch=FLOW_BRANCH, head_sha=before_union),
        "main",
        2,
        lambda: next(probes_u),
    )
    assert err_u is None and updated_u is not None and updated_u.head_sha != before_union
    assert ctx_u.review_scope_paths == [APP, "src/other.py"]

    # Resolver turn boundary also resolves a newly injected target ledger conflict.
    repos_turn = flow_history_copy(tmp_path_factory, tmp_path / "turn", CODE_BASE, ledger_conflict(2))
    before_turn = repos_turn.shas[1]
    updated2, err2 = sync_after_resolver_turn(
        _ctx(repos_turn.work), ReviewTarget(raw=FLOW_BRANCH, head_branch=FLOW_BRANCH, head_sha=before_turn), "main", 1
    )
    assert err2 is None
    assert updated2 is not None and updated2.head_sha != before_turn


def test_agent_resolved_merge_never_carries_approval_and_preserves_scope_across_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_merged = SyncResult(
        "merged",
        "a" * 40,
        "b" * 40,
        "c" * 40,
        resolutions=((APP, "code", "tier-1"),),
        code_paths=(APP,),
        resolver_tier="tier-1",
        changed_paths=(APP, "tests/test_app.py", "src/target_only.py"),
        resolver_test_edits=("tests/test_app.py",),
    )
    monkeypatch.setattr(sync_points, "sync_review_head", lambda *a, **k: agent_merged)
    monkeypatch.setattr(approval_carry_over, "approval_fingerprint_of", lambda *a, **k: "pf2:" + "e" * 40)
    ctx = FlowContext(target_worktree=str(tmp_path), execution_branch=FLOW_BRANCH, token_reduce=False)
    exec_file = tmp_path / "gate.jsonl"
    post = MagicMock()
    request = PostRequest(
        repo_dir=str(tmp_path),
        review_target=FLOW_BRANCH,
        consensus_data={
            "approved": True,
            "base_branch": "main",
            "target_commit": "a" * 40,
            "approval_fingerprint": "pf2:" + "e" * 40,
        },
        proxy_env=None,
        reviewers=[],
        title="t",
        iteration=1,
        mergeability_probe=lambda *a, **k: {
            "mergeable": "CONFLICTING",
            "merge_state_status": "DIRTY",
            "is_dirty": True,
        },
    )

    post_error, posting_res = post_review_consensus(post, ctx, str(exec_file), request)

    post.assert_not_called()
    assert post_error is not None and posting_res["conflict_gate"]["mechanical_only"] is False
    assert ctx.review_scope_paths == [APP, "tests/test_app.py"]

    # Round-trip through checkpoint serialization preserves review_scope_paths.
    restored = FlowContext.from_dict(ctx.to_dict())
    assert restored.review_scope_paths == [APP, "tests/test_app.py"]

    # Stage 5 merged-head gate forces a Stage 4 restart when review_scope_paths is set.
    restored.stage_results["review"] = StageResult(
        stage=FlowStage.REVIEW,
        status=StageStatus.APPROVED,
        payload={"consensus": {"approved": True, "target_commit": "a" * 40, "approval_fingerprint": "pf2:" + "e" * 40}},
    )
    monkeypatch.setattr("sandbox_executor.flow.review_restart._execution_head", lambda *a, **k: "b" * 40)
    monkeypatch.setattr("sandbox_executor.flow.review_restart._git_bookkeeping_move", lambda *a, **k: lambda *_: False)
    monkeypatch.setattr("sandbox_executor.flow.review_restart.withdraw_consensus", lambda *a, **k: {"withdrawn": True})
    stopped, _ = stage5_merged_head_gate(restored)
    assert stopped is not None and stopped.payload.get("review_restart") is True
    assert restored.review_scope_paths == [APP, "tests/test_app.py"]


def test_build_review_request_scopes_diff_and_falls_back_when_empty(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    repos = flow_history_copy(
        tmp_path_factory,
        tmp_path,
        {APP: "x = 0\n", "src/other.py": "z = 0\n"},
        (FlowCommit(FLOW_BRANCH, {APP: "x = 1\n", "src/other.py": "z = 1\n"}, "edit both files"),),
    )
    work, head_sha = repos.work, repos.shas[0]
    target = ReviewTarget(raw=FLOW_BRANCH, head_branch=FLOW_BRANCH, head_sha=head_sha)

    scoped = build_review_request(target, "origin/main", 1, "T", "Body", str(work), review_paths=[APP])
    assert APP in scoped.diff_text
    assert "src/other.py" not in scoped.diff_text
    assert "Scoped Conflict-Resolution Re-Review" in scoped.pr_body

    fallback = build_review_request(
        target, "origin/main", 1, "T", "Body", str(work), review_paths=["does/not/exist.py"]
    )
    assert APP in fallback.diff_text and "src/other.py" in fallback.diff_text


def test_dispatch_review_ensemble_passes_review_paths_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []

    def fake_dispatch(context, role, args, result_file=None):
        del context, role, result_file
        captured.append(list(args))
        return 1

    monkeypatch.setattr(ensemble, "_dispatch_role", fake_dispatch)
    ctx = FlowContext(
        target_worktree=str(tmp_path),
        execution_branch=FLOW_BRANCH,
        token_reduce=False,
        review_scope_paths=[APP, "tests/test_app.py"],
    )
    target = ReviewTarget(raw=FLOW_BRANCH, head_branch=FLOW_BRANCH, head_sha="a" * 40)

    ensemble._dispatch_review_ensemble(ctx, target, 1, 1)

    assert captured and f"--review-paths={APP},tests/test_app.py" in captured[0]


def test_stage5_entry_and_retry_sync_resolve_conflicts_and_block_cleanly(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    work = sync_work(tmp_path_factory, tmp_path, ledger_conflict(2), base=CODE_BASE)
    ctx = _ctx(work)

    # Stage 5 entry sync resolves mechanical ledger conflict.
    assert sync_before_calibration(ctx, "2026-11-24T00:00:00Z") is None
    rows = read_merged_rows_at_ref(str(work), f"origin/{FLOW_BRANCH}", "executions.jsonl")
    assert any(r.get("action") == "target_sync" and r.get("boundary") == "calibrate" for r in rows)

    # Stage 5 calibration retry sync runs before retry attempt 2.
    calls = 0

    def flaky_calibrate(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            _inject_ledger_conflict(work, FLOW_BRANCH, 3)
            raise TimeoutError("transient timeout")
        return {"ok": True}

    wrapped = call_with_retries(
        flaky_calibrate,
        log_fn=ctx.log,
        retry_safe=lambda: True,
        before_retry=make_calibrate_retry_sync(ctx),
    )
    from tests.flow.sleep_patch import scoped_sleep_patch

    with scoped_sleep_patch("sandbox_executor.flow.calibrate_retry"):
        assert wrapped() == {"ok": True}
    assert calls == 2

    # A blocked retry sync raises permanent CalibrationSyncError without retrying further.
    work_fail = sync_work(tmp_path_factory, tmp_path / "blocked", (TARGET_CODE, FLOW_CODE), base=CODE_BASE)
    ctx_fail = _ctx(work_fail, dispatcher=FakeResolverDispatcher([(None, "cannot_decide")] * 3))
    with pytest.raises(CalibrationSyncError, match=r"code conflicts with main in: src/app\.py"):
        make_calibrate_retry_sync(ctx_fail)(1)


def test_finalize_resolver_turn_commits_pending_ledger_rows_and_preserves_full_scope(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    from sandbox_executor.flow.review.resolver import ResolverOutcome
    from sandbox_executor.flow.review.sync_points import finalize_resolver_turn

    repos = flow_history_copy(tmp_path_factory, tmp_path, CODE_BASE, (TARGET_CODE, FLOW_CODE))
    work, before = repos.work, repos.shas[1]
    # Append uncommitted ACT-REV row to tracked executions.jsonl before finalize_resolver_turn.
    with open(work / LEDGER, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"action_id": "ACT-REV-1", "execution_id": "REV-1", "action": "review"}) + "\n")

    ctx = _ctx(work, dispatcher=FakeResolverDispatcher([(_resolve_app, "resolved")]))
    ctx.review_scope_paths = ["src/stale.py"]
    target = ReviewTarget(raw=FLOW_BRANCH, head_branch=FLOW_BRANCH, head_sha=before)
    outcome = ResolverOutcome(dispositions=(), pushed=True, head_sha=before)

    final = finalize_resolver_turn(ctx, target, 1, outcome)

    assert final.error is None
    assert final.pushed is True and final.head_sha != before
    assert ctx.review_scope_paths == []  # Resolver push forces full review even after agent-resolved sync.


def test_conflict_gate_blocks_dry_run_head_and_deferred_or_unprobed_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sandbox_executor.flow.review import conflict_gate
    from sandbox_executor.flow.target_sync_result import SyncBoundary

    # Strict boundary blocks detached HEAD.
    ctx_head = FlowContext(target_worktree=str(tmp_path), execution_branch="HEAD", token_reduce=False)
    res_head = sync_points.sync_review_head(ctx_head, "HEAD", "main", SyncBoundary.PUSH)
    assert res_head.status == "blocked"

    # _approved_post_gate blocks a conflicting head in dry_run without merging or posting.
    ctx_dry = FlowContext(target_worktree=str(tmp_path), execution_branch=FLOW_BRANCH, dry_run=True, token_reduce=False)
    post = MagicMock()
    dirty = {"mergeable": "CONFLICTING", "merge_state_status": "DIRTY", "is_dirty": True}
    req = PostRequest(
        repo_dir=str(tmp_path),
        review_target=FLOW_BRANCH,
        consensus_data={"approved": True, "base_branch": "main"},
        proxy_env=None,
        reviewers=[],
        title="t",
        iteration=1,
        mergeability_probe=lambda *a, **k: dict(dirty),
    )
    post_err, _ = post_review_consensus(post, ctx_dry, str(tmp_path / "gate.jsonl"), req)
    post.assert_not_called()
    assert post_err is not None and "dry run" in post_err

    # _gate_conflicting_head blocks on deferred or unprobed unchanged SyncResult.
    ctx_real = FlowContext(target_worktree=str(tmp_path), execution_branch=FLOW_BRANCH, token_reduce=False)
    merge = conflict_gate._GateMerge(trailers="", iteration=1)
    for bad in (SyncResult("deferred", "a", "a", "b"), SyncResult("unchanged", "", "", "")):
        monkeypatch.setattr(sync_points, "sync_review_head", lambda *a, _b=bad, **k: _b)
        gate = conflict_gate._gate_conflicting_head(ctx_real, FLOW_BRANCH, "main", dirty, merge)
        assert gate.conflicting is True and gate.blocked_reason is not None


def test_prepare_execution_branch_syncs_before_calibrated_and_retry_reanchors(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sandbox_executor.flow.calibrate_sync import publish_calibration
    from sandbox_executor.flow.stages import calibrate as cal_stage
    from sandbox_executor.ref_names import calibration_branch

    work = sync_work(tmp_path_factory, tmp_path, (TARGET_CODE, FLOW_CODE), base=CODE_BASE)
    calibrated = calibration_branch(FLOW_BRANCH)

    # Blocked sync_before_calibration does not create/leak /calibrated.
    ctx_blk = _ctx(work, dispatcher=FakeResolverDispatcher([(None, "cannot_decide")] * 3))
    failure, sha, created = cal_stage._prepare_execution_branch(ctx_blk, "2026-11-24T00:00:00Z")
    assert failure is not None and sha is None and created is False
    assert _git(work, "branch", "--list", calibrated) == ""

    # Mechanical sync_before_calibration runs before prepare_calibrated_branch so /calibrated starts at merged SHA.
    work2 = sync_work(tmp_path_factory, tmp_path / "r2", ledger_conflict(2), base=CODE_BASE)
    ctx_ok = _ctx(work2)
    failure2, start_sha, created2 = cal_stage._prepare_execution_branch(ctx_ok, "2026-11-24T00:00:00Z")
    assert failure2 is None and created2 is True and start_sha is not None
    assert _git(work2, "rev-parse", f"refs/heads/{calibrated}") == start_sha
    assert _git(work2, "rev-parse", f"refs/heads/{FLOW_BRANCH}") == start_sha

    # Mechanical retry sync re-anchors /calibrated and updates start_sha so publish_calibration --ff-only succeeds.
    _inject_ledger_conflict(work2, FLOW_BRANCH, 3)
    updated_shas: list[str] = []
    make_calibrate_retry_sync(ctx_ok, on_head_updated=updated_shas.append)(1)
    assert len(updated_shas) == 1 and updated_shas[0] != start_sha
    assert _git(work2, "rev-parse", f"refs/heads/{calibrated}") == updated_shas[0]
    _git(work2, "checkout", calibrated)
    (work2 / "cal.md").write_text("report\n", encoding="utf-8")
    _git(work2, "add", "cal.md")
    _git(work2, "commit", "-m", "cal report")
    ctx_ok.calibrated_branch = calibrated
    assert publish_calibration(ctx_ok) is True

    # Agent-resolved sync_before_calibration triggers Stage 4 review restart via stage5_merged_head_gate.
    repos3 = flow_history_copy(tmp_path_factory, tmp_path / "r3", CODE_BASE, (TARGET_CODE, FLOW_CODE))
    work3, approved_sha = repos3.work, repos3.shas[1]
    ctx_r = _ctx(work3, dispatcher=FakeResolverDispatcher([(_resolve_app, "resolved")]))
    ctx_r.stage_results["review"] = StageResult(
        stage=FlowStage.REVIEW,
        status=StageStatus.APPROVED,
        payload={"consensus": {"approved": True, "target_commit": approved_sha, "approval_fingerprint": "pf1:abc"}},
    )
    monkeypatch.setattr("sandbox_executor.flow.review_restart.withdraw_consensus", lambda *a, **k: {"withdrawn": True})
    stopped, sha3, created3 = cal_stage._prepare_execution_branch(ctx_r, "2026-11-24T00:00:00Z")
    assert stopped is not None and stopped.payload.get("review_restart") is True
    assert sha3 is None and created3 is False
    assert _git(work3, "branch", "--list", calibrated) == ""


def test_containerised_run_review_loop_skips_target_sync_and_dispatches_reviewers(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sandbox_executor.flow import run_review_loop
    from sandbox_executor.flow.target_probe import TargetProbeError
    from sandbox_executor.flow.target_sync_result import SyncBoundary
    from sandbox_executor.review_target import DiffVerdict

    from tests.review_fakes import FakeEnsemble

    repos = flow_history_copy(tmp_path_factory, tmp_path, BASE, (FLOW_CODE,))
    work, head_sha = repos.work, repos.shas[0]
    ctx = _ctx(work)

    def _boom(*_a, **_k):
        raise TargetProbeError("no remote access inside role container")

    monkeypatch.setattr(sync_points, "workspace_mounted", lambda: True)
    monkeypatch.setattr(sync_points, "probe_target", _boom)
    monkeypatch.setattr("sandbox_executor.flow.review.loop.workspace_mounted", lambda: True)
    monkeypatch.setattr(
        "sandbox_executor.review_target.verify_review_diff", lambda *a, **k: (DiffVerdict.VERIFIED, "ok")
    )
    monkeypatch.setattr(
        "sandbox_executor.flow.review.loop._resolve_reviewed_target",
        lambda *a, **k: (ReviewTarget(raw=FLOW_BRANCH, head_branch=FLOW_BRANCH, head_sha=head_sha), None),
    )
    monkeypatch.setattr(
        "sandbox_executor.flow.review.loop.check_pr_mergeability",
        lambda *a, **k: {"mergeable": "MERGEABLE", "merge_state_status": "CLEAN", "is_dirty": False},
    )
    monkeypatch.setattr(
        "sandbox_executor.flow.review.loop.probe_ci_checks",
        lambda *a, **k: {
            "measured": True,
            "all_passed": True,
            "passed": 1,
            "failed": 0,
            "pending": 0,
            "checks": [{"name": "ci", "bucket": "pass"}],
        },
    )
    monkeypatch.setattr(
        "sandbox_executor.flow.review.loop.post_review_consensus", lambda *a, **k: (None, {"posted": True})
    )

    # Strict boundary also returns unchanged inside a mounted container without touching origin.
    assert sync_points.sync_review_head(ctx, FLOW_BRANCH, "main", SyncBoundary.PUSH).status == "unchanged"

    fake_ensemble = FakeEnsemble()
    res = run_review_loop(
        ctx,
        target=FLOW_BRANCH,
        max_iterations=1,
        ensemble_size=3,
        run_ensemble_fn=fake_ensemble,
        github=MagicMock(available=False),
    )
    assert len(fake_ensemble.calls) == 1
    assert res["consensus"]["approved"] is True


def test_stage4_start_boundary_unions_rereview_scope_when_already_scoped(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    from sandbox_executor.flow.target_sync_hooks import at_stage_boundary
    from sandbox_executor.flow.target_sync_result import SyncBoundary

    work = sync_work(tmp_path_factory, tmp_path, (TARGET_CODE, FLOW_CODE), base=CODE_BASE)

    # Already scoped Stage 4 entry unions newly agent-resolved paths into review_scope_paths.
    ctx = _ctx(work, dispatcher=FakeResolverDispatcher([(_resolve_app, "resolved")]))
    ctx.review_scope_paths = ["src/existing.py"]
    assert at_stage_boundary(ctx, FlowStage.REVIEW, SyncBoundary.STAGE_START) is None
    assert ctx.review_scope_paths == [APP, "src/existing.py"]

    # Unscoped Stage 4 entry keeps review_scope_paths empty (full PR review).
    work_full = sync_work(tmp_path_factory, tmp_path / "full", (TARGET_CODE, FLOW_CODE), base=CODE_BASE)
    ctx_full = _ctx(work_full, dispatcher=FakeResolverDispatcher([(_resolve_app, "resolved")]))
    assert at_stage_boundary(ctx_full, FlowStage.REVIEW, SyncBoundary.STAGE_START) is None
    assert ctx_full.review_scope_paths == []


def _approved_stage5_ctx(work: Path, approved_sha: str) -> tuple[FlowContext, str]:
    from sandbox_executor.flow.review.approval_fingerprint import approval_fingerprint_of

    fp = approval_fingerprint_of(str(work), approved_sha, "main")
    assert fp is not None
    ctx = _ctx(work)
    ctx.stage_results["review"] = StageResult(
        stage=FlowStage.REVIEW,
        status=StageStatus.APPROVED,
        payload={
            "consensus": {
                "approved": True,
                "base_branch": "main",
                "target_commit": approved_sha,
                "approval_fingerprint": fp,
            }
        },
    )
    return ctx, approved_sha


def test_sync_before_calibration_gates_ci_on_mechanical_merge(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sandbox_executor.flow.github.ci_head_checks import FAILED
    from sandbox_executor.flow.review.merged_head_gate import gate_record

    repos = flow_history_copy(tmp_path_factory, tmp_path, CODE_BASE, (FLOW_CODE, *ledger_conflict(10)))
    work = repos.work
    ctx, _ = _approved_stage5_ctx(work, repos.shas[0])
    gate_heads: list[str] = []

    def _ci_fail(_ctx, _repo, head, _seams):
        gate_heads.append(head)
        return gate_record(FAILED, head, [], [], "unit failed")

    monkeypatch.setattr("sandbox_executor.flow.review_restart.gate_merged_head_ci", _ci_fail)
    monkeypatch.setattr("sandbox_executor.flow.review_restart.withdraw_consensus", lambda *a, **k: {"withdrawn": True})
    stopped = sync_before_calibration(ctx, "2026-11-24T00:00:00Z")
    assert stopped is not None and stopped.payload.get("review_restart") is True
    assert len(gate_heads) == 1
    assert _git(work, "status", "--porcelain") == ""


def test_calibrate_retry_sync_from_calibrated_branch_commits_carry_over_and_gates_ci(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sandbox_executor.flow.calibrate_sync import prepare_calibrated_branch, publish_calibration
    from sandbox_executor.flow.calibrate_target_sync import _CalibrateRestartRequired
    from sandbox_executor.flow.github.ci_head_checks import FAILED, PASSED
    from sandbox_executor.flow.review.merged_head_gate import gate_record
    from sandbox_executor.ref_names import calibration_branch

    repos = flow_history_copy(tmp_path_factory, tmp_path, CODE_BASE, (FLOW_CODE,))
    work = repos.work
    ctx, approved = _approved_stage5_ctx(work, repos.shas[0])
    calibrated = calibration_branch(FLOW_BRANCH)
    assert prepare_calibrated_branch(ctx, approved) is True
    _git(work, "checkout", calibrated)
    _inject_ledger_conflict(work, FLOW_BRANCH, 11)

    passed_heads: list[str] = []
    monkeypatch.setattr(
        "sandbox_executor.flow.review_restart.gate_merged_head_ci",
        lambda _c, _r, head, _s: (passed_heads.append(head), gate_record(PASSED, head, [], [], "ok"))[1],
    )
    updated: list[str] = []
    make_calibrate_retry_sync(ctx, on_head_updated=updated.append)(1)

    assert len(passed_heads) == 1 and len(updated) == 1
    assert _git(work, "status", "--porcelain") == ""
    assert _git(work, "rev-parse", f"refs/heads/{calibrated}") == updated[0]
    assert _git(work, "rev-parse", f"origin/{FLOW_BRANCH}") == updated[0]
    rows = read_merged_rows_at_ref(str(work), f"origin/{FLOW_BRANCH}", "executions.jsonl")
    assert any(str(r.get("action_id", "")).startswith("ACT-CARRY") for r in rows)

    # A second retry sync with HEAD on /calibrated and failing CI raises _CalibrateRestartRequired cleanly.
    _git(work, "checkout", calibrated)
    _inject_ledger_conflict(work, FLOW_BRANCH, 12)
    monkeypatch.setattr(
        "sandbox_executor.flow.review_restart.gate_merged_head_ci",
        lambda _c, _r, head, _s: gate_record(FAILED, head, [], [], "unit failed"),
    )
    monkeypatch.setattr("sandbox_executor.flow.review_restart.withdraw_consensus", lambda *a, **k: {"withdrawn": True})
    with pytest.raises(_CalibrateRestartRequired) as exc_info:
        make_calibrate_retry_sync(ctx)(2)
    assert exc_info.value.result.payload.get("review_restart") is True
    assert _git(work, "status", "--porcelain") == ""

    # Publishing calibration from the re-anchored /calibrated branch succeeds cleanly.
    _git(work, "checkout", FLOW_BRANCH)
    prepare_calibrated_branch(ctx, _git(work, "rev-parse", f"origin/{FLOW_BRANCH}"))
    _git(work, "checkout", calibrated)
    (work / "cal.md").write_text("report\n", encoding="utf-8")
    _git(work, "add", "cal.md")
    _git(work, "commit", "-m", "cal report")
    ctx.calibrated_branch = calibrated
    assert publish_calibration(ctx) is True


def _code_merge() -> SyncResult:
    """An agent-resolved (``CODE``) merge that resolved ``APP`` and edited a test, plus ledger and metadata paths."""
    return SyncResult(
        "merged",
        "a" * 40,
        "b" * 40,
        "c" * 40,
        resolutions=((APP, "code", "tier-1"), (LEDGER, "ledger", "mechanical")),
        code_paths=(APP, LEDGER, "executions/E-1.md"),
        resolver_tier="tier-1",
        changed_paths=(APP, "tests/test_app.py", "src/target_only.py", LEDGER),
        resolver_test_edits=("tests/test_app.py",),
    )


def _approve(ctx: FlowContext) -> FlowContext:
    consensus = {"approved": True, "target_commit": "a" * 40, "approval_fingerprint": "pf2:" + "e" * 40}
    ctx.stage_results["review"] = StageResult(FlowStage.REVIEW, StageStatus.APPROVED, payload={"consensus": consensus})
    return ctx


def _turn_ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: SyncResult) -> FlowContext:
    monkeypatch.setattr(sync_points, "sync_review_head", lambda *a, **k: result)
    return FlowContext(target_worktree=str(tmp_path), execution_branch=FLOW_BRANCH, token_reduce=False)


def _iteration_sync(ctx: FlowContext) -> ReviewTarget | None:
    target = ReviewTarget(raw=FLOW_BRANCH, head_branch=FLOW_BRANCH, head_sha="a" * 40)
    updated, error = sync_points.sync_before_review_iteration(ctx, target, "main", 2)
    assert error is None
    return updated


def test_code_merge_under_an_approval_records_only_the_resolved_code_and_tests(tmp_path, monkeypatch) -> None:
    ctx = _approve(_turn_ctx(tmp_path, monkeypatch, _code_merge()))
    updated = _iteration_sync(ctx)
    assert ctx.review_scope_paths == [APP, "tests/test_app.py"]  # No ledger, metadata or target-only path.
    assert updated is not None and updated.head_sha == "b" * 40


def test_code_merge_unions_an_existing_scope(tmp_path, monkeypatch) -> None:
    ctx = _turn_ctx(tmp_path, monkeypatch, _code_merge())
    ctx.review_scope_paths = ["src/earlier.py"]
    _iteration_sync(ctx)
    assert ctx.review_scope_paths == [APP, "src/earlier.py", "tests/test_app.py"]


@pytest.mark.parametrize("consensus", [None, {"approved": False, "target_commit": "a" * 40, "withdrawn": {}}])
def test_code_merge_without_a_standing_approval_keeps_the_full_review(tmp_path, monkeypatch, consensus) -> None:
    ctx = _turn_ctx(tmp_path, monkeypatch, _code_merge())
    if consensus is not None:  # A withdrawn consensus is no longer approved.
        ctx.stage_results["review"] = StageResult(
            FlowStage.REVIEW, StageStatus.FAILED, payload={"consensus": consensus}
        )
    _iteration_sync(ctx)
    assert ctx.review_scope_paths == []


def test_mechanical_merge_never_records_a_scope(tmp_path, monkeypatch) -> None:
    mech = SyncResult("merged", "a" * 40, "b" * 40, "c" * 40, resolutions=((LEDGER, "ledger", "mechanical"),))
    ctx = _approve(_turn_ctx(tmp_path, monkeypatch, mech))
    _iteration_sync(ctx)
    assert ctx.review_scope_paths == []


def test_record_rereview_scope_rule() -> None:
    from sandbox_executor.flow.review import sync_scope

    ctx = FlowContext(token_reduce=False)
    assert sync_scope.record_rereview_scope(ctx, ()) == sync_scope.SCOPE_UNCHANGED
    assert sync_scope.record_rereview_scope(ctx, ("b.py",)) == sync_scope.SCOPE_FULL_REVIEW
    assert ctx.review_scope_paths == []
    assert sync_scope.record_rereview_scope(ctx, ("b.py", "a.py"), approved=True) == sync_scope.SCOPE_RECORDED
    assert ctx.review_scope_paths == ["a.py", "b.py"]
    assert sync_scope.record_rereview_scope(ctx, ("c.py",), approved=False) == sync_scope.SCOPE_UNIONED
    assert ctx.review_scope_paths == ["a.py", "b.py", "c.py"]


def test_resolver_push_then_code_merge_keeps_the_full_review(tmp_path, monkeypatch) -> None:
    from sandbox_executor.flow.review.resolver import ResolverOutcome

    ctx = _turn_ctx(tmp_path, monkeypatch, _code_merge())
    ctx.review_scope_paths = ["src/stale.py"]
    target = ReviewTarget(raw=FLOW_BRANCH, head_branch=FLOW_BRANCH, head_sha="a" * 40)
    final = sync_points.finalize_resolver_turn(ctx, target, 1, ResolverOutcome(dispositions=(), pushed=True))
    assert final.error is None and final.head_sha == "b" * 40
    assert ctx.review_scope_paths == []  # The resolver's own push needs the full review; nothing narrows it.


def test_sync_scope_names_are_re_exported_from_sync_points() -> None:
    from sandbox_executor.flow.review import sync_scope

    assert sync_points.compute_rereview_scope is sync_scope.compute_rereview_scope
    assert sync_points.format_conflict_block_reason is sync_scope.format_conflict_block_reason
    assert sync_points.is_mechanical_only is sync_scope.is_mechanical_only
    assert compute_rereview_scope is sync_scope.compute_rereview_scope


def _stub_review_loop(monkeypatch: pytest.MonkeyPatch, head_sha: str) -> None:
    """Stub every remote seam of ``run_review_loop`` around the pre-iteration sync."""
    from sandbox_executor.review_target import DiffVerdict

    loop = "sandbox_executor.flow.review.loop"
    monkeypatch.setattr(f"{loop}.workspace_mounted", lambda: True)
    monkeypatch.setattr("sandbox_executor.review_target.verify_review_diff", lambda *a, **k: (DiffVerdict.VERIFIED, ""))
    target = ReviewTarget(raw=FLOW_BRANCH, head_branch=FLOW_BRANCH, head_sha=head_sha)
    monkeypatch.setattr(f"{loop}._resolve_reviewed_target", lambda *a, **k: (target, None))
    clean = {"mergeable": "MERGEABLE", "merge_state_status": "CLEAN", "is_dirty": False}
    monkeypatch.setattr(f"{loop}.check_pr_mergeability", lambda *a, **k: clean)
    checks = [{"name": "ci", "bucket": "pass"}]
    ci = {"measured": True, "all_passed": True, "passed": 1, "failed": 0, "pending": 0, "checks": checks}
    monkeypatch.setattr(f"{loop}.probe_ci_checks", lambda *a, **k: ci)
    monkeypatch.setattr(f"{loop}.post_review_consensus", lambda *a, **k: (None, {"posted": True}))


@pytest.mark.parametrize("blocked", [False, True])
def test_pre_iteration_sync_records_the_scope_before_reviewers_run(
    tmp_path, tmp_path_factory, monkeypatch, blocked
) -> None:
    from sandbox_executor.flow import run_review_loop

    from tests.review_fakes import FakeEnsemble, fake_review_request

    repos = flow_history_copy(tmp_path_factory, tmp_path, BASE, (FLOW_CODE,))
    work, head_sha = repos.work, repos.shas[0]
    merge = _code_merge() if not blocked else SyncResult("blocked", "", "", "", code_paths=(APP,), error="boom")
    monkeypatch.setattr(sync_points, "sync_review_head", lambda *a, **k: merge)
    _stub_review_loop(monkeypatch, head_sha)
    requested: list[list[str]] = []

    def _request(*args, review_paths=(), **kwargs):  # The reviewers' diff is scoped to ``review_paths``.
        requested.append(list(review_paths))
        return fake_review_request(*args, **kwargs)

    monkeypatch.setattr(ensemble, "build_review_request", _request)
    ctx, reviewers = _approve(_ctx(work)), FakeEnsemble()

    run_review_loop(
        ctx,
        FLOW_BRANCH,
        max_iterations=1,
        ensemble_size=1,
        run_ensemble_fn=reviewers,
        github=MagicMock(available=False),
    )

    assert requested == ([] if blocked else [[APP, "tests/test_app.py"]])
    assert len(reviewers.calls) == (0 if blocked else 1)  # A blocked sync never dispatches reviewers.
