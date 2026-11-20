"""The conflict gate: no approved consensus is posted, and Stage 5 never starts, on a CONFLICTING or DIRTY head.

The mergeability probe is scripted, the post is a mock, and the merge either is a recorded fake or runs real git
against a local bare ``origin``; no test reaches GitHub.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.flow import FlowContext, StageResult, StageStatus, run_review_loop
from sandbox_executor.flow.review import conflict_gate
from sandbox_executor.flow.review.posting import PostRequest, post_review_consensus
from sandbox_executor.flow.stages import calibrate

from tests.flow.helpers import _git
from tests.ledger_conflict_repos import BRANCH, LEDGER, build_conflicting_repos, refuse_test_runs
from tests.review_fakes import FakeEnsemble, clean_reviewer
from tests.review_loop_stubs import _CLEAN_MERGE, EXEC_TARGET, _ensemble_ctx, _LoopStubs, _rows

CONFLICTING = {"mergeable": "CONFLICTING", "merge_state_status": "DIRTY", "is_dirty": True}
DIRTY = {"mergeable": "UNKNOWN", "merge_state_status": "DIRTY", "is_dirty": True}
CODE_CONFLICT = conflict_gate._MergeOutcome(merged=False, conflicting_paths=("src/app.py",))


class _RecordingMerge:
    """Stands in for the temporary-worktree merge; records each call and returns a fixed outcome."""

    def __init__(self, outcome: conflict_gate._MergeOutcome) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, str, str]] = []
        self.trailers = ""

    def __call__(
        self, repo_dir: str, branch: str, target_branch: str, trailers: str = ""
    ) -> conflict_gate._MergeOutcome:
        self.calls.append((repo_dir, branch, target_branch))
        self.trailers = trailers
        return self.outcome


@pytest.mark.parametrize(
    ("probe", "expected"),
    [
        (CONFLICTING, True),
        (DIRTY, True),
        ({"mergeable": "UNKNOWN", "merge_state_status": "UNKNOWN"}, False),
        ({"mergeable": "UNKNOWN", "merge_state_status": "MISSING_REF"}, False),
        (_CLEAN_MERGE, False),
        (None, False),
    ],
)
def test_only_an_explicit_conflict_or_dirty_state_counts(probe, expected):
    assert conflict_gate._is_conflicting(probe) is expected


def test_loop_never_posts_an_approval_when_the_head_turns_conflicting_before_the_post(tmp_path, monkeypatch):
    merge = _RecordingMerge(CODE_CONFLICT)
    monkeypatch.setattr(conflict_gate, "_merge_and_push", merge)
    probes = [dict(_CLEAN_MERGE), dict(CONFLICTING)]
    stubs = _LoopStubs()
    ctx = _ensemble_ctx(tmp_path)
    with (
        stubs,
        patch(
            "sandbox_executor.flow.review.loop.check_pr_mergeability",
            side_effect=lambda *a, **k: dict(probes.pop(0) if len(probes) > 1 else probes[0]),
        ),
    ):
        res = run_review_loop(
            ctx,
            target=EXEC_TARGET,
            max_iterations=2,
            ensemble_size=1,
            run_ensemble_fn=FakeEnsemble([clean_reviewer(1)]),
        )

    stubs.post.assert_not_called()
    assert merge.calls == [(str(tmp_path), EXEC_TARGET, "main")]
    assert res["consensus"]["approved"] is False
    assert res["consensus"]["status"] == "conflict_gate"
    assert not _rows(tmp_path, "ACT-POST")
    (gate_row,) = _rows(tmp_path, "ACT-GATE")
    assert "code conflicts with main in: src/app.py" in gate_row["error_context"]


def test_a_clean_head_posts_the_approval_unchanged(tmp_path, monkeypatch):
    merge = _RecordingMerge(CODE_CONFLICT)
    monkeypatch.setattr(conflict_gate, "_merge_and_push", merge)
    ctx = _ensemble_ctx(tmp_path)
    with _LoopStubs() as stubs:
        res = run_review_loop(
            ctx,
            target=EXEC_TARGET,
            max_iterations=2,
            ensemble_size=1,
            run_ensemble_fn=FakeEnsemble([clean_reviewer(1)]),
        )

    assert res["consensus"]["approved"] is True
    assert stubs.post.call_count == 1
    assert merge.calls == []
    assert not _rows(tmp_path, "ACT-GATE")


def _stage5_context(tmp_path: Path, **kwargs) -> FlowContext:
    return FlowContext(repo_dir=str(tmp_path), execution_branch=EXEC_TARGET, token_reduce=False, **kwargs)


@pytest.mark.parametrize("dry_run", [False, True])
def test_stage5_refuses_a_dirty_head_without_calibrating(tmp_path, monkeypatch, dry_run):
    merge = _RecordingMerge(CODE_CONFLICT)
    calibrate_branch = MagicMock()
    monkeypatch.setattr(conflict_gate, "_merge_and_push", merge)
    monkeypatch.setattr(conflict_gate.stage_conflicts, "check_pr_mergeability", lambda *a, **k: dict(DIRTY))
    monkeypatch.setattr(calibrate, "_calibrate_execution_branch", calibrate_branch)

    result = calibrate.run_calibrate_stage(_stage5_context(tmp_path, dry_run=dry_run))

    assert result.status is StageStatus.FAILED
    assert "Calibrate stage refused" in result.error
    calibrate_branch.assert_not_called()
    assert merge.calls == ([] if dry_run else [(str(tmp_path), EXEC_TARGET, "main")])


@pytest.mark.parametrize(
    ("probe", "outcome"),
    [(_CLEAN_MERGE, CODE_CONFLICT), (CONFLICTING, conflict_gate._MergeOutcome(merged=True, ledger_only=True))],
)
def test_stage5_proceeds_on_a_clean_or_freshly_merged_head(tmp_path, monkeypatch, probe, outcome):
    expected = StageResult(stage=calibrate.FlowStage.CALIBRATE, status=StageStatus.SUCCESS)
    monkeypatch.setattr(conflict_gate, "_merge_and_push", _RecordingMerge(outcome))
    monkeypatch.setattr(conflict_gate.stage_conflicts, "check_pr_mergeability", lambda *a, **k: dict(probe))
    monkeypatch.setattr(calibrate, "_calibrate_execution_branch", MagicMock(return_value=expected))

    assert calibrate.run_calibrate_stage(_stage5_context(tmp_path)) is expected


def test_ledger_only_conflict_is_merged_and_pushed_but_the_approval_is_not_posted(tmp_path, monkeypatch):
    refuse_test_runs(monkeypatch)
    origin, work = build_conflicting_repos(tmp_path)
    before = _git(origin, "rev-parse", f"refs/heads/{BRANCH}")
    exec_file = work / "review-ledger.jsonl"
    post = MagicMock()
    ctx = FlowContext(repo_dir=str(work), execution_branch=BRANCH, token_reduce=False)
    request = PostRequest(
        repo_dir=str(work),
        review_target=BRANCH,
        consensus_data={"approved": True, "base_branch": "main"},
        proxy_env=None,
        reviewers=[],
        title="t",
        iteration=1,
        mergeability_probe=lambda *a, **k: dict(CONFLICTING),
    )

    post_error, posting_res = post_review_consensus(post, ctx, str(exec_file), request)

    post.assert_not_called()
    assert post_error == f"conflict gate: {conflict_gate.MERGED_REASON}"
    assert posting_res["posted"] is False
    assert posting_res["conflict_gate"]["ledger_only"] is True
    assert _git(origin, "rev-parse", f"refs/heads/{BRANCH}") != before
    ids = [
        json.loads(line)["execution_id"] for line in _git(origin, "show", f"refs/heads/{BRANCH}:{LEDGER}").splitlines()
    ]
    assert ids == ["E-base", "E-main", "E-early", "E-late"]
    message = _git(origin, "log", "-1", "--format=%B", f"refs/heads/{BRANCH}")
    assert "Action-Origin: pr_review" in message and "Review-Iteration: 1" in message
    rows = [json.loads(line) for line in exec_file.read_text(encoding="utf-8").splitlines()]
    assert [row["action_id"].split("-")[1] for row in rows] == ["GATE"]


def test_the_gate_refuses_to_merge_into_a_protected_branch_or_after_a_failed_fetch(tmp_path):
    protected = conflict_gate._merge_and_push(str(tmp_path), "main", "feature")
    assert (protected.merged, protected.error) == (False, "refusing to push to protected branch 'main'")

    unfetched = conflict_gate._merge_and_push(str(tmp_path), "feature", "main")
    assert unfetched.merged is False
    assert unfetched.error.startswith("fetching 'main' and 'feature' failed: ")


class _SpyMerge:
    """Records, when the merge starts, the flow worktree's status and the ledger origin's branch holds; then merges."""

    def __init__(self, origin: Path, work: Path) -> None:
        self.origin, self.work = origin, work
        self.seen: list[tuple[str, str]] = []

    def __call__(self, repo_dir: str, branch: str, target_branch: str, trailers: str = ""):
        self.seen.append(
            (_git(self.work, "status", "--porcelain"), _git(self.origin, "show", f"refs/heads/{branch}:{LEDGER}"))
        )
        return _REAL_MERGE(repo_dir, branch, target_branch, trailers)


_REAL_MERGE = conflict_gate._merge_and_push


def _pending_rows_repos(tmp_path: Path) -> tuple[Path, Path]:
    """The ledger-only conflict, with the flow worktree on the head branch and one uncommitted row pending."""
    origin, work = build_conflicting_repos(tmp_path)
    _git(work, "checkout", "-q", BRANCH)
    with (work / LEDGER).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"execution_id": "E-pending", "created_at": "2026-11-06T00:00:00Z"}) + "\n")
    return origin, work


def _through_the_post_gate(work: Path, exec_file: Path) -> None:
    request = PostRequest(
        repo_dir=str(work),
        review_target=BRANCH,
        consensus_data={"approved": True, "base_branch": "main"},
        proxy_env=None,
        reviewers=[],
        title="t",
        iteration=1,
        mergeability_probe=lambda *a, **k: dict(CONFLICTING),
    )
    ctx = FlowContext(repo_dir=str(work), execution_branch=BRANCH, token_reduce=False)
    post_error, _ = post_review_consensus(MagicMock(), ctx, str(exec_file), request)
    assert post_error == f"conflict gate: {conflict_gate.MERGED_REASON}"


def _through_the_stage5_gate(work: Path, exec_file: Path) -> None:
    del exec_file
    ctx = FlowContext(repo_dir=str(work), execution_branch=BRANCH, token_reduce=False)
    assert conflict_gate._stage5_conflict_gate(ctx) is None


@pytest.mark.parametrize("gate", [_through_the_post_gate, _through_the_stage5_gate])
def test_a_conflict_merge_starts_from_a_clean_tree_with_pending_rows_committed_and_pushed(tmp_path, monkeypatch, gate):
    refuse_test_runs(monkeypatch)
    origin, work = _pending_rows_repos(tmp_path)
    monkeypatch.setattr(conflict_gate.stage_conflicts, "check_pr_mergeability", lambda *a, **k: dict(CONFLICTING))
    spy = _SpyMerge(origin, work)
    monkeypatch.setattr(conflict_gate, "_merge_and_push", spy)

    gate(work, tmp_path / "gate-ledger.jsonl")

    ((status, origin_ledger),) = spy.seen
    assert status == ""
    assert "E-pending" in origin_ledger
    merged = _git(origin, "rev-parse", f"refs/heads/{BRANCH}")
    assert _git(work, "rev-parse", "HEAD") == merged  # The local branch followed the pushed merge.
    assert _git(work, "status", "--porcelain") == ""
    ids = [json.loads(line)["execution_id"] for line in _git(origin, "show", f"{merged}:{LEDGER}").splitlines()]
    assert ids == ["E-base", "E-main", "E-early", "E-late", "E-pending"]


def test_pending_rows_that_cannot_be_pushed_block_the_gate_without_merging(tmp_path, monkeypatch):
    origin, work = _pending_rows_repos(tmp_path)
    _git(work, "stash", "-q")
    _git(work, "reset", "-q", "--hard", "HEAD~1")  # Behind origin: the ledger commit cannot fast-forward.
    _git(work, "stash", "pop", "-q")
    before = _git(origin, "rev-parse", f"refs/heads/{BRANCH}")
    merge = _RecordingMerge(CODE_CONFLICT)
    monkeypatch.setattr(conflict_gate, "_merge_and_push", merge)
    ctx = FlowContext(repo_dir=str(work), execution_branch=BRANCH, token_reduce=False)

    gate = conflict_gate._gate_conflicting_head(ctx, BRANCH, "main", CONFLICTING, "Action-Origin: pr_review")

    assert gate.blocked_reason == conflict_gate.UNPUSHED_ROWS_REASON
    assert merge.calls == []
    assert _git(origin, "rev-parse", f"refs/heads/{BRANCH}") == before
    assert _git(work, "status", "--porcelain") == ""  # The rows are safe in the local commit.
