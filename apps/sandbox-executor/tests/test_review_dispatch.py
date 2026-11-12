"""Unit and integration tests for Stage 4 reviewer container dispatch and result transport."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
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
        patch("sandbox_executor.flow.stages.review._checkout_execution_branch", return_value=None),
        patch("sandbox_executor.flow.stages.review.check_and_resolve_stage_conflicts"),
        patch(
            "sandbox_executor.flow.review.loop.create_or_update_pr",
            return_value={"success": True, "action": "created", "pr": {"number": 1, "url": "https://github.com/pr/1"}},
        ),
        patch(
            "sandbox_executor.flow.review.ensemble.resolve_review_target",
            return_value=ReviewTarget(raw=EXEC_TARGET, head_branch=EXEC_TARGET, head_sha="abc1234"),
        ),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
        patch(
            "sandbox_executor.flow.review.loop.check_pr_mergeability",
            return_value={"is_dirty": True, "merge_state_status": "DIRTY", "mergeable": "CONFLICTING"},
        ),
        patch(
            "sandbox_executor.flow.review.loop.probe_ci_checks",
            return_value={
                "all_passed": True,
                "passed": 1,
                "failed": 0,
                "pending": 0,
                "total": 1,
                "source": "github_cli",
            },
        ),
        patch("sandbox_executor.flow.review.loop.post_pr_review_consensus") as mock_post,
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
        if "--resolve" in _args:
            # A resolver pass that changed nothing still writes a resolver result; one that leaves none halts the loop.
            payload = {"resolver": True, "dispositions": [], "head_sha": "abc1234", "error": None}
        Path(result_file).write_text(json.dumps(payload), encoding="utf-8")
        return 0

    ctx = _make_context(tmp_path, dispatcher=fake_dispatcher)

    with (
        patch("sandbox_executor.flow.stages.review._checkout_execution_branch", return_value=None),
        patch("sandbox_executor.flow.stages.review.check_and_resolve_stage_conflicts"),
        patch(
            "sandbox_executor.flow.review.loop.create_or_update_pr",
            return_value={"success": True, "action": "created", "pr": {"number": 1, "url": "https://github.com/pr/1"}},
        ),
        patch(
            "sandbox_executor.flow.review.ensemble.resolve_review_target",
            return_value=ReviewTarget(raw=EXEC_TARGET, head_branch=EXEC_TARGET, head_sha="abc1234"),
        ),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
        patch(
            "sandbox_executor.flow.review.loop.check_pr_mergeability",
            return_value={"is_dirty": False, "merge_state_status": "CLEAN", "mergeable": "MERGEABLE"},
        ),
        patch(
            "sandbox_executor.flow.review.loop.probe_ci_checks",
            return_value={
                "all_passed": False,
                "passed": 0,
                "failed": 2,
                "pending": 0,
                "total": 2,
                "source": "github_cli",
                "checks": [
                    {"name": "lint", "bucket": "fail", "state": "FAILURE"},
                    {"name": "unit", "bucket": "fail", "state": "FAILURE"},
                ],
            },
        ),
        patch("sandbox_executor.flow.review.loop.post_pr_review_consensus") as mock_post,
    ):
        result = run_review_stage(ctx)

    assert result.status is StageStatus.FAILED
    assert "CI failed: lint, unit." in (result.error or "")
    assert "test suite reported failures" not in (result.error or "")
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
        patch("sandbox_executor.flow.stages.review._checkout_execution_branch", return_value=None),
        patch("sandbox_executor.flow.stages.review.check_and_resolve_stage_conflicts"),
        patch(
            "sandbox_executor.flow.review.loop.create_or_update_pr",
            return_value={"success": True, "action": "created", "pr": {"number": 1, "url": "https://github.com/pr/1"}},
        ),
        patch(
            "sandbox_executor.flow.review.ensemble.resolve_review_target",
            return_value=ReviewTarget(raw=EXEC_TARGET, head_branch=EXEC_TARGET, head_sha="abc1234"),
        ),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
        patch(
            "sandbox_executor.flow.review.loop.check_pr_mergeability",
            return_value={"is_dirty": False, "merge_state_status": "CLEAN", "mergeable": "MERGEABLE"},
        ),
        patch(
            "sandbox_executor.flow.review.loop.probe_ci_checks",
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
            "sandbox_executor.flow.review.loop.post_pr_review_consensus",
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


# --------------------------------------------------------------------------------------
# Stage 4 dispatches review the host worktree in place: no volume, no session lifecycle
# --------------------------------------------------------------------------------------


def _host_loop_patches():
    from unittest.mock import MagicMock

    from sandbox_executor.review_target import DiffVerdict

    clean_merge = {"is_dirty": False, "merge_state_status": "CLEAN", "mergeable": "MERGEABLE", "source": "github_cli"}
    green_ci = {
        "all_passed": True,
        "passed": 1,
        "failed": 0,
        "pending": 0,
        "total": 1,
        "source": "github_cli",
        "checks": [],
    }
    return (
        patch("sandbox_executor.flow.review.loop.shutil.which", return_value=None),
        patch("sandbox_executor.flow.review.loop.check_pr_mergeability", side_effect=lambda *a, **k: dict(clean_merge)),
        patch("sandbox_executor.flow.review.loop.probe_ci_checks", side_effect=lambda *a, **k: dict(green_ci)),
        patch(
            "sandbox_executor.flow.review.loop.post_pr_review_consensus",
            MagicMock(return_value={"dual_posted": True, "official_review": {"success": True}}),
        ),
        patch(
            "sandbox_executor.flow.review.ensemble.resolve_review_target",
            return_value=ReviewTarget(raw=EXEC_TARGET, head_branch=EXEC_TARGET, head_sha="a" * 40),
        ),
        patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
    )


def _objecting_then_approving_dispatcher(calls: list[dict]):
    """A fake dispatcher: iteration 1 reviewers object, the resolver applies and commits, iteration 2 approves."""
    from sandbox_executor.review_ensemble import ReviewFinding

    def fake_dispatcher(role, runner, model, positional_args, **kwargs):
        calls.append({"role": role, "args": list(positional_args), **kwargs})
        result_file = kwargs["result_file"]
        if "--resolve" in positional_args:
            with open(result_file, encoding="utf-8") as f:
                payload = json.load(f)
            outcome = {
                "resolver": True,
                "dispositions": [
                    {**item, "disposition": "applied", "rejection_reason": None} for item in payload.get("findings", [])
                ],
                "head_sha": "b" * 40,
                "lint_passed": True,
                "tests_passed": True,
                "error": None,
            }
            Path(result_file).write_text(json.dumps(outcome), encoding="utf-8")
            return 0
        iteration_args = [a for a in positional_args if a.startswith("--iteration=")]
        objecting = iteration_args == ["--iteration=1"]
        if objecting:
            finding = ReviewFinding("important", "src/app.py", 12, "important problem in the handler")
            parsed = ParsedTrailer(ReviewerVerdict.CHANGES_REQUESTED, ReviewCounts(important=1), (finding,))
            verdict = ConsensusVerdict.CHANGES_REQUESTED
        else:
            parsed = ParsedTrailer(ReviewerVerdict.APPROVED, ReviewCounts(), ())
            verdict = ConsensusVerdict.APPROVED
        reviewers = [ReviewerResult.completed(i, "test-agent", "test-model", f"r{i}.md", "", parsed) for i in (1, 2, 3)]
        payload = serialize_review_outcome(reviewers, verdict)
        Path(result_file).write_text(json.dumps(payload), encoding="utf-8")
        return 0 if verdict is ConsensusVerdict.APPROVED else 1

    return fake_dispatcher


def test_every_stage4_dispatch_reviews_the_flow_worktree_in_place(tmp_path: Path, monkeypatch) -> None:
    """A 2-iteration convergence cycle dispatches every reviewer and resolver pass on the flow worktree.

    No dispatch carries a workspace volume, and the loop runs no docker command of its own (no volume create, no
    root chown init container, no volume teardown).
    """
    from sandbox_executor.flow import run_review_loop

    monkeypatch.delenv("HOLON_IN_SANDBOX", raising=False)
    monkeypatch.delenv("HOLON_ROLE", raising=False)

    calls: list[dict] = []
    ctx = _make_context(tmp_path, dispatcher=_objecting_then_approving_dispatcher(calls))

    patches = _host_loop_patches()
    for p in patches:
        p.start()
    try:
        with (
            patch("subprocess.run", side_effect=AssertionError("the loop ran a subprocess")) as no_subprocess,
            patch("sandbox_executor.flow.review.resolver.push_commit") as host_push,
        ):
            res = run_review_loop(ctx, target=EXEC_TARGET, max_iterations=5)
    finally:
        for p in reversed(patches):
            p.stop()

    assert res["consensus"]["approved"] is True
    assert res["consensus"]["iterations"] == 2
    assert [c["role"] for c in calls] == ["review-loop", "review-loop", "review-loop"]
    assert ["--resolve" in c["args"] for c in calls] == [False, True, False]
    assert [c["target_worktree"] for c in calls] == [ctx.target_worktree] * 3
    assert all("review_workspace_volume" not in c for c in calls)
    no_subprocess.assert_not_called()
    # The host, not the container, pushes the committed head after the resolver pass.
    host_push.assert_called_once_with(ctx.target_worktree, "b" * 40, EXEC_TARGET)

    # Canonical Stage 4 provenance stays host-written: per-iteration ACT-REV rows and one summary row.
    ledger = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"
    records = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    iteration_rows = [
        r for r in records if str(r.get("action_id", "")).startswith("ACT-REV-") and "review_reviewer_index" not in r
    ]
    assert [r["review_iteration"] for r in iteration_rows] == [1, 2]
    assert [r for r in records if str(r.get("action_id", "")).startswith("ACT-RES-")]
    assert [r for r in records if r.get("entry_type") == "summary"]


def test_sandbox_workspace_mount_matches_the_container_workspace_default(monkeypatch) -> None:
    """The dispatcher's mount path constant equals get_workspace_dir() inside a role container."""
    from sandbox_executor.agent_runner import SANDBOX_WORKSPACE_MOUNT, get_workspace_dir

    monkeypatch.delenv("HOLON_TARGET_WORKTREE", raising=False)
    monkeypatch.setenv("HOLON_ROLE", "review-loop")
    monkeypatch.setenv("HOME", "/home/holon")
    assert get_workspace_dir() == SANDBOX_WORKSPACE_MOUNT


# --------------------------------------------------------------------------------------
# host pre-dispatch fetch of the plan, execution and base branches
# --------------------------------------------------------------------------------------


def _fetch_args(target: str):
    import argparse

    return argparse.Namespace(target=target, target_worktree=".", agent="claude-agent", model="m")


def test_container_review_fetches_the_three_branches_once_before_dispatch(monkeypatch):
    from sandbox_executor import git_flow
    from sandbox_executor.cli.commands import review, review_fetch

    exec_branch = "I-1-x/P-1-a/E-2-a/_"
    calls: list[str] = []
    monkeypatch.setattr(review_fetch, "_has_origin_remote", lambda _w: True)
    monkeypatch.setattr(review_fetch, "target_branch", lambda _w, _p: "main")
    monkeypatch.setattr(git_flow, "fetch_origin_branch", lambda _w, branch: calls.append(branch))
    monkeypatch.setattr(git_flow, "resolve_review_worktree", lambda *_a: "/wt")

    assert review._container_review_worktree(_fetch_args(exec_branch)) == "/wt"
    assert calls == ["I-1-x/P-1-a/_", exec_branch, "main"]


def test_container_review_fetch_failure_stops_the_stage_before_dispatch(monkeypatch, capsys):
    from sandbox_executor import cli, git_flow
    from sandbox_executor.cli.commands import review, review_fetch

    def failing_fetch(_w, branch):
        raise RuntimeError(f"could not fetch branch '{branch}' from origin")

    monkeypatch.setattr(review_fetch, "_has_origin_remote", lambda _w: True)
    monkeypatch.setattr(review_fetch, "target_branch", lambda _w, _p: "main")
    monkeypatch.setattr(git_flow, "fetch_origin_branch", failing_fetch)
    monkeypatch.setattr(
        cli, "resolve_runner_and_model", lambda a, m: (type("R", (), {"value": a})(), type("R", (), {"value": m})())
    )
    monkeypatch.setattr(cli, "detect_holon_coherence", lambda v: v)
    dispatched: list[object] = []
    monkeypatch.setattr(cli, "run_docker_container", lambda *a, **k: dispatched.append(a) or 0)
    monkeypatch.delenv("HOLON_ROLE", raising=False)
    monkeypatch.delenv("HOLON_IN_SANDBOX", raising=False)
    args = _fetch_args("I-1-x/P-1-a/E-2-a/_")
    args.__dict__.update(
        max_iterations=1, ensemble_size=1, local=False, dry_run=False, json=False, command="review-loop"
    )
    monkeypatch.setattr(review, "default_branch_worktree_allowed", lambda *_a: True)

    with pytest.raises(SystemExit) as exc:
        review.run(args)

    assert exc.value.code == 1
    assert not dispatched
    assert "could not fetch branch" in capsys.readouterr().err


def _recorded_fetches(monkeypatch, *, origin: bool = True) -> list[str]:
    from sandbox_executor import git_flow
    from sandbox_executor.cli.commands import review_fetch

    calls: list[str] = []
    monkeypatch.setattr(review_fetch, "_has_origin_remote", lambda _w: origin)
    monkeypatch.setattr(review_fetch, "target_branch", lambda _w, _p: "release")
    monkeypatch.setattr(git_flow, "fetch_origin_branch", lambda _w, branch: calls.append(branch))
    return calls


def test_fetch_review_branches_pr_base_replaces_the_intent_target(monkeypatch):
    from sandbox_executor.cli.commands.review_fetch import fetch_review_branches

    calls = _recorded_fetches(monkeypatch)

    fetch_review_branches(".", "I-1-x/P-1-a/E-2-a/_", "develop")

    assert calls == ["I-1-x/P-1-a/_", "I-1-x/P-1-a/E-2-a/_", "develop"]


def test_fetch_review_branches_uses_the_intent_target_without_a_pr_base(monkeypatch):
    from sandbox_executor.cli.commands.review_fetch import fetch_review_branches

    calls = _recorded_fetches(monkeypatch)

    fetch_review_branches(".", "I-1-x/P-1-a/E-2-a/_", None)

    assert calls[-1] == "release"


def test_fetch_review_branches_non_execution_head_fetches_head_and_main_only(monkeypatch):
    from sandbox_executor.cli.commands.review_fetch import fetch_review_branches

    calls = _recorded_fetches(monkeypatch)

    fetch_review_branches(".", "fix/E-12-thing", None)

    assert calls == ["fix/E-12-thing", "main"]


def test_fetch_review_branches_execution_shape_requires_the_underscore_final_segment(monkeypatch):
    from sandbox_executor.cli.commands.review_fetch import fetch_review_branches

    calls = _recorded_fetches(monkeypatch)

    fetch_review_branches(".", "I-1-x/P-1-a/E-2-a/calibrated", None)

    assert calls == ["I-1-x/P-1-a/E-2-a/calibrated", "main"]


def test_fetch_review_branches_skips_everything_without_an_origin_remote(monkeypatch):
    from sandbox_executor.cli.commands.review_fetch import fetch_review_branches

    calls = _recorded_fetches(monkeypatch, origin=False)

    fetch_review_branches(".", "I-1-x/P-1-a/E-2-a/_", "main")

    assert calls == []


def test_fetch_review_branches_fetches_a_shared_branch_once(monkeypatch):
    from sandbox_executor.cli.commands.review_fetch import fetch_review_branches

    calls = _recorded_fetches(monkeypatch)

    fetch_review_branches(".", "main", "main")

    assert calls == ["main"]


def test_container_review_pr_target_fetches_its_head_and_base(monkeypatch):
    from sandbox_executor import git_flow
    from sandbox_executor.cli.commands import review
    from sandbox_executor.review_target import ReviewTarget

    calls = _recorded_fetches(monkeypatch)
    resolved = ReviewTarget(
        raw="7", head_branch="I-1-x/P-1-a/E-2-a/_", head_sha="0" * 40, pr_number=7, base_branch="develop"
    )
    monkeypatch.setattr(review, "resolve_review_target", lambda *_a: resolved)
    monkeypatch.setattr(git_flow, "resolve_review_worktree", lambda *_a: "/wt")

    assert review._container_review_worktree(_fetch_args("7")) == "/wt"
    assert calls == ["I-1-x/P-1-a/_", "I-1-x/P-1-a/E-2-a/_", "develop"]
