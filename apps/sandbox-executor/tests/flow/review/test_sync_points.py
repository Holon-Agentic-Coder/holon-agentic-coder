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
from sandbox_executor.review_ensemble import build_review_request
from sandbox_executor.review_target import ReviewTarget

from tests.flow.helpers import _git
from tests.flow.test_sync_boundaries import APP, BASE_ROW, LEDGER, FakeResolverDispatcher, _inject_ledger_conflict
from tests.ledger_conflict_repos import FLOW_BRANCH, build_flow_repos, commit_on


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
        changed_paths=(APP, "tests/test_app.py", LEDGER),
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


def test_stage4_iteration_and_resolver_turn_boundaries_resolve_injected_conflicts(tmp_path: Path) -> None:
    _, work = build_flow_repos(tmp_path, {LEDGER: BASE_ROW, APP: "x = 0\n", ".gitignore": "tmp/\n"})
    commit_on(work, "main", {APP: "y = 2\n"}, "target code")
    commit_on(work, FLOW_BRANCH, {APP: "x = 1\n"}, "flow code")
    before = _git(work, "rev-parse", f"origin/{FLOW_BRANCH}")
    dispatcher = FakeResolverDispatcher([(_resolve_app, "resolved")])
    ctx = _ctx(work, dispatcher=dispatcher)
    target = ReviewTarget(raw=FLOW_BRANCH, head_branch=FLOW_BRANCH, head_sha=before)

    updated, mergeability, is_dirty, err = sync_iteration_mergeability(
        ctx, target, "main", 1, {"mergeable": "CONFLICTING", "merge_state_status": "DIRTY", "is_dirty": True}
    )

    assert err is None
    assert is_dirty is False and mergeability["merge_state_status"] == "CLEAN"
    assert updated is not None and updated.head_sha != before
    assert APP in ctx.review_scope_paths

    # Resolver turn boundary also resolves a newly injected target ledger conflict.
    _inject_ledger_conflict(work, FLOW_BRANCH, 4)
    before_turn = _git(work, "rev-parse", f"origin/{FLOW_BRANCH}")
    updated2, err2 = sync_after_resolver_turn(
        ctx, ReviewTarget(raw=FLOW_BRANCH, head_branch=FLOW_BRANCH, head_sha=before_turn), "main", 1
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
        changed_paths=(APP, "tests/test_app.py"),
    )
    monkeypatch.setattr(sync_points, "sync_review_head", lambda *a, **k: agent_merged)
    monkeypatch.setattr(approval_carry_over, "approval_fingerprint_of", lambda *a, **k: "pf1:" + "e" * 40)
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
            "approval_fingerprint": "pf1:" + "e" * 40,
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
        payload={"consensus": {"approved": True, "target_commit": "a" * 40, "approval_fingerprint": "pf1:" + "e" * 40}},
    )
    monkeypatch.setattr("sandbox_executor.flow.review_restart._execution_head", lambda *a, **k: "b" * 40)
    monkeypatch.setattr("sandbox_executor.flow.review_restart._git_bookkeeping_move", lambda *a, **k: lambda *_: False)
    monkeypatch.setattr("sandbox_executor.flow.review_restart.withdraw_consensus", lambda *a, **k: {"withdrawn": True})
    stopped, _ = stage5_merged_head_gate(restored)
    assert stopped is not None and stopped.payload.get("review_restart") is True
    assert restored.review_scope_paths == [APP, "tests/test_app.py"]


def test_build_review_request_scopes_diff_and_falls_back_when_empty(tmp_path: Path) -> None:
    _, work = build_flow_repos(tmp_path, {APP: "x = 0\n", "src/other.py": "z = 0\n"})
    commit_on(work, FLOW_BRANCH, {APP: "x = 1\n", "src/other.py": "z = 1\n"}, "edit both files")
    head_sha = _git(work, "rev-parse", f"origin/{FLOW_BRANCH}")
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


def test_stage5_entry_and_retry_sync_resolve_conflicts_and_block_cleanly(tmp_path: Path) -> None:
    _, work = build_flow_repos(tmp_path, {LEDGER: BASE_ROW, APP: "x = 0\n", ".gitignore": "tmp/\n"})
    _inject_ledger_conflict(work, FLOW_BRANCH, 2)
    ctx = _ctx(work)

    # Stage 5 entry sync resolves mechanical ledger conflict.
    assert sync_before_calibration(ctx, "2026-11-24T00:00:00Z") is None
    rows = [json.loads(line) for line in _git(work, "show", f"origin/{FLOW_BRANCH}:{LEDGER}").splitlines()]
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
    assert wrapped() == {"ok": True}
    assert calls == 2

    # A blocked retry sync raises permanent CalibrationSyncError without retrying further.
    commit_on(work, "main", {APP: "main = 1\n"}, "main code")
    commit_on(work, FLOW_BRANCH, {APP: "flow = 1\n"}, "flow code")
    ctx_fail = _ctx(work, dispatcher=FakeResolverDispatcher([(None, "cannot_decide")] * 3))
    with pytest.raises(CalibrationSyncError, match=r"code conflicts with main in: src/app\.py"):
        make_calibrate_retry_sync(ctx_fail)(1)
