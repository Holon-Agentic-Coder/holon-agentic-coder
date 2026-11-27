from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.flow import FlowContext, FlowStage, PipelineEngine, StageResult, StageStatus, run_calibrate_stage
from sandbox_executor.flow.review.approval_fingerprint import FINGERPRINT_VERSION
from sandbox_executor.flow.review.merged_head_gate import MergedHeadSeams
from sandbox_executor.flow.review_restart import stage5_merged_head_gate
from sandbox_executor.flow.stage5 import (
    Stage5PrerequisiteError,
    Stage5Steps,
    run_stage5,
    stage5_prerequisite_error,
    standalone_context,
)
from sandbox_executor.ref_names import calibration_branch

from tests.flow.helpers import _configure_identity

EXECUTION_BRANCH = "I-900-gate/P-901-plan/E-902-exec/_"
CALIBRATED_BRANCH = calibration_branch(EXECUTION_BRANCH)
APPROVED_SHA = "a" * 40
CARRIED_TO_SHA = "b" * 40
VALID_FINGERPRINT = f"{FINGERPRINT_VERSION}:{'f' * 40}"
SILENT_SKIP_LOG = "Merged-head CI gate skipped: no Stage 4 approval recorded"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _make_git_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _configure_identity(tmp_path)
    (tmp_path / "base.txt").write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "initial")
    _git(tmp_path, "checkout", "-q", "-b", EXECUTION_BRANCH)
    (tmp_path / "feature.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "execution change")
    return tmp_path


def _execute_result(status: StageStatus = StageStatus.SUCCESS) -> StageResult:
    return StageResult(stage=FlowStage.EXECUTE, status=status)


def _review_result(
    status: StageStatus = StageStatus.APPROVED,
    *,
    consensus: dict[str, Any] | None = None,
    posting: Any = None,
    pr: dict[str, Any] | None = None,
    review_package: dict[str, Any] | None = None,
) -> StageResult:
    payload: dict[str, Any] = {}
    if consensus is not None:
        payload["consensus"] = consensus
    if posting is not None:
        payload["posting"] = posting
    if pr is not None:
        payload["pr"] = pr
    if review_package is not None:
        payload["review_package"] = review_package
    return StageResult(stage=FlowStage.REVIEW, status=status, payload=payload)


def _approved_consensus(**extra: Any) -> dict[str, Any]:
    return {"approved": True, "target_commit": APPROVED_SHA, "pr_number": 42, **extra}


def _context(tmp_path: Path, *, dry_run: bool = False, skip_push: bool = False) -> FlowContext:
    return FlowContext(
        target_worktree=str(tmp_path),
        execution_branch=EXECUTION_BRANCH,
        plan_branch="I-900-gate/P-901-plan/_",
        agent="a",
        model="m",
        dry_run=dry_run,
        skip_push=skip_push,
    )


def _valid_context(tmp_path: Path, *, dry_run: bool = False, skip_push: bool = False) -> FlowContext:
    ctx = _context(tmp_path, dry_run=dry_run, skip_push=skip_push)
    ctx.stage_results["execute"] = _execute_result(StageStatus.SUCCESS)
    ctx.stage_results["review"] = _review_result(
        StageStatus.APPROVED,
        consensus=_approved_consensus(),
        posting={"dual_posted": True},
    )
    return ctx


def _dummy_steps(calibrate: MagicMock | None = None) -> Stage5Steps:
    calibrate_mock = calibrate or MagicMock(
        return_value=MagicMock(calibrated_branch=None, to_dict=MagicMock(return_value={}))
    )
    return Stage5Steps(
        calibrate=calibrate_mock,
        reconcile=MagicMock(return_value={}),
        promote=MagicMock(return_value=[]),
        commit_reconciliation=MagicMock(),
        commit_promoted=MagicMock(),
        warn_ledger_failure=MagicMock(),
    )


def test_missing_execute_refused_across_all_stage5_entrypoints_without_git_mutation(tmp_path: Path) -> None:
    repo = _make_git_repo(tmp_path)
    refs_before = _git(repo, "for-each-ref")
    head_before = _git(repo, "rev-parse", EXECUTION_BRANCH)

    ctx_gate = _context(repo)
    ctx_gate.stage_results["review"] = _review_result(
        StageStatus.APPROVED, consensus=_approved_consensus(), posting={"dual_posted": True}
    )
    stopped, record = stage5_merged_head_gate(ctx_gate, MergedHeadSeams(head_reader=lambda: APPROVED_SHA))
    assert stopped is not None and stopped.status == StageStatus.FAILED
    assert record["state"] == "refused"
    assert "execute" in str(stopped.error).lower()
    assert all(SILENT_SKIP_LOG not in line for line in ctx_gate.logs)

    ctx_stage = _context(repo)
    ctx_stage.stage_results["review"] = _review_result(
        StageStatus.APPROVED, consensus=_approved_consensus(), posting={"dual_posted": True}
    )
    with patch("sandbox_executor.flow.stages.calibrate.run_calibrate") as mock_calibrate:
        stage_result = run_calibrate_stage(ctx_stage)
    assert stage_result.status == StageStatus.FAILED
    mock_calibrate.assert_not_called()
    assert all(SILENT_SKIP_LOG not in line for line in ctx_stage.logs)

    ctx_direct = _context(repo)
    ctx_direct.stage_results["review"] = _review_result(
        StageStatus.APPROVED, consensus=_approved_consensus(), posting={"dual_posted": True}
    )
    direct_calibrate = MagicMock()
    with pytest.raises(Stage5PrerequisiteError, match="execute"):
        run_stage5(ctx_direct, _dummy_steps(direct_calibrate), "success", report_only=True)
    direct_calibrate.assert_not_called()

    ctx_engine = _context(repo)
    ctx_engine.stage_results["review"] = _review_result(
        StageStatus.APPROVED, consensus=_approved_consensus(), posting={"dual_posted": True}
    )
    engine = PipelineEngine(ctx_engine, from_stage=FlowStage.CALIBRATE.value)
    with (
        patch("sandbox_executor.flow.stages.calibrate.run_calibrate") as engine_calibrate,
        patch("sandbox_executor.flow.pipeline.pre_run_guard_error", return_value=None),
        patch("sandbox_executor.flow.pipeline._resume_context_error", return_value=None),
        patch("sandbox_executor.flow.pipeline.validate_stage_ledger"),
        patch.object(engine, "save_checkpoint"),
    ):
        engine_result = engine.run()
    assert engine_result[FlowStage.CALIBRATE.value].status == StageStatus.FAILED
    engine_calibrate.assert_not_called()

    assert _git(repo, "for-each-ref") == refs_before
    assert _git(repo, "rev-parse", EXECUTION_BRANCH) == head_before
    assert _git(repo, "branch", "--list", CALIBRATED_BRANCH) == ""


def test_failed_execute_is_refused_even_with_approved_posted_review(tmp_path: Path) -> None:
    ctx = _valid_context(tmp_path)
    ctx.stage_results["execute"] = _execute_result(StageStatus.FAILED)

    error = stage5_prerequisite_error(ctx)
    assert error is not None and "failed" in error

    stopped, record = stage5_merged_head_gate(ctx, MergedHeadSeams(head_reader=lambda: APPROVED_SHA))
    assert stopped is not None and stopped.status == StageStatus.FAILED
    assert record["state"] == "refused"

    calibrate = MagicMock()
    with pytest.raises(Stage5PrerequisiteError, match="failed"):
        run_stage5(ctx, _dummy_steps(calibrate), "failure", report_only=True)
    calibrate.assert_not_called()


@pytest.mark.parametrize(
    "review_status",
    [None, StageStatus.FAILED, StageStatus.HALTED_FOR_HUMAN, StageStatus.PENDING, StageStatus.SUCCESS],
)
def test_missing_or_unapproved_review_is_refused_without_silent_skip_log(
    tmp_path: Path, review_status: StageStatus | None
) -> None:
    ctx = _context(tmp_path)
    ctx.stage_results["execute"] = _execute_result(StageStatus.SUCCESS)
    if review_status is not None:
        ctx.stage_results["review"] = _review_result(
            review_status, consensus=_approved_consensus(), posting={"dual_posted": True}
        )

    stopped, record = stage5_merged_head_gate(ctx, MergedHeadSeams(head_reader=lambda: APPROVED_SHA))
    assert stopped is not None and stopped.status == StageStatus.FAILED
    assert record["state"] == "refused"
    assert all(SILENT_SKIP_LOG not in line for line in ctx.logs)

    calibrate = MagicMock()
    with pytest.raises(Stage5PrerequisiteError):
        run_stage5(ctx, _dummy_steps(calibrate), "success", report_only=True)
    calibrate.assert_not_called()


@pytest.mark.parametrize(
    "posting",
    [None, {}, {"dual_posted": False}, {"official_review": {"success": False}}],
)
def test_approved_review_without_posted_consensus_or_carried_fingerprint_is_refused(
    tmp_path: Path, posting: dict[str, Any] | None
) -> None:
    ctx = _context(tmp_path)
    ctx.stage_results["execute"] = _execute_result(StageStatus.SUCCESS)
    ctx.stage_results["review"] = _review_result(
        StageStatus.APPROVED, consensus=_approved_consensus(), posting=posting
    )

    error = stage5_prerequisite_error(ctx)
    assert error is not None and "posted GitHub consensus review" in error

    stopped, record = stage5_merged_head_gate(ctx, MergedHeadSeams(head_reader=lambda: APPROVED_SHA))
    assert stopped is not None and stopped.status == StageStatus.FAILED
    assert record["state"] == "refused"

    calibrate = MagicMock()
    with pytest.raises(Stage5PrerequisiteError):
        run_stage5(ctx, _dummy_steps(calibrate), "success", report_only=True)
    calibrate.assert_not_called()


@pytest.mark.parametrize(
    ("consensus_patch", "pr_payload"),
    [
        ({"approval_fingerprint": "pf1:" + "f" * 40, "carried_from": APPROVED_SHA, "carried_to": CARRIED_TO_SHA}, None),
        (
            {"approval_fingerprint": f"{FINGERPRINT_VERSION}:", "carried_from": APPROVED_SHA, "carried_to": CARRIED_TO_SHA},
            None,
        ),
        ({"approval_fingerprint": VALID_FINGERPRINT, "carried_from": "", "carried_to": CARRIED_TO_SHA}, None),
        ({"approval_fingerprint": VALID_FINGERPRINT, "carried_from": "HEAD", "carried_to": CARRIED_TO_SHA}, None),
        ({"approval_fingerprint": VALID_FINGERPRINT, "carried_from": APPROVED_SHA, "carried_to": ""}, None),
        ({"approval_fingerprint": VALID_FINGERPRINT, "carried_from": APPROVED_SHA, "carried_to": "HEAD"}, None),
        (
            {
                "approval_fingerprint": VALID_FINGERPRINT,
                "carried_from": APPROVED_SHA,
                "carried_to": CARRIED_TO_SHA,
                "pr_number": None,
            },
            None,
        ),
    ],
)
def test_invalid_or_incomplete_carried_consensus_fingerprint_is_refused(
    tmp_path: Path, consensus_patch: dict[str, Any], pr_payload: dict[str, Any] | None
) -> None:
    ctx = _context(tmp_path)
    ctx.stage_results["execute"] = _execute_result(StageStatus.SUCCESS)
    ctx.stage_results["review"] = _review_result(
        StageStatus.APPROVED,
        consensus=_approved_consensus(**consensus_patch),
        posting={"dual_posted": False},
        pr=pr_payload,
    )

    assert stage5_prerequisite_error(ctx) is not None
    stopped, record = stage5_merged_head_gate(ctx, MergedHeadSeams(head_reader=lambda: APPROVED_SHA))
    assert stopped is not None and stopped.status == StageStatus.FAILED
    assert record["state"] == "refused"


@pytest.mark.parametrize("flags", [{"dry_run": True}, {"skip_push": True}])
def test_refusal_holds_under_dry_run_and_skip_push(tmp_path: Path, flags: dict[str, bool]) -> None:
    ctx = _context(tmp_path, **flags)
    stopped, record = stage5_merged_head_gate(ctx, MergedHeadSeams(head_reader=lambda: APPROVED_SHA))
    assert stopped is not None and stopped.status == StageStatus.FAILED
    assert record["state"] == "refused"
    assert all(SILENT_SKIP_LOG not in line for line in ctx.logs)


@pytest.mark.parametrize(
    "review_kwargs",
    [
        {"consensus": _approved_consensus(), "posting": {"dual_posted": True}},
        {"consensus": _approved_consensus(), "posting": {"official_review": {"success": True}}},
        {"consensus": _approved_consensus(), "review_package": {"posting": {"dual_posted": True}}},
        {
            "consensus": _approved_consensus(
                approval_fingerprint=VALID_FINGERPRINT,
                carried_from=APPROVED_SHA,
                carried_to=CARRIED_TO_SHA,
            ),
            "posting": {"dual_posted": False},
        },
        {
            "consensus": {
                "approved": True,
                "target_commit": APPROVED_SHA,
                "approval_fingerprint": VALID_FINGERPRINT,
                "carried_from": APPROVED_SHA,
                "carried_to": CARRIED_TO_SHA,
            },
            "pr": {"pr_url": "https://github.com/org/repo/pull/42"},
        },
    ],
)
def test_valid_posted_or_carried_checkpoints_pass(tmp_path: Path, review_kwargs: dict[str, Any]) -> None:
    ctx = _context(tmp_path)
    ctx.stage_results["execute"] = _execute_result(StageStatus.SUCCESS)
    ctx.stage_results["review"] = _review_result(StageStatus.APPROVED, **review_kwargs)

    assert stage5_prerequisite_error(ctx) is None

    stopped, record = stage5_merged_head_gate(ctx, MergedHeadSeams(head_reader=lambda: APPROVED_SHA))
    assert stopped is None
    assert record["state"] == "stage4"

    calibrate = MagicMock(return_value=MagicMock(calibrated_branch=None, to_dict=MagicMock(return_value={})))
    run_stage5(ctx, _dummy_steps(calibrate), "success", report_only=True)
    calibrate.assert_called_once()


def test_standalone_context_bypasses_flow_stage_result_prerequisites(tmp_path: Path) -> None:
    ctx = standalone_context(EXECUTION_BRANCH, str(tmp_path), no_commit=True)
    calibrate = MagicMock(return_value=MagicMock(calibrated_branch=None, to_dict=MagicMock(return_value={})))
    run_stage5(ctx, _dummy_steps(calibrate), None, report_only=True)
    calibrate.assert_called_once()
