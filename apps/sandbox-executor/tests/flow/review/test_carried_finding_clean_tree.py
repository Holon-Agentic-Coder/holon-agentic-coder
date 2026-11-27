from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor.flow import (
    FlowContext,
    FlowStage,
    StageResult,
    StageStatus,
    load_checkpoint,
    local_tests_ci_status,
    save_checkpoint,
    serialize_open,
)
from sandbox_executor.flow.review import carried_findings, ledger_commit
from sandbox_executor.flow.review.loop import _run_engine_review_loop, run_review_loop
from sandbox_executor.review_ensemble import (
    ConsensusVerdict,
    ParsedTrailer,
    ReviewCounts,
    ReviewerResult,
    ReviewerVerdict,
    serialize_review_outcome,
)
from sandbox_executor.review_report import FindingRow
from sandbox_executor.review_target import ReviewTarget
from sandbox_executor.worktree_head import restore_branch

from tests.flow.helpers import _git
from tests.review_flow_repo import build_flow_worktree, ledger_commits
from tests.review_loop_stubs import EXEC_TARGET, FakeGitHub, ci_checks_payload, merge_view
from tests.test_review_dispatch import _host_loop_patches, _make_context

HEAD_A = "a" * 40
HEAD_B = "b" * 40
CLEAN_TREE_ERROR = "Resolver commit failed: nothing to commit, working tree clean"
CI_FINDING = FindingRow("FIND-CI-001", "important", "", None, (), "CI checks failed: pytest", "Fix CI.")
CODE_FINDING = FindingRow("R1-F1", "important", "src/app.py", 10, (1,), "missing guard", "Add guard.")


def _approving_reviewers() -> tuple[list[ReviewerResult], ConsensusVerdict]:
    clean = ParsedTrailer(ReviewerVerdict.APPROVED, ReviewCounts(), ())
    reviewers = [ReviewerResult.completed(i, "test-agent", "test-model", f"r{i}.md", "", clean) for i in (1, 2, 3)]
    return reviewers, ConsensusVerdict.APPROVED


def _dispatcher(log: list[str], error: str | None) -> Callable[..., int]:
    def fake_dispatcher(_role, _runner, _model, positional_args, **kwargs):
        result_file = Path(kwargs["result_file"])
        if "--resolve" in positional_args:
            findings = json.loads(result_file.read_text(encoding="utf-8")).get("findings", [])
            log.append("resolver:" + ",".join(f["id"] for f in findings))
            payload = {
                "resolver": True,
                "dispositions": [],
                "error": error,
                "exit_code": 1 if error else 0,
                "output_tail": error or "",
            }
            result_file.write_text(json.dumps(payload), encoding="utf-8")
            return 1 if error else 0
        log.append("ensemble")
        reviewers, verdict = _approving_reviewers()
        result_file.write_text(json.dumps(serialize_review_outcome(reviewers, verdict)), encoding="utf-8")
        return 0

    return fake_dispatcher


GREEN_CI = {
    "available": True,
    "all_passed": True,
    "failed": 0,
    "pending": 0,
    "passed": 1,
    "total": 1,
    "source": "github_cli",
    "measured": True,
}
RED_CI = {
    "available": True,
    "all_passed": False,
    "failed": 1,
    "pending": 0,
    "passed": 0,
    "total": 1,
    "source": "github_cli",
    "measured": True,
}


def _run_with_seams(
    ctx: FlowContext,
    *,
    head_sha: str = HEAD_A,
    ci_statuses: list[dict] | None = None,
    sync_fn: Callable[..., tuple[ReviewTarget | None, str | None]] | None = None,
) -> dict:
    statuses = list(ci_statuses or [GREEN_CI])
    idx = 0

    def fake_probe(*_args, **_kwargs):
        nonlocal idx
        current = statuses[min(idx, len(statuses) - 1)]
        idx += 1
        return current

    target_obj = ReviewTarget(raw=EXEC_TARGET, head_branch=EXEC_TARGET, head_sha=head_sha)
    default_sync = sync_fn or (lambda _ctx, tgt, _tb, _it: (tgt, None))
    patches = [
        *_host_loop_patches(),
        patch("sandbox_executor.flow.review.loop._resolve_reviewed_target", return_value=(target_obj, None)),
        patch("sandbox_executor.flow.review.loop.probe_ci_checks", side_effect=fake_probe),
        patch("sandbox_executor.flow.review.loop.sync_after_resolver_turn", side_effect=default_sync),
        patch("sandbox_executor.flow.review.loop._commit_pending_ledger_rows", return_value=None),
        patch("sandbox_executor.flow.review.resolver.push_commit"),
    ]
    for p in patches:
        p.start()
    try:
        return _run_engine_review_loop(ctx)
    finally:
        for p in reversed(patches):
            p.stop()


def test_stale_ci_finding_cleared_when_ci_is_already_green(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("HOLON_IN_SANDBOX", raising=False)
    monkeypatch.delenv("HOLON_ROLE", raising=False)
    log: list[str] = []
    ctx = _make_context(tmp_path, dispatcher=_dispatcher(log, CLEAN_TREE_ERROR), intent_data={"slug": "ci-green"})
    ctx.carried_review_findings = serialize_open([CI_FINDING], {})

    res = _run_with_seams(ctx)

    assert log == ["resolver:FIND-CI-001", "ensemble"]
    assert res["resolver_error"] is None
    assert res["blocked_on"] is None
    assert res["consensus"]["approved"] is True
    assert ctx.carried_review_findings == []


def test_stale_ci_finding_cleared_after_target_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("HOLON_IN_SANDBOX", raising=False)
    monkeypatch.delenv("HOLON_ROLE", raising=False)
    log: list[str] = []
    ctx = _make_context(tmp_path, dispatcher=_dispatcher(log, CLEAN_TREE_ERROR), intent_data={"slug": "ci-sync"})
    ctx.carried_review_findings = serialize_open([CI_FINDING], {})
    ci_statuses = [RED_CI, GREEN_CI, GREEN_CI]

    def sync_advances(_ctx, tgt: ReviewTarget, _tb: str, _it: int) -> tuple[ReviewTarget, None]:
        return dataclasses.replace(tgt, head_sha=HEAD_B), None

    res = _run_with_seams(ctx, head_sha=HEAD_A, ci_statuses=ci_statuses, sync_fn=sync_advances)

    assert log == ["resolver:FIND-CI-001", "ensemble"]
    assert res["resolver_error"] is None
    assert res["blocked_on"] is None
    assert res["consensus"]["approved"] is True
    assert res["consensus"]["target_commit"] == HEAD_B
    assert ctx.carried_review_findings == []


def test_non_green_ci_finding_halts_once_with_blocked_on_and_resumes_without_looping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("HOLON_IN_SANDBOX", raising=False)
    monkeypatch.delenv("HOLON_ROLE", raising=False)
    red_ci = [RED_CI]
    log1: list[str] = []
    ctx = _make_context(tmp_path, dispatcher=_dispatcher(log1, CLEAN_TREE_ERROR), intent_data={"slug": "ci-red"})
    ctx.carried_review_findings = serialize_open([CI_FINDING], {})

    res1 = _run_with_seams(ctx, head_sha=HEAD_A, ci_statuses=red_ci)

    assert log1 == ["resolver:FIND-CI-001"]
    assert res1["resolver_error"] is None
    assert res1["blocked_on"] is not None
    assert res1["blocked_on"]["head_sha"] == HEAD_A
    assert res1["blocked_on"]["finding_ids"] == ["FIND-CI-001"]
    assert res1["consensus"]["approved"] is False
    assert res1["consensus"]["passes"] == 0
    assert [f["id"] for f in ctx.carried_review_findings] == ["FIND-CI-001"]

    ckpt = save_checkpoint(ctx)
    resumed = load_checkpoint(ckpt).context
    log2: list[str] = []
    resumed.dispatcher = _dispatcher(log2, CLEAN_TREE_ERROR)

    res2 = _run_with_seams(resumed, head_sha=HEAD_A, ci_statuses=red_ci)

    assert log2 == []
    assert res2["resolver_error"] is None
    assert res2["blocked_on"]["head_sha"] == HEAD_A
    assert res2["consensus"]["approved"] is False
    assert res2["consensus"]["passes"] == 0

    log3: list[str] = []
    resumed.dispatcher = _dispatcher(log3, CLEAN_TREE_ERROR)
    res3 = _run_with_seams(resumed, head_sha=HEAD_B)

    assert log3 == ["resolver:FIND-CI-001", "ensemble"]
    assert res3["blocked_on"] is None
    assert res3["consensus"]["approved"] is True
    assert resumed.carried_review_findings == []


@pytest.mark.parametrize(
    "status",
    [
        local_tests_ci_status(),
        {**GREEN_CI, "measured": False},
        {**GREEN_CI, "total": 0},
        {**GREEN_CI, "total": None},
    ],
)
def test_non_github_or_unmeasured_or_empty_ci_is_not_green(status: dict):
    assert carried_findings._ci_status_is_green(status) is False


def test_local_tests_fallback_halts_with_blocked_on_instead_of_clearing_ci_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("HOLON_IN_SANDBOX", raising=False)
    monkeypatch.delenv("HOLON_ROLE", raising=False)
    log: list[str] = []
    ctx = _make_context(tmp_path, dispatcher=_dispatcher(log, CLEAN_TREE_ERROR), intent_data={"slug": "ci-fallback"})
    ctx.carried_review_findings = serialize_open([CI_FINDING], {})

    res = _run_with_seams(ctx, head_sha=HEAD_A, ci_statuses=[local_tests_ci_status()])

    assert log == ["resolver:FIND-CI-001"]
    assert res["resolver_error"] is None
    assert res["blocked_on"] is not None
    assert res["blocked_on"]["head_sha"] == HEAD_A
    assert res["blocked_on"]["finding_ids"] == ["FIND-CI-001"]
    assert res["consensus"]["approved"] is False
    assert res["consensus"]["passes"] == 0
    assert [f["id"] for f in ctx.carried_review_findings] == ["FIND-CI-001"]


def test_non_ci_carried_finding_clean_tree_halts_once_with_blocked_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("HOLON_IN_SANDBOX", raising=False)
    monkeypatch.delenv("HOLON_ROLE", raising=False)
    log1: list[str] = []
    ctx = _make_context(tmp_path, dispatcher=_dispatcher(log1, CLEAN_TREE_ERROR), intent_data={"slug": "code-clean"})
    ctx.carried_review_findings = serialize_open([CODE_FINDING], {})

    res1 = _run_with_seams(ctx, head_sha=HEAD_A)

    assert log1 == ["resolver:R1-F1"]
    assert res1["resolver_error"] is None
    assert res1["blocked_on"]["finding_ids"] == ["R1-F1"]
    assert res1["consensus"]["approved"] is False
    assert res1["consensus"]["passes"] == 0

    ckpt = save_checkpoint(ctx)
    resumed = load_checkpoint(ckpt).context
    log2: list[str] = []
    resumed.dispatcher = _dispatcher(log2, CLEAN_TREE_ERROR)

    res2 = _run_with_seams(resumed, head_sha=HEAD_A)

    assert log2 == []
    assert res2["resolver_error"] is None
    assert res2["blocked_on"]["finding_ids"] == ["R1-F1"]
    assert res2["consensus"]["passes"] == 0


def _origin_tip(origin: Path, branch: str = EXEC_TARGET) -> str:
    return _git(origin, "rev-parse", f"refs/heads/{branch}")


def _real_git_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    origin, work = build_flow_worktree(tmp_path)
    _git(work, "push", "-q", "origin", "main:refs/heads/I-100-intent/P-200-plan/_")
    _git(work, "fetch", "-q", "origin")
    return origin, work, _git(work, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("error_text", "finding"),
    [
        ("You've hit your session limit", CODE_FINDING),
        (CLEAN_TREE_ERROR, CODE_FINDING),
    ],
)
def test_zero_ledger_commits_or_pushes_on_pre_iteration_failure_or_blocked_on(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_text: str,
    finding: FindingRow,
):
    monkeypatch.delenv("HOLON_IN_SANDBOX", raising=False)
    monkeypatch.delenv("HOLON_ROLE", raising=False)
    origin, work, head_before = _real_git_repo(tmp_path)
    origin_before = _origin_tip(origin)
    github = FakeGitHub(standing_view=merge_view(), checks=ci_checks_payload(passed=1), head_sha=head_before)
    log: list[str] = []
    ctx = FlowContext(
        repo_dir=str(work),
        execution_branch=EXEC_TARGET,
        token_reduce=False,
        intent_data={"slug": "no-empty-ledger"},
        carried_review_findings=serialize_open([finding], {}),
        dispatcher=_dispatcher(log, error_text),
    )
    target_obj = ReviewTarget(raw=EXEC_TARGET, head_branch=EXEC_TARGET, head_sha=head_before)
    with patch("sandbox_executor.flow.review.loop._resolve_reviewed_target", return_value=(target_obj, None)):
        res = run_review_loop(ctx, target=EXEC_TARGET, max_iterations=3, auto_pr=False, skip_post=True, github=github)
    ledger_commit._close_review_stage(ctx, [EXEC_TARGET], restore_branch)

    assert log == [f"resolver:{finding.id}"]
    assert res["consensus"]["passes"] == 0
    assert res["consensus"]["approved"] is False
    assert _git(work, "rev-parse", "HEAD") == head_before
    assert _origin_tip(origin) == origin_before
    assert ledger_commits(work) == []


def test_normal_iteration_ledger_commit_still_works_after_cleared_suppression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("HOLON_IN_SANDBOX", raising=False)
    monkeypatch.delenv("HOLON_ROLE", raising=False)
    origin, work, head_before = _real_git_repo(tmp_path)
    github = FakeGitHub(standing_view=merge_view(), checks=ci_checks_payload(passed=1), head_sha=head_before)
    log: list[str] = []
    ctx = FlowContext(
        repo_dir=str(work),
        execution_branch=EXEC_TARGET,
        token_reduce=False,
        intent_data={"slug": "recover-ledger"},
        carried_review_findings=serialize_open([CODE_FINDING], {}),
        dispatcher=_dispatcher(log, CLEAN_TREE_ERROR),
    )
    target_obj = ReviewTarget(raw=EXEC_TARGET, head_branch=EXEC_TARGET, head_sha=head_before)
    with patch("sandbox_executor.flow.review.loop._resolve_reviewed_target", return_value=(target_obj, None)):
        run_review_loop(ctx, target=EXEC_TARGET, max_iterations=3, auto_pr=False, skip_post=True, github=github)
    ledger_commit._close_review_stage(ctx, [EXEC_TARGET], restore_branch)
    assert ledger_commits(work) == []

    ctx.carried_review_findings = []
    ctx.stage_results["execute"] = StageResult(
        stage=FlowStage.EXECUTE,
        status=StageStatus.SUCCESS,
        payload={"head_sha": "stage3-head", "test_pass_rate": 1.0, "exit_code": 0},
    )
    with (
        patch("sandbox_executor.flow.review.loop._resolve_reviewed_target", return_value=(target_obj, None)),
        patch("sandbox_executor.flow.review.loop.probe_ci_checks", return_value=GREEN_CI),
    ):
        res = run_review_loop(ctx, target=EXEC_TARGET, max_iterations=3, auto_pr=False, skip_post=True, github=github)
    ledger_commit._close_review_stage(ctx, [EXEC_TARGET], restore_branch)

    assert res["consensus"]["approved"] is True
    assert len(ledger_commits(work)) >= 1
    assert _origin_tip(origin) == _git(work, "rev-parse", "HEAD")
