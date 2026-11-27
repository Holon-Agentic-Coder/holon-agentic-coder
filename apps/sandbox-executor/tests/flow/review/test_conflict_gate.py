"""The conflict gate: no approved consensus is posted, and Stage 5 never starts, on a CONFLICTING or DIRTY head.

The mergeability reads and the post are answered by an injected ``FakeGitHub``, and the merge either is a recorded
fake or runs real git against a local bare ``origin``; no test reaches GitHub. The conflict-resolver role dispatch is
stubbed, so no test reaches the container launcher.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sandbox_executor.agent_runner import CONFLICT_RESOLVER_ROLE
from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus, run_review_loop
from sandbox_executor.flow.review import conflict_gate, sync_points
from sandbox_executor.flow.review.posting import PostRequest, post_review_consensus
from sandbox_executor.flow.stages import calibrate
from sandbox_executor.flow.target_sync_result import SyncBoundary, SyncResult
from sandbox_executor.ledger import read_merged_rows, read_merged_rows_at_ref
from sandbox_executor.review_report import _mergeability_word

from tests.flow.github.fake_github import view_for
from tests.flow.helpers import _git
from tests.ledger_conflict_repos import BRANCH, LEDGER, build_conflicting_repos, refuse_test_runs
from tests.review_fakes import FakeEnsemble
from tests.review_loop_stubs import _CLEAN_MERGE, EXEC_TARGET, _ensemble_ctx, _LoopStubs, _rows

CONFLICTING = {"mergeable": "CONFLICTING", "merge_state_status": "DIRTY", "is_dirty": True}
DIRTY = {"mergeable": "UNKNOWN", "merge_state_status": "DIRTY", "is_dirty": True}
CODE_CONFLICT = SyncResult(
    "blocked",
    "a" * 40,
    "a" * 40,
    "b" * 40,
    code_paths=("src/app.py",),
    conflicted_paths=("src/app.py",),
    error="code conflicts unresolved: src/app.py",
)
LEDGER_MERGED = SyncResult(
    "merged",
    "a" * 40,
    "b" * 40,
    "c" * 40,
    resolutions=((LEDGER, "ledger", "mechanical"),),
)


class _RecordingSync:
    """Stands in for ``sync_points.sync_review_head``; records each call and returns a fixed ``SyncResult``."""

    def __init__(self, result: SyncResult) -> None:
        self.result = result
        self.calls: list[tuple[str, str, str]] = []

    def __call__(
        self, context: FlowContext, branch: str, target_branch: str, boundary: SyncBoundary, **kwargs
    ) -> SyncResult:
        del kwargs
        if boundary is SyncBoundary.TURN:
            return SyncResult("unchanged", "", "", "")
        self.calls.append((context.target_worktree, branch, target_branch))
        return self.result


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
    merge = _RecordingSync(CODE_CONFLICT)
    monkeypatch.setattr(sync_points, "sync_review_head", merge)
    stubs = _LoopStubs()
    # The iteration reads a clean head; every later read (the pre-post conflict gate) sees it conflicting.
    stubs.github.views = [view_for(_CLEAN_MERGE)]
    stubs.github.standing_view = view_for(CONFLICTING)
    ctx = _ensemble_ctx(tmp_path)
    with stubs:
        res = run_review_loop(
            ctx,
            target=EXEC_TARGET,
            max_iterations=2,
            ensemble_size=3,
            run_ensemble_fn=FakeEnsemble(),
        )

    assert stubs.posts() == []
    assert merge.calls == [(str(tmp_path), EXEC_TARGET, "main")]
    assert res["consensus"]["approved"] is False
    assert res["consensus"]["status"] == "conflict_gate"
    assert not _rows(tmp_path, "ACT-POST")
    (gate_row,) = _rows(tmp_path, "ACT-GATE")
    assert "code conflicts with main in: src/app.py" in gate_row["error_context"]


def test_a_clean_head_posts_the_approval_unchanged(tmp_path, monkeypatch):
    merge = _RecordingSync(CODE_CONFLICT)
    monkeypatch.setattr(sync_points, "sync_review_head", merge)
    ctx = _ensemble_ctx(tmp_path)
    with _LoopStubs() as stubs:
        res = run_review_loop(
            ctx,
            target=EXEC_TARGET,
            max_iterations=2,
            ensemble_size=3,
            run_ensemble_fn=FakeEnsemble(),
        )

    assert res["consensus"]["approved"] is True
    assert len(stubs.posts()) == 1
    assert merge.calls == []
    assert not _rows(tmp_path, "ACT-GATE")


def _stage5_context(tmp_path: Path, **kwargs) -> FlowContext:
    ctx = FlowContext(repo_dir=str(tmp_path), execution_branch=EXEC_TARGET, token_reduce=False, **kwargs)
    ctx.stage_results["execute"] = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS)
    ctx.stage_results["review"] = StageResult(
        stage=FlowStage.REVIEW,
        status=StageStatus.APPROVED,
        payload={
            "consensus": {"approved": True, "target_commit": "a" * 40, "pr_number": 7},
            "posting": {"dual_posted": True},
        },
    )
    return ctx


@pytest.mark.parametrize("dry_run", [False, True])
def test_stage5_refuses_a_dirty_head_without_calibrating(tmp_path, monkeypatch, dry_run):
    merge = _RecordingSync(CODE_CONFLICT)
    calibrate_branch = MagicMock()
    monkeypatch.setattr(sync_points, "sync_review_head", merge)
    monkeypatch.setattr(conflict_gate.stage_conflicts, "check_pr_mergeability", lambda *a, **k: dict(DIRTY))
    monkeypatch.setattr(calibrate, "_calibrate_execution_branch", calibrate_branch)

    result = calibrate.run_calibrate_stage(_stage5_context(tmp_path, dry_run=dry_run))

    assert result.status is StageStatus.FAILED
    assert "Calibrate stage refused" in result.error
    calibrate_branch.assert_not_called()
    assert merge.calls == ([] if dry_run else [(str(tmp_path), EXEC_TARGET, "main")])


@pytest.mark.parametrize(
    ("probe", "outcome"),
    [(_CLEAN_MERGE, CODE_CONFLICT), (CONFLICTING, LEDGER_MERGED)],
)
def test_stage5_proceeds_on_a_clean_or_freshly_merged_head(tmp_path, monkeypatch, probe, outcome):
    expected = StageResult(stage=calibrate.FlowStage.CALIBRATE, status=StageStatus.SUCCESS)
    monkeypatch.setattr(sync_points, "sync_review_head", _RecordingSync(outcome))
    monkeypatch.setattr(conflict_gate.stage_conflicts, "check_pr_mergeability", lambda *a, **k: dict(probe))
    monkeypatch.setattr(calibrate, "stage5_merged_head_gate", lambda _ctx: (None, {"state": "stage4"}))
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
    merged_rows = read_merged_rows_at_ref(str(origin), f"refs/heads/{BRANCH}", "executions.jsonl")
    assert [r["execution_id"] for r in merged_rows[:4]] == ["E-base", "E-early", "E-main", "E-late"]
    assert merged_rows[-1]["action"] == "target_sync"
    message = _git(origin, "log", "-1", "--format=%B", f"refs/heads/{BRANCH}")
    assert "sync: merge target" in message
    rows = [json.loads(line) for line in exec_file.read_text(encoding="utf-8").splitlines()]
    assert [row["action_id"].split("-")[1] for row in rows] == ["GATE"]


def test_the_gate_refuses_to_merge_into_a_protected_branch_or_after_a_failed_fetch(tmp_path):
    ctx = FlowContext(repo_dir=str(tmp_path), execution_branch="main", token_reduce=False)
    protected = sync_points.sync_review_head(ctx, "main", "feature", SyncBoundary.PUSH)
    assert (protected.status, protected.error) == ("blocked", "refusing to push to protected branch 'main'")

    unfetched = sync_points.sync_review_head(ctx, "feature", "main", SyncBoundary.PUSH)
    assert unfetched.status == "blocked"
    assert unfetched.error.startswith("fetching 'main' and 'feature' failed")


class _SpySync:
    """Records, when the sync starts, the flow worktree's status and the ledger origin's branch holds; then syncs."""

    def __init__(self, origin: Path, work: Path) -> None:
        self.origin, self.work = origin, work
        self.seen: list[tuple[str, str]] = []

    def __call__(self, context: FlowContext, branch: str, target_branch: str, boundary: SyncBoundary, **kwargs):
        self.seen.append(
            (_git(self.work, "status", "--porcelain"), _git(self.origin, "show", f"refs/heads/{branch}:{LEDGER}"))
        )
        return _REAL_SYNC(context, branch, target_branch, boundary, **kwargs)


_REAL_SYNC = sync_points.sync_review_head


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
    spy = _SpySync(origin, work)
    monkeypatch.setattr(sync_points, "sync_review_head", spy)

    gate(work, tmp_path / "gate-ledger.jsonl")

    ((status, origin_ledger),) = spy.seen
    assert status == ""
    assert "E-pending" in origin_ledger
    merged = _git(origin, "rev-parse", f"refs/heads/{BRANCH}")
    assert _git(work, "rev-parse", "HEAD") == merged  # The local branch followed the pushed merge.
    assert _git(work, "status", "--porcelain") == ""
    ids = [json.loads(line)["execution_id"] for line in _git(origin, "show", f"{merged}:{LEDGER}").splitlines()]
    assert ids[:5] == ["E-base", "E-early", "E-main", "E-late", "E-pending"]


def _unpushable_rows_repos(tmp_path: Path) -> tuple[Path, Path]:
    """The pending-rows repos with the local branch behind origin, so a ledger commit cannot fast-forward."""
    origin, work = _pending_rows_repos(tmp_path)
    _git(work, "stash", "-q")
    _git(work, "reset", "-q", "--hard", "HEAD~1")
    _git(work, "stash", "pop", "-q")
    return origin, work


def test_pending_rows_that_cannot_be_pushed_block_the_gate_without_merging(tmp_path, monkeypatch):
    origin, work = _unpushable_rows_repos(tmp_path)
    before = _git(origin, "rev-parse", f"refs/heads/{BRANCH}")
    merge = _RecordingSync(CODE_CONFLICT)
    monkeypatch.setattr(sync_points, "sync_review_head", merge)
    ctx = FlowContext(repo_dir=str(work), execution_branch=BRANCH, token_reduce=False)

    gate = conflict_gate._gate_conflicting_head(
        ctx, BRANCH, "main", CONFLICTING, conflict_gate._GateMerge("Action-Origin: pr_review")
    )

    assert gate.blocked_reason == conflict_gate.UNPUSHED_ROWS_REASON
    assert merge.calls == []
    assert _git(origin, "rev-parse", f"refs/heads/{BRANCH}") == before
    assert _git(work, "status", "--porcelain") == ""  # The rows are safe in the local commit.


def test_stage5_rows_whose_push_was_rejected_block_the_merge(tmp_path, monkeypatch):
    origin, work = _unpushable_rows_repos(tmp_path)
    before = _git(origin, "rev-parse", f"refs/heads/{BRANCH}")
    merge = _RecordingSync(CODE_CONFLICT)
    monkeypatch.setattr(sync_points, "sync_review_head", merge)
    monkeypatch.setattr(conflict_gate.stage_conflicts, "check_pr_mergeability", lambda *a, **k: dict(CONFLICTING))
    ctx = FlowContext(repo_dir=str(work), execution_branch=BRANCH, token_reduce=False, review_iterations_done=2)

    result = conflict_gate._stage5_conflict_gate(ctx)

    assert result is not None and result.status is StageStatus.FAILED
    assert conflict_gate.UNPUSHED_ROWS_REASON in result.error
    assert merge.calls == []
    assert _git(origin, "rev-parse", f"refs/heads/{BRANCH}") == before
    message = _git(work, "log", "-1", "--format=%B")
    assert "(before Stage 5)" in message and "Review-Iteration: 2" in message


def _committed_rows_repos(tmp_path: Path, *, diverged: bool) -> tuple[Path, Path]:
    """An earlier ledger commit left unpushed on the local branch (diverged from origin when ``diverged``)."""
    origin, work = _unpushable_rows_repos(tmp_path) if diverged else _pending_rows_repos(tmp_path)
    _git(work, "commit", "-q", "-am", "earlier ledger rows")
    return origin, work


def test_an_earlier_unpushed_ledger_commit_is_pushed_before_the_merge(tmp_path, monkeypatch):
    refuse_test_runs(monkeypatch)
    origin, work = _committed_rows_repos(tmp_path, diverged=False)
    spy = _SpySync(origin, work)
    monkeypatch.setattr(sync_points, "sync_review_head", spy)
    ctx = FlowContext(repo_dir=str(work), execution_branch=BRANCH, token_reduce=False)

    gate = conflict_gate._gate_conflicting_head(
        ctx, BRANCH, "main", CONFLICTING, conflict_gate._GateMerge("Action-Origin: pr_review")
    )

    assert gate.blocked_reason == conflict_gate.MERGED_REASON
    ((_, origin_ledger),) = spy.seen
    assert "E-pending" in origin_ledger
    assert _git(work, "rev-parse", "HEAD") == _git(origin, "rev-parse", f"refs/heads/{BRANCH}")


def test_an_earlier_ledger_commit_that_cannot_be_pushed_blocks_the_merge(tmp_path, monkeypatch):
    origin, work = _committed_rows_repos(tmp_path, diverged=True)
    before = _git(origin, "rev-parse", f"refs/heads/{BRANCH}")
    merge = _RecordingSync(CODE_CONFLICT)
    monkeypatch.setattr(sync_points, "sync_review_head", merge)
    ctx = FlowContext(repo_dir=str(work), execution_branch=BRANCH, token_reduce=False)

    gate = conflict_gate._gate_conflicting_head(
        ctx, BRANCH, "main", CONFLICTING, conflict_gate._GateMerge("Action-Origin: pr_review")
    )

    assert gate.blocked_reason == conflict_gate.UNPUSHED_ROWS_REASON
    assert merge.calls == []
    assert _git(origin, "rev-parse", f"refs/heads/{BRANCH}") == before


def test_an_unpushed_code_commit_blocks_the_merge_and_is_never_pushed(tmp_path, monkeypatch):
    origin, work = _pending_rows_repos(tmp_path)
    _git(work, "stash", "-q")
    (work / "resolver.py").write_text("x = 1\n", encoding="utf-8")
    _git(work, "add", "resolver.py")
    _git(work, "commit", "-q", "-m", "resolver commit the host refused to push")
    _git(work, "stash", "pop", "-q")
    before = _git(origin, "rev-parse", f"refs/heads/{BRANCH}")
    merge = _RecordingSync(CODE_CONFLICT)
    monkeypatch.setattr(sync_points, "sync_review_head", merge)
    ctx = FlowContext(repo_dir=str(work), execution_branch=BRANCH, token_reduce=False)

    gate = conflict_gate._gate_conflicting_head(
        ctx, BRANCH, "main", CONFLICTING, conflict_gate._GateMerge("Action-Origin: pr_review")
    )

    assert gate.blocked_reason == conflict_gate.UNPUSHED_ROWS_REASON
    assert merge.calls == []
    assert _git(origin, "rev-parse", f"refs/heads/{BRANCH}") == before


def test_the_gate_never_pushes_an_unpushed_code_commit(tmp_path):
    origin, work = _pending_rows_repos(tmp_path)
    _git(work, "commit", "-q", "-am", "earlier ledger rows")
    (work / "resolver.py").write_text("x = 1\n", encoding="utf-8")
    _git(work, "add", "resolver.py")
    _git(work, "commit", "-q", "-m", "resolver commit the host refused to push")
    before = _git(origin, "rev-parse", f"refs/heads/{BRANCH}")
    ctx = FlowContext(repo_dir=str(work), execution_branch=BRANCH, token_reduce=False)

    assert conflict_gate._local_commits_unpushed(ctx, BRANCH)
    assert _git(origin, "rev-parse", f"refs/heads/{BRANCH}") == before


# --- Approval carry-over across a mechanical target merge ------------------------------------------------------------

GREEN_AT_A = {"total": 2, "passed": 2, "failed": 0, "pending": 0, "measured": True}


def _approved_at_head(work: Path, origin: Path, *, fingerprinted: bool = True) -> dict:
    """The consensus data of an approval of origin's head commit, fingerprinted against origin/main."""
    from sandbox_executor.flow.review.approval_fingerprint import approval_fingerprint_of

    head = _git(origin, "rev-parse", f"refs/heads/{BRANCH}")
    _git(work, "fetch", "-q", "origin")
    data = {"approved": True, "base_branch": "main", "target_commit": head, "ci_checks": dict(GREEN_AT_A)}
    data["mergeability"] = {"merge_state_status": "CLEAN", "mergeable": "MERGEABLE", "is_dirty": False}  # A's probe.
    if fingerprinted:
        data["approval_fingerprint"] = approval_fingerprint_of(str(work), head, "main")
        assert str(data["approval_fingerprint"]).startswith("pf2:")
    return data


def _post_through_gate(work: Path, exec_file: Path, consensus_data: dict) -> tuple[str | None, dict, MagicMock]:
    post = MagicMock(return_value={"dual_posted": True, "official_review": {"success": True}})
    request = PostRequest(
        repo_dir=str(work),
        review_target=BRANCH,
        consensus_data=consensus_data,
        proxy_env=None,
        reviewers=[],
        title="t",
        iteration=1,
        mergeability_probe=lambda *a, **k: dict(CONFLICTING),
    )
    ctx = FlowContext(repo_dir=str(work), execution_branch=BRANCH, token_reduce=False)
    post_error, posting_res = post_review_consensus(post, ctx, str(exec_file), request)
    return post_error, posting_res, post


def _gate_rows(exec_file: Path) -> list[dict]:
    return [json.loads(line) for line in exec_file.read_text(encoding="utf-8").splitlines()]


def test_a_ledger_only_merge_with_the_same_fingerprint_carries_the_approval_over(tmp_path, monkeypatch):
    refuse_test_runs(monkeypatch)
    origin, work = build_conflicting_repos(tmp_path)
    consensus_data = _approved_at_head(work, origin)
    approved = consensus_data["target_commit"]
    exec_file = tmp_path / "gate-ledger.jsonl"
    earlier = json.dumps({"action_id": "ACT-EARLIER-1", "execution_id": "E-0"}) + "\n"
    exec_file.write_text(earlier, encoding="utf-8")

    post_error, posting_res, post = _post_through_gate(work, exec_file, consensus_data)

    merged = _git(origin, "rev-parse", f"refs/heads/{BRANCH}")
    assert merged != approved
    assert post_error is None and posting_res["dual_posted"] is True
    post.assert_called_once()
    assert post.call_args.kwargs["consensus_data"] is consensus_data
    assert consensus_data["target_commit"] == approved  # The SHA the reviewers saw is never rewritten.
    assert (consensus_data["carried_from"], consensus_data["carried_to"]) == (approved, merged)
    assert consensus_data["ci_checks"]["measured"] is False  # Never A's green CI for B.
    assert _mergeability_word(consensus_data) == "UNMEASURED"  # Nor A's clean probe for the unprobed B.
    text = exec_file.read_text(encoding="utf-8")
    assert text.startswith(earlier)  # Append-only: earlier bytes are a prefix.
    rows = _gate_rows(exec_file)
    assert [row["action_id"].split("-")[1] for row in rows] == ["EARLIER", "CARRY", "POST"]
    carry = rows[1]
    assert (carry["approved_head"], carry["merged_head"]) == (approved, merged)
    assert carry["approval_fingerprint"] == consensus_data["approval_fingerprint"]
    assert carry["outcome"] == "success" and "approval-carry-over" in carry["labels"]


def test_the_carry_over_is_logged_with_both_shas_and_the_fingerprint(tmp_path, monkeypatch):
    refuse_test_runs(monkeypatch)
    origin, work = build_conflicting_repos(tmp_path)
    consensus_data = _approved_at_head(work, origin)
    logged: list[str] = []
    monkeypatch.setattr(FlowContext, "log", lambda self, message: logged.append(message))

    _post_through_gate(work, tmp_path / "gate-ledger.jsonl", consensus_data)

    (line,) = [message for message in logged if "approval carried over" in message]
    assert consensus_data["target_commit"] in line and consensus_data["carried_to"] in line
    assert consensus_data["approval_fingerprint"] in line and "CI on" in line


def test_a_pr_own_change_after_approval_blocks_the_merged_head(tmp_path, monkeypatch):
    refuse_test_runs(monkeypatch)
    origin, work = build_conflicting_repos(tmp_path)
    consensus_data = _approved_at_head(work, origin)
    _git(work, "checkout", "-q", BRANCH)
    (work / "src/feature.py").write_text("x = 2\n", encoding="utf-8")
    _git(work, "commit", "-q", "-am", "post-approval push")
    _git(work, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    exec_file = tmp_path / "gate-ledger.jsonl"

    post_error, posting_res, post = _post_through_gate(work, exec_file, consensus_data)

    post.assert_not_called()
    assert post_error == f"conflict gate: {conflict_gate.MERGED_REASON}"
    assert posting_res["conflict_gate"]["merged"] is True
    assert "carried_to" not in consensus_data
    assert [row["action_id"].split("-")[1] for row in _gate_rows(exec_file)] == ["GATE"]


def test_a_code_conflict_still_aborts_with_a_fingerprinted_approval(tmp_path, monkeypatch):
    refuse_test_runs(monkeypatch)
    origin, work = build_conflicting_repos(tmp_path)
    consensus_data = _approved_at_head(work, origin)
    before = consensus_data["target_commit"]
    (work / "src").mkdir(exist_ok=True)
    (work / "src/feature.py").write_text("x = 3\n", encoding="utf-8")
    _git(work, "add", "src/feature.py")
    _git(work, "commit", "-q", "-m", "main edits the PR's file")
    _git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    exec_file = tmp_path / "gate-ledger.jsonl"
    # A failing exit code, never a raise: the resolver would record a raised assertion as agent_failed and hide it.
    dispatch = MagicMock(return_value=1)
    monkeypatch.setattr("sandbox_executor.flow.conflict_resolver_role._dispatch_role", dispatch)

    post_error, _, post = _post_through_gate(work, exec_file, consensus_data)

    post.assert_not_called()
    assert dispatch.call_count >= 1  # The code-conflict path ran through the resolver.
    assert {call.args[1] for call in dispatch.call_args_list} == {CONFLICT_RESOLVER_ROLE}
    assert all(call.kwargs["target_worktree"] for call in dispatch.call_args_list)
    assert post_error is not None and "code conflicts with main in:" in post_error and "src/feature.py" in post_error
    assert _git(origin, "rev-parse", f"refs/heads/{BRANCH}") == before
    assert [row["action_id"].split("-")[1] for row in _gate_rows(exec_file)] == ["GATE"]


def test_a_legacy_approval_without_a_fingerprint_is_blocked_as_before(tmp_path, monkeypatch):
    refuse_test_runs(monkeypatch)
    origin, work = build_conflicting_repos(tmp_path)
    consensus_data = _approved_at_head(work, origin, fingerprinted=False)
    exec_file = tmp_path / "gate-ledger.jsonl"

    post_error, _, post = _post_through_gate(work, exec_file, consensus_data)

    post.assert_not_called()
    assert post_error == f"conflict gate: {conflict_gate.MERGED_REASON}"
    assert [row["action_id"].split("-")[1] for row in _gate_rows(exec_file)] == ["GATE"]


def test_a_carried_approval_ends_the_loop_approved_without_another_reviewer_dispatch(tmp_path, monkeypatch):
    merged_head = "b" * 40
    merge = _RecordingSync(
        SyncResult("merged", "a" * 40, merged_head, "c" * 40, resolutions=((LEDGER, "ledger", "mechanical"),))
    )
    monkeypatch.setattr(sync_points, "sync_review_head", merge)
    same_change = MagicMock(return_value="pf2:" + "c" * 40)
    monkeypatch.setattr("sandbox_executor.flow.review.loop.approval_fingerprint_of", same_change)
    monkeypatch.setattr("sandbox_executor.flow.review.approval_carry_over.approval_fingerprint_of", same_change)
    ensemble = FakeEnsemble()
    stubs = _LoopStubs()
    stubs.github.views = [view_for(_CLEAN_MERGE)]
    stubs.github.standing_view = view_for(CONFLICTING)
    ctx = _ensemble_ctx(tmp_path)
    with stubs:
        res = run_review_loop(ctx, target=EXEC_TARGET, max_iterations=3, ensemble_size=3, run_ensemble_fn=ensemble)

    assert len(ensemble.calls) == 1  # The single approving pass; no reviewer is dispatched again.
    assert len(stubs.posts()) == 1
    assert res["consensus"]["approved"] is True and res["consensus"]["status"] == "approved"
    approved = res["consensus"]["target_commit"]  # The SHA the reviewers saw, never the merged head.
    assert approved and approved != merged_head
    assert (res["consensus"]["carried_from"], res["consensus"]["carried_to"]) == (approved, merged_head)
    assert res["consensus"]["approval_fingerprint"] == "pf2:" + "c" * 40
    (carry,) = _rows(tmp_path, "ACT-CARRY")
    assert carry["merged_head"] == merged_head
    assert not _rows(tmp_path, "ACT-GATE")


def test_stage5_records_one_carry_over_row_for_a_merge_that_kept_the_change(tmp_path, monkeypatch):
    refuse_test_runs(monkeypatch)
    origin, work = build_conflicting_repos(tmp_path)
    consensus_data = _approved_at_head(work, origin)
    _git(work, "checkout", "-q", BRANCH)
    monkeypatch.setattr(conflict_gate.stage_conflicts, "check_pr_mergeability", lambda *a, **k: dict(CONFLICTING))
    ctx = FlowContext(repo_dir=str(work), execution_branch=BRANCH, token_reduce=False)
    ctx.stage_results["review"] = StageResult(
        stage=calibrate.FlowStage.REVIEW, status=StageStatus.APPROVED, payload={"consensus": dict(consensus_data)}
    )
    ledger = str(work / LEDGER)
    before = read_merged_rows(ledger)

    assert conflict_gate._stage5_conflict_gate(ctx) is None

    merged = _git(origin, "rev-parse", f"refs/heads/{BRANCH}")
    after = read_merged_rows(ledger)
    merged_rows = read_merged_rows_at_ref(str(work), merged, "executions.jsonl")
    assert after[: len(merged_rows)] == merged_rows  # Appended after the merged rows only.
    (carry,) = [row for row in after if str(row.get("action_id", "")).startswith("ACT-CARRY-")]
    assert (carry["approved_head"], carry["merged_head"]) == (consensus_data["target_commit"], merged)
    assert len(after) == len(merged_rows) + 1
    consensus = ctx.stage_results["review"].payload["consensus"]
    assert consensus["target_commit"] == consensus_data["target_commit"] and consensus["carried_to"] == merged
    assert before != after


FINGERPRINT = "pf2:" + "c" * 40
MECHANICAL_RESOLUTIONS = {
    "clean": (),
    "ledger": ((LEDGER, "ledger", "mechanical"),),
    "kb_timestamp_only": (("holon-knowledge/kb/entry.json", "kb_timestamp_only", "mechanical"),),
    "appended_list": (("src/registry.py", "appended_list", "mechanical"),),
    "delete_vs_touch": (("src/old.py", "delete_vs_touch", "mechanical"),),
}


def _gate_post(
    tmp_path: Path, monkeypatch, result: SyncResult, merged_fingerprint: str, ctx: FlowContext | None = None
):
    """Run ``_approved_post_gate`` on a CONFLICTING head whose merge returns ``result``; the fingerprint is stubbed."""
    monkeypatch.setattr(sync_points, "sync_review_head", _RecordingSync(result))
    monkeypatch.setattr(
        "sandbox_executor.flow.review.approval_carry_over.approval_fingerprint_of", lambda *a: merged_fingerprint
    )
    consensus = {
        "approved": True,
        "base_branch": "main",
        "target_commit": "a" * 40,
        "approval_fingerprint": FINGERPRINT,
    }
    request = PostRequest(str(tmp_path), BRANCH, consensus, None, [], "t", iteration=1)
    ctx = ctx or FlowContext(target_worktree=str(tmp_path), execution_branch=BRANCH, token_reduce=False)
    exec_file = tmp_path / "gate.jsonl"
    gated = conflict_gate._approved_post_gate(ctx, str(exec_file), request, lambda **_: dict(CONFLICTING))
    rows = _gate_rows(exec_file) if exec_file.exists() else []
    return gated, [row["action_id"].split("-")[1] for row in rows], consensus, ctx


@pytest.mark.parametrize("resolutions", list(MECHANICAL_RESOLUTIONS.values()), ids=list(MECHANICAL_RESOLUTIONS))
def test_a_mechanical_merge_with_an_unchanged_fingerprint_carries_the_approval(tmp_path, monkeypatch, resolutions):
    merge = SyncResult("merged", "a" * 40, "b" * 40, "c" * 40, resolutions=resolutions)
    gated, rows, consensus, ctx = _gate_post(tmp_path, monkeypatch, merge, FINGERPRINT)
    assert gated is None and rows == ["CARRY"]
    assert (consensus["carried_from"], consensus["carried_to"]) == ("a" * 40, "b" * 40)
    assert ctx.review_scope_paths == []


@pytest.mark.parametrize("resolutions", list(MECHANICAL_RESOLUTIONS.values()), ids=list(MECHANICAL_RESOLUTIONS))
def test_a_mechanical_merge_with_a_changed_fingerprint_blocks_and_never_carries(tmp_path, monkeypatch, resolutions):
    # The carry is gated on the fingerprint, never on the conflict class alone: a delete-vs-touch that removed a file
    # the PR changed alters the PR-own change, so it blocks here too.
    merge = SyncResult("merged", "a" * 40, "b" * 40, "c" * 40, resolutions=resolutions)
    gated, rows, consensus, _ = _gate_post(tmp_path, monkeypatch, merge, "pf2:" + "d" * 40)
    assert gated is not None and gated[0] == f"conflict gate: {conflict_gate.MERGED_REASON}"
    assert rows == ["GATE"] and "carried_to" not in consensus


def test_a_code_resolution_never_carries_and_unions_the_resolved_paths_into_the_scope(tmp_path, monkeypatch):
    merge = SyncResult(
        "merged",
        "a" * 40,
        "b" * 40,
        "c" * 40,
        resolutions=(("src/app.py", "code", "tier-1"), (LEDGER, "ledger", "mechanical")),
        code_paths=("src/app.py",),
        resolver_tier="tier-1",
        resolver_test_edits=("tests/test_app.py",),
    )
    ctx = FlowContext(target_worktree=str(tmp_path), execution_branch=BRANCH, token_reduce=False)
    ctx.review_scope_paths = ["src/earlier.py"]
    gated, rows, consensus, ctx = _gate_post(tmp_path, monkeypatch, merge, FINGERPRINT, ctx)
    assert gated is not None and gated[1]["conflict_gate"]["mechanical_only"] is False
    assert rows == ["GATE"] and "carried_to" not in consensus
    assert ctx.review_scope_paths == ["src/app.py", "src/earlier.py", "tests/test_app.py"]
