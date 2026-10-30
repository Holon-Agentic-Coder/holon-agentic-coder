"""Unit and integration tests for Stage 4 reviewer container dispatch and result transport."""

import json
from pathlib import Path
from unittest.mock import patch

from sandbox_executor.flow import (
    FlowContext,
    FlowStage,
    StageResult,
    StageStatus,
    _dispatch_review_ensemble,
    run_review_stage,
)
from sandbox_executor.review_ensemble import (
    ConsensusVerdict,
    ParsedTrailer,
    ReviewCounts,
    ReviewerResult,
    ReviewerVerdict,
    serialize_review_outcome,
)
from sandbox_executor.review_target import DiffVerdict, ReviewTarget

EXEC_TARGET = "I-100-intent/P-200-plan/E-300-exec/_"


def _make_context(tmp_path: Path, **kwargs) -> FlowContext:
    (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True, exist_ok=True)
    ctx = FlowContext(
        repo_dir=str(tmp_path),
        execution_branch=EXEC_TARGET,
        agent="test-agent",
        model="test-model",
        token_reduce=False,
        **kwargs,
    )
    ctx.stage_results["execute"] = StageResult(
        stage=FlowStage.EXECUTE,
        status=StageStatus.SUCCESS,
        payload={"test_pass_rate": 1.0, "exit_code": 0},
    )
    return ctx


def _three_approved_reviewers() -> list[ReviewerResult]:
    p = ParsedTrailer(ReviewerVerdict.APPROVED, ReviewCounts(), ())
    return [
        ReviewerResult.completed(1, "test-agent", "test-model", "r1.md", "", p),
        ReviewerResult.completed(2, "test-agent", "test-model", "r2.md", "", p),
        ReviewerResult.completed(3, "test-agent", "test-model", "r3.md", "", p),
    ]


def test_dispatch_review_ensemble_success(tmp_path: Path) -> None:
    calls = []

    def fake_dispatcher(role, runner, model, positional_args, **kwargs):
        calls.append({"role": role, "runner": runner, "model": model, "args": positional_args, **kwargs})
        result_file = kwargs.get("result_file")
        assert result_file and Path(result_file).exists()
        payload = serialize_review_outcome(_three_approved_reviewers(), ConsensusVerdict.APPROVED)
        Path(result_file).write_text(json.dumps(payload), encoding="utf-8")
        return 0

    ctx = _make_context(tmp_path, dispatcher=fake_dispatcher)
    target = ReviewTarget(raw=EXEC_TARGET, head_branch=EXEC_TARGET, head_sha="abc1234")

    reviewers, verdict, err = _dispatch_review_ensemble(ctx, target, iteration=1, ensemble_size=3)

    assert err is None
    assert verdict == ConsensusVerdict.APPROVED
    assert len(reviewers) == 3
    assert len(calls) == 1
    assert calls[0]["role"] == "review-loop"
    assert calls[0]["runner"] == "test-agent"
    assert calls[0]["model"] == "test-model"
    assert "--ensemble-size=3" in calls[0]["args"]
    assert "--no-auto-pr" in calls[0]["args"]
    assert "--skip-post" in calls[0]["args"]
    assert "--result-file=/tmp/review_result.json" in calls[0]["args"]


def test_dispatch_review_ensemble_nonzero_exit_without_results(tmp_path: Path) -> None:
    def crashing_dispatcher(*_args, **_kwargs):
        return 137

    ctx = _make_context(tmp_path, dispatcher=crashing_dispatcher)
    target = ReviewTarget(raw=EXEC_TARGET, head_branch=EXEC_TARGET, head_sha="abc1234")

    reviewers, verdict, err = _dispatch_review_ensemble(ctx, target, iteration=1, ensemble_size=3)

    assert reviewers == []
    assert verdict is None
    assert "exited with code 137" in (err or "")


def test_dispatch_review_ensemble_handles_container_reported_error(tmp_path: Path) -> None:
    def error_dispatcher(_role, _runner, _model, _args, **kwargs):
        result_file = kwargs.get("result_file")
        payload = {"error": "Out of memory during reviewer turn 2", "reviewers": [], "ensemble_verdict": "FAILED"}
        Path(result_file).write_text(json.dumps(payload), encoding="utf-8")
        return 1

    ctx = _make_context(tmp_path, dispatcher=error_dispatcher)
    target = ReviewTarget(raw=EXEC_TARGET, head_branch=EXEC_TARGET, head_sha="abc1234")

    reviewers, verdict, err = _dispatch_review_ensemble(ctx, target, iteration=1, ensemble_size=3)

    assert reviewers == []
    assert verdict is None
    assert "Out of memory during reviewer turn 2" in (err or "")


def test_dispatch_review_ensemble_catches_dispatcher_exception(tmp_path: Path) -> None:
    def throwing_dispatcher(*_args, **_kwargs):
        raise ConnectionResetError("Docker daemon socket closed")

    ctx = _make_context(tmp_path, dispatcher=throwing_dispatcher)
    target = ReviewTarget(raw=EXEC_TARGET, head_branch=EXEC_TARGET, head_sha="abc1234")

    reviewers, verdict, err = _dispatch_review_ensemble(ctx, target, iteration=1, ensemble_size=3)

    assert reviewers == []
    assert verdict is None
    assert "Docker daemon socket closed" in (err or "")


def test_stage4_host_integrity_dirty_merge_blocks_approval(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("HOLON_IN_SANDBOX", raising=False)
    monkeypatch.delenv("HOLON_ROLE", raising=False)

    def fake_dispatcher(_role, _runner, _model, _args, **kwargs):
        result_file = kwargs.get("result_file")
        payload = serialize_review_outcome(_three_approved_reviewers(), ConsensusVerdict.APPROVED)
        Path(result_file).write_text(json.dumps(payload), encoding="utf-8")
        return 0

    ctx = _make_context(tmp_path, dispatcher=fake_dispatcher)

    with (
        patch("sandbox_executor.flow._checkout_execution_branch", return_value=None),
        patch("sandbox_executor.flow.check_and_resolve_stage_conflicts"),
        patch(
            "sandbox_executor.flow.create_or_update_pr",
            return_value={"success": True, "action": "created", "pr": {"number": 1, "url": "https://github.com/pr/1"}},
        ),
        patch(
            "sandbox_executor.flow.resolve_review_target",
            return_value=ReviewTarget(raw=EXEC_TARGET, head_branch=EXEC_TARGET, head_sha="abc1234"),
        ),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
        patch(
            "sandbox_executor.flow.check_pr_mergeability",
            return_value={"is_dirty": True, "merge_state_status": "DIRTY", "mergeable": "CONFLICTING"},
        ),
        patch(
            "sandbox_executor.flow.probe_ci_checks",
            return_value={
                "all_passed": True,
                "passed": 1,
                "failed": 0,
                "pending": 0,
                "total": 1,
                "source": "github_cli",
            },
        ),
        patch("sandbox_executor.flow.post_pr_review_consensus") as mock_post,
    ):
        result = run_review_stage(ctx)

    assert result.status is StageStatus.FAILED
    assert "Pull Request has merge conflicts" in (result.error or "")
    assert mock_post.called
    assert mock_post.call_args[1]["consensus_data"]["approved"] is False


def test_stage4_host_integrity_failed_ci_blocks_approval(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("HOLON_IN_SANDBOX", raising=False)
    monkeypatch.delenv("HOLON_ROLE", raising=False)

    def fake_dispatcher(_role, _runner, _model, _args, **kwargs):
        result_file = kwargs.get("result_file")
        payload = serialize_review_outcome(_three_approved_reviewers(), ConsensusVerdict.APPROVED)
        Path(result_file).write_text(json.dumps(payload), encoding="utf-8")
        return 0

    ctx = _make_context(tmp_path, dispatcher=fake_dispatcher)

    with (
        patch("sandbox_executor.flow._checkout_execution_branch", return_value=None),
        patch("sandbox_executor.flow.check_and_resolve_stage_conflicts"),
        patch(
            "sandbox_executor.flow.create_or_update_pr",
            return_value={"success": True, "action": "created", "pr": {"number": 1, "url": "https://github.com/pr/1"}},
        ),
        patch(
            "sandbox_executor.flow.resolve_review_target",
            return_value=ReviewTarget(raw=EXEC_TARGET, head_branch=EXEC_TARGET, head_sha="abc1234"),
        ),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
        patch(
            "sandbox_executor.flow.check_pr_mergeability",
            return_value={"is_dirty": False, "merge_state_status": "CLEAN", "mergeable": "MERGEABLE"},
        ),
        patch(
            "sandbox_executor.flow.probe_ci_checks",
            return_value={
                "all_passed": False,
                "passed": 0,
                "failed": 2,
                "pending": 0,
                "total": 2,
                "source": "github_cli",
            },
        ),
        patch("sandbox_executor.flow.post_pr_review_consensus") as mock_post,
    ):
        result = run_review_stage(ctx)

    assert result.status is StageStatus.FAILED
    assert "test suite reported failures" in (result.error or "")
    assert mock_post.called
    assert mock_post.call_args[1]["consensus_data"]["approved"] is False


def test_stage4_canonical_ledger_records_verified_results(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("HOLON_IN_SANDBOX", raising=False)
    monkeypatch.delenv("HOLON_ROLE", raising=False)

    def fake_dispatcher(_role, _runner, _model, _args, **kwargs):
        result_file = kwargs.get("result_file")
        payload = serialize_review_outcome(_three_approved_reviewers(), ConsensusVerdict.APPROVED)
        Path(result_file).write_text(json.dumps(payload), encoding="utf-8")
        return 0

    ctx = _make_context(tmp_path, dispatcher=fake_dispatcher)

    with (
        patch("sandbox_executor.flow._checkout_execution_branch", return_value=None),
        patch("sandbox_executor.flow.check_and_resolve_stage_conflicts"),
        patch(
            "sandbox_executor.flow.create_or_update_pr",
            return_value={"success": True, "action": "created", "pr": {"number": 1, "url": "https://github.com/pr/1"}},
        ),
        patch(
            "sandbox_executor.flow.resolve_review_target",
            return_value=ReviewTarget(raw=EXEC_TARGET, head_branch=EXEC_TARGET, head_sha="abc1234"),
        ),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
        patch(
            "sandbox_executor.flow.check_pr_mergeability",
            return_value={"is_dirty": False, "merge_state_status": "CLEAN", "mergeable": "MERGEABLE"},
        ),
        patch(
            "sandbox_executor.flow.probe_ci_checks",
            return_value={
                "all_passed": True,
                "passed": 1,
                "failed": 0,
                "pending": 0,
                "total": 1,
                "source": "github_cli",
            },
        ),
        patch(
            "sandbox_executor.flow.post_pr_review_consensus",
            return_value={"dual_posted": True, "official_review": {"success": True}},
        ) as mock_post,
    ):
        result = run_review_stage(ctx)

    assert result.status is StageStatus.HALTED_FOR_HUMAN
    assert result.payload["halted_for_human"] is True
    mock_post.assert_called_once()

    ledger = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"
    assert ledger.exists()
    records = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    act_rev = [r for r in records if str(r.get("action_id", "")).startswith("ACT-REV-")]
    act_post = [r for r in records if str(r.get("action_id", "")).startswith("ACT-POST-")]
    assert len(act_rev) >= 3
    assert len(act_post) == 1
