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
            payload = {"resolver": True, "dispositions": [], "head_sha": "abc1234", "pushed": False, "error": None}
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
            },
        ),
        patch("sandbox_executor.flow.review.loop.post_pr_review_consensus") as mock_post,
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
# Persistent Stage 4 review workspace: session lifecycle across engine-owned iterations
# --------------------------------------------------------------------------------------


class _FakeDockerCli:
    """Records the docker CLI commands a ReviewWorkspaceSession runs and answers them all with success."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]):
        import subprocess

        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    def commands(self, *verbs: str) -> list[list[str]]:
        return [c for c in self.calls if c[: len(verbs)] == list(verbs)]


def _session(docker: _FakeDockerCli):
    from sandbox_executor.flow import ReviewWorkspaceSession

    return ReviewWorkspaceSession(EXEC_TARGET, agent="claude-agent", docker_runner=docker)


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
    """A fake dispatcher: iteration 1 reviewers object, the resolver applies and pushes, iteration 2 approves."""
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
                "pushed": True,
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


def test_one_session_volume_is_shared_by_every_dispatch_and_removed_once(tmp_path: Path, monkeypatch) -> None:
    """A 2-iteration convergence cycle mounts one volume in all three dispatches and tears it down once."""
    from sandbox_executor.flow import review_session_volume_name, run_review_loop

    monkeypatch.delenv("HOLON_IN_SANDBOX", raising=False)
    monkeypatch.delenv("HOLON_ROLE", raising=False)

    calls: list[dict] = []
    ctx = _make_context(tmp_path, dispatcher=_objecting_then_approving_dispatcher(calls))
    docker = _FakeDockerCli()

    patches = _host_loop_patches()
    for p in patches:
        p.start()
    try:
        res = run_review_loop(ctx, target=EXEC_TARGET, max_iterations=5, workspace_session=_session(docker))
    finally:
        for p in reversed(patches):
            p.stop()

    assert res["consensus"]["approved"] is True
    assert res["consensus"]["iterations"] == 2

    expected_volume = review_session_volume_name(EXEC_TARGET)
    assert [c["role"] for c in calls] == ["review-loop", "review-loop", "review-loop"]
    assert ["--resolve" in c["args"] for c in calls] == [False, True, False]
    assert [c.get("review_workspace_volume") for c in calls] == [expected_volume] * 3

    # The volume is created once (plus its one root ownership init) and removed exactly once.
    assert len(docker.commands("docker", "volume", "create")) == 1
    create = docker.commands("docker", "volume", "create")[0]
    assert "--label" in create and "holon.review-session=1" in create
    assert create[-1] == expected_volume
    init_runs = docker.commands("docker", "run")
    assert len(init_runs) == 1
    assert "--user" in init_runs[0] and "root" in init_runs[0]
    assert ["docker", "volume", "rm", "-f", expected_volume] in docker.calls
    assert len(docker.commands("docker", "volume", "rm")) == 1

    # Canonical Stage 4 provenance stays host-written: per-iteration ACT-REV rows and one summary row.
    ledger = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"
    records = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    iteration_rows = [
        r for r in records if str(r.get("action_id", "")).startswith("ACT-REV-") and "review_reviewer_index" not in r
    ]
    assert [r["review_iteration"] for r in iteration_rows] == [1, 2]
    assert [r for r in records if str(r.get("action_id", "")).startswith("ACT-RES-")]
    assert [r for r in records if r.get("entry_type") == "summary"]


def test_session_volume_is_removed_when_the_loop_raises_mid_iteration(tmp_path: Path, monkeypatch) -> None:
    """An exception after the first dispatch still tears the created volume down exactly once."""
    import pytest
    from sandbox_executor.flow import run_review_loop

    monkeypatch.delenv("HOLON_IN_SANDBOX", raising=False)
    monkeypatch.delenv("HOLON_ROLE", raising=False)

    calls: list[dict] = []
    ctx = _make_context(tmp_path, dispatcher=_objecting_then_approving_dispatcher(calls))
    docker = _FakeDockerCli()

    patches = _host_loop_patches()
    for p in patches:
        p.start()
    try:
        with (
            patch("sandbox_executor.flow.review.loop.probe_ci_checks", side_effect=RuntimeError("gh exploded")),
            pytest.raises(RuntimeError, match="gh exploded"),
        ):
            run_review_loop(ctx, target=EXEC_TARGET, max_iterations=5, workspace_session=_session(docker))
    finally:
        for p in reversed(patches):
            p.stop()

    assert len(calls) == 1  # The first ensemble dispatch ran, then the loop raised.
    assert len(docker.commands("docker", "volume", "create")) == 1
    assert len(docker.commands("docker", "volume", "rm")) == 1


def test_kill_switch_disables_the_session_and_passes_no_volume(tmp_path: Path, monkeypatch) -> None:
    """HOLON_REVIEW_PERSIST=0 restores clone-per-pass dispatch arguments: no volume kwarg, no docker call."""
    from sandbox_executor.flow import run_review_loop

    monkeypatch.delenv("HOLON_IN_SANDBOX", raising=False)
    monkeypatch.delenv("HOLON_ROLE", raising=False)
    monkeypatch.setenv("HOLON_REVIEW_PERSIST", "0")

    calls: list[dict] = []
    ctx = _make_context(tmp_path, dispatcher=_objecting_then_approving_dispatcher(calls))
    docker = _FakeDockerCli()

    patches = _host_loop_patches()
    for p in patches:
        p.start()
    try:
        res = run_review_loop(ctx, target=EXEC_TARGET, max_iterations=5, workspace_session=_session(docker))
    finally:
        for p in reversed(patches):
            p.stop()

    assert res["consensus"]["approved"] is True
    assert calls, "the loop dispatched nothing"
    assert all("review_workspace_volume" not in c for c in calls)
    assert docker.calls == []


def test_in_container_mode_never_creates_a_session(tmp_path: Path, monkeypatch) -> None:
    """With reviewers sandboxed, the loop runs the in-container ensemble and touches no docker volume."""
    from sandbox_executor.flow import run_review_loop

    from tests.review_fakes import install_fake_reviewers

    install_fake_reviewers(monkeypatch)
    ctx = _make_context(tmp_path)
    docker = _FakeDockerCli()

    patches = _host_loop_patches()
    for p in patches:
        p.start()
    try:
        res = run_review_loop(ctx, target=EXEC_TARGET, max_iterations=2, workspace_session=_session(docker))
    finally:
        for p in reversed(patches):
            p.stop()

    assert res["consensus"]["approved"] is True
    assert docker.calls == []


def test_session_creation_failure_falls_back_to_clone_per_pass(tmp_path: Path, monkeypatch) -> None:
    """A failing `docker volume create` disables persistence; dispatch continues without a volume."""
    import subprocess as _subprocess

    from sandbox_executor.flow import ReviewWorkspaceSession, run_review_loop

    monkeypatch.delenv("HOLON_IN_SANDBOX", raising=False)
    monkeypatch.delenv("HOLON_ROLE", raising=False)

    def failing_docker(argv: list[str]):
        return _subprocess.CompletedProcess(argv, 1, stdout="", stderr="no docker daemon")

    session = ReviewWorkspaceSession(EXEC_TARGET, agent="claude-agent", docker_runner=failing_docker)
    calls: list[dict] = []
    ctx = _make_context(tmp_path, dispatcher=_objecting_then_approving_dispatcher(calls))

    patches = _host_loop_patches()
    for p in patches:
        p.start()
    try:
        res = run_review_loop(ctx, target=EXEC_TARGET, max_iterations=5, workspace_session=session)
    finally:
        for p in reversed(patches):
            p.stop()

    assert res["consensus"]["approved"] is True
    assert calls and all("review_workspace_volume" not in c for c in calls)


def test_review_persist_toggle_and_deterministic_volume_names() -> None:
    """The kill switch recognises the documented falsy values; volume names are deterministic per target."""
    from sandbox_executor.flow import review_persist_enabled, review_session_volume_name

    assert review_persist_enabled({}) is True
    assert review_persist_enabled({"HOLON_REVIEW_PERSIST": "1"}) is True
    for value in ("0", "false", "No", "OFF"):
        assert review_persist_enabled({"HOLON_REVIEW_PERSIST": value}) is False

    name = review_session_volume_name(EXEC_TARGET)
    assert name == review_session_volume_name(EXEC_TARGET)
    assert name.startswith("holon-review-ws-")
    assert name != review_session_volume_name("another/target")


def test_sandbox_workspace_mount_matches_the_container_workspace_default(monkeypatch) -> None:
    """The dispatcher's mount path constant equals get_workspace_dir() inside a role container."""
    from sandbox_executor.agent_runner import SANDBOX_WORKSPACE_MOUNT, get_workspace_dir

    monkeypatch.delenv("HOLON_TARGET_WORKTREE", raising=False)
    monkeypatch.setenv("HOLON_ROLE", "review-loop")
    monkeypatch.setenv("HOME", "/home/holon")
    assert get_workspace_dir() == SANDBOX_WORKSPACE_MOUNT
