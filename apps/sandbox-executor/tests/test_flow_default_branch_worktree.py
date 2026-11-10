"""A default-branch worktree selects the repository and base branch only: flows run in flow-<slug>, never in it."""

import io
import json
import logging
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.cli import main as cli_main
from sandbox_executor.flow import (
    FlowContext,
    FlowStage,
    PipelineEngine,
    StageResult,
    StageStatus,
    get_default_checkpoint_path,
    save_checkpoint,
)
from sandbox_executor.git_flow import (
    checked_out_branch,
    default_branch_worktree_problem,
    ensure_flow_worktree_off_default,
    is_default_branch_worktree,
    normalise_base_branch,
    repository_default_branch,
)

_SLUG = "redirect-me"
_FLOW_RUNNER_ARGS = ["--agent", "antigravity-agent", "--model", "gemini-3.8-flash-medium", "--dry-run", "--json"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True).stdout.strip()


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(f"{name}\n", encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", f"Add {name}")
    return _git(repo, "rev-parse", "HEAD")


def _snapshot(repo: Path) -> tuple[str, str, str]:
    """HEAD, checked-out branch and porcelain status of a worktree."""
    return (
        _git(repo, "rev-parse", "HEAD"),
        _git(repo, "symbolic-ref", "--short", "HEAD"),
        _git(repo, "status", "--porcelain"),
    )


@pytest.fixture
def main_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """A bare origin, a main worktree on 'main' and an origin/main one commit ahead of local main.

    The cwd is a non-git directory, so nothing can fall back to the developer's real repository.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    _commit(seed, "README.md")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
    checkout = tmp_path / "checkout"
    _git(tmp_path, "clone", "-q", str(origin), str(checkout))
    pusher = tmp_path / "pusher"
    _git(tmp_path, "clone", "-q", str(origin), str(pusher))
    _commit(pusher, "ahead.txt")
    _git(pusher, "push", "-q", "origin", "main")
    _git(checkout, "fetch", "-q", "origin")
    intent = tmp_path / "intent.json"
    intent.write_text(json.dumps({"slug": _SLUG, "target_branch": "main"}), encoding="utf-8")
    return {"root": tmp_path, "checkout": checkout, "intent": intent, "flow": tmp_path / f"flow-{_SLUG}"}


def _run(argv: list[str]) -> tuple[MagicMock, MagicMock, str]:
    """Run ``holon flow`` with the pipeline and ``sys.exit`` patched; return (pipeline mock, exit mock, stderr)."""
    with (
        patch("sandbox_executor.cli.run_flow_pipeline", return_value={"status": "ok"}) as mock_pipeline,
        patch("sys.exit") as mock_exit,
        patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
        patch("sys.argv", ["holon", "flow", *argv]),
    ):
        cli_main()
    return mock_pipeline, mock_exit, mock_stderr.getvalue()


def _run_real_pipeline(argv: list[str]) -> tuple[MagicMock, str]:
    """Run ``holon flow`` through the real pipeline with ``sys.exit`` patched; return (exit mock, stderr)."""
    with (
        patch("sys.exit") as mock_exit,
        patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
        patch("sys.argv", ["holon", "flow", *argv]),
    ):
        cli_main()
    return mock_exit, mock_stderr.getvalue()


def _flow_siblings(root: Path) -> list[str]:
    return sorted(p.name for p in root.iterdir() if p.name.startswith("flow-"))


# ---------------------------------------------------------------------------
# git_flow primitives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [("origin/main", "main"), ("main", "main"), ("refs/heads/develop", "develop"), ("", "main"), (None, "main")],
)
def test_normalise_base_branch(given: str | None, expected: str) -> None:
    assert normalise_base_branch(given) == expected


def test_detached_head_is_not_a_default_branch_worktree(main_repo: dict[str, Path]) -> None:
    checkout = main_repo["checkout"]
    _git(checkout, "checkout", "-q", "--detach")

    assert checked_out_branch(str(checkout)) is None
    assert is_default_branch_worktree(str(checkout), "origin/main") is False
    assert default_branch_worktree_problem(str(checkout), "main") is None


def test_repository_default_branch_reads_origin_head(main_repo: dict[str, Path], tmp_path: Path) -> None:
    checkout = main_repo["checkout"]
    assert repository_default_branch(str(checkout)) == "main"

    _git(checkout, "remote", "set-head", "origin", "--delete")
    assert repository_default_branch(str(checkout)) == "main"
    assert repository_default_branch(str(tmp_path / "missing")) == "main"


def test_off_default_refuses_a_derived_path_that_is_not_a_worktree(main_repo: dict[str, Path]) -> None:
    main_repo["flow"].mkdir()

    with pytest.raises(RuntimeError, match="exists but is not a git working tree"):
        ensure_flow_worktree_off_default(str(main_repo["checkout"]), _SLUG, "main")


def test_off_default_refuses_a_flow_worktree_on_the_base_branch(main_repo: dict[str, Path]) -> None:
    checkout = main_repo["checkout"]
    _git(checkout, "checkout", "-q", "-b", "side")
    _git(checkout, "worktree", "add", "-q", str(main_repo["flow"]), "main")

    with pytest.raises(RuntimeError, match="has the base branch 'main' checked out"):
        ensure_flow_worktree_off_default(str(checkout), _SLUG, "main")


def test_off_default_derives_the_sibling_from_the_top_level(main_repo: dict[str, Path]) -> None:
    subdir = main_repo["checkout"] / "sub"
    subdir.mkdir()

    path = ensure_flow_worktree_off_default(str(subdir), _SLUG, "origin/main")

    assert path == str(main_repo["flow"])


# ---------------------------------------------------------------------------
# (a) + (b): redirect to flow-<slug> off origin/main; the main worktree is untouched
# ---------------------------------------------------------------------------


def _assert_flow_worktree_off_origin_main(main_repo: dict[str, Path]) -> None:
    flow, checkout = main_repo["flow"], main_repo["checkout"]
    assert _git(flow, "symbolic-ref", "--short", "HEAD") == f"flow-{_SLUG}"
    assert _git(flow, "rev-parse", "HEAD") == _git(checkout, "rev-parse", "origin/main")
    assert _git(flow, "rev-parse", "HEAD") != _git(checkout, "rev-parse", "main")


@pytest.mark.parametrize("implicit", [False, True], ids=["explicit-target", "default-dot"])
def test_flow_from_the_main_worktree_runs_in_flow_slug(
    main_repo: dict[str, Path], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, implicit: bool
) -> None:
    checkout, flow = main_repo["checkout"], main_repo["flow"]
    before = _snapshot(checkout)
    argv = [str(main_repo["intent"]), *_FLOW_RUNNER_ARGS]
    if implicit:
        monkeypatch.chdir(checkout)
    else:
        argv += ["--target-worktree", str(checkout)]

    with caplog.at_level(logging.INFO):
        pipeline, exit_mock, _ = _run(argv)

    pipeline.assert_called_once()
    context = pipeline.call_args[1]["context"]
    assert context.target_worktree == str(flow)
    _assert_flow_worktree_off_origin_main(main_repo)
    assert Path(get_default_checkpoint_path(context.target_worktree, _SLUG)).is_relative_to(flow)
    assert f"Running the flow in '{flow}'" in caplog.text
    assert f"Target worktree '{checkout}' has the base branch 'main' checked out" in caplog.text
    exit_mock.assert_called_with(0)
    assert _snapshot(checkout) == before
    assert f"worktree {checkout}\nHEAD {before[0]}\nbranch refs/heads/main" in _git(
        checkout, "worktree", "list", "--porcelain"
    )


def test_every_stage_and_the_checkpoint_use_the_flow_worktree(main_repo: dict[str, Path]) -> None:
    checkout, flow = main_repo["checkout"], main_repo["flow"]
    before = _snapshot(checkout)
    seen: list[tuple[str, str]] = []

    def record(engine: PipelineEngine, stage: FlowStage) -> StageResult:
        seen.append((stage.value, engine.context.target_worktree))
        return StageResult(stage=stage, status=StageStatus.SUCCESS)

    with patch.object(PipelineEngine, "execute_stage", autospec=True, side_effect=record):
        exit_mock, stderr = _run_real_pipeline(
            [str(main_repo["intent"]), *_FLOW_RUNNER_ARGS, "--target-worktree", str(checkout)]
        )

    assert [stage for stage, _ in seen] == ["intent", "plan", "execute", "review", "calibrate"], stderr
    assert {target for _, target in seen} == {str(flow)}
    assert Path(get_default_checkpoint_path(str(flow), _SLUG)).exists()
    assert not (checkout / ".holon").exists()
    exit_mock.assert_called_with(0)
    assert _snapshot(checkout) == before


# ---------------------------------------------------------------------------
# (c) reuse and (d) non-default branch
# ---------------------------------------------------------------------------


def test_an_existing_flow_worktree_is_reused(main_repo: dict[str, Path]) -> None:
    checkout, flow = main_repo["checkout"], main_repo["flow"]
    _git(checkout, "worktree", "add", "-q", "-b", f"flow-{_SLUG}", str(flow), "origin/main")
    marker = _commit(flow, "marker.txt")

    pipeline, _, _ = _run([str(main_repo["intent"]), *_FLOW_RUNNER_ARGS, "--target-worktree", str(checkout)])

    assert pipeline.call_args[1]["context"].target_worktree == str(flow)
    assert _git(flow, "rev-parse", "HEAD") == marker
    assert _flow_siblings(main_repo["root"]) == [f"flow-{_SLUG}"]


def test_a_target_on_another_branch_is_used_as_it_is(main_repo: dict[str, Path]) -> None:
    feature = main_repo["root"] / "feature"
    _git(main_repo["checkout"], "worktree", "add", "-q", "-b", "feature-x", str(feature), "origin/main")

    pipeline, exit_mock, _ = _run([str(main_repo["intent"]), *_FLOW_RUNNER_ARGS, "--target-worktree", str(feature)])

    assert pipeline.call_args[1]["context"].target_worktree == str(feature)
    assert _flow_siblings(main_repo["root"]) == []
    exit_mock.assert_called_with(0)


# ---------------------------------------------------------------------------
# (e) checkpoint recording a default-branch worktree
# ---------------------------------------------------------------------------


def _checkpoint_on(main_repo: dict[str, Path], target: Path) -> Path:
    checkpoint = main_repo["root"] / "checkpoint.json"
    context = FlowContext(target_worktree=str(target), intent_data={"slug": _SLUG, "target_branch": "main"})
    save_checkpoint(context, str(checkpoint))
    return checkpoint


@pytest.mark.parametrize("extra", [[], ["--from-stage", "review"]], ids=["checkpoint-stage", "from-review"])
def test_a_default_branch_checkpoint_is_refused(main_repo: dict[str, Path], extra: list[str]) -> None:
    checkout, flow = main_repo["checkout"], main_repo["flow"]
    checkpoint = _checkpoint_on(main_repo, checkout)
    before = _snapshot(checkout)

    pipeline, exit_mock, stderr = _run(["--checkpoint", str(checkpoint), *_FLOW_RUNNER_ARGS, *extra])

    exit_mock.assert_called_once_with(1)
    pipeline.assert_not_called()
    assert f"Checkpoint '{checkpoint}' target_worktree '{checkout}' has the default branch 'main'" in stderr
    assert f"--target-worktree {flow}" in stderr
    assert f"--target-worktree {checkout} to provision it" in stderr
    assert _flow_siblings(main_repo["root"]) == []
    assert _snapshot(checkout) == before


def test_a_default_branch_checkpoint_resumes_with_an_explicit_target(main_repo: dict[str, Path]) -> None:
    checkout, flow = main_repo["checkout"], main_repo["flow"]
    checkpoint = _checkpoint_on(main_repo, checkout)
    before = _snapshot(checkout)

    pipeline, exit_mock, _ = _run(
        ["--checkpoint", str(checkpoint), *_FLOW_RUNNER_ARGS, "--target-worktree", str(checkout)]
    )

    assert pipeline.call_args[1]["context"].target_worktree == str(flow)
    _assert_flow_worktree_off_origin_main(main_repo)
    exit_mock.assert_called_with(0)
    assert _snapshot(checkout) == before

    pipeline, exit_mock, _ = _run(["--checkpoint", str(checkpoint), *_FLOW_RUNNER_ARGS, "--target-worktree", str(flow)])

    assert pipeline.call_args[1]["context"].target_worktree == str(flow)
    exit_mock.assert_called_with(0)


# ---------------------------------------------------------------------------
# Engine guard: run_flow_pipeline refuses a default-branch target for callers that bypass the CLI
# ---------------------------------------------------------------------------


def test_run_flow_pipeline_refuses_a_default_branch_target(main_repo: dict[str, Path]) -> None:
    from sandbox_executor import flow as flow_module

    checkpoint = main_repo["root"] / "c.json"
    context = FlowContext(target_worktree=str(main_repo["checkout"]), intent_data={"slug": _SLUG}, dry_run=True)

    with patch.object(PipelineEngine, "execute_stage") as execute_stage:
        flow_module.run_flow_pipeline(context, from_stage="review", checkpoint_path=str(checkpoint))

    assert context.stage_results["review"].status == StageStatus.FAILED
    assert "has the default branch 'main' checked out" in (context.stage_results["review"].error or "")
    execute_stage.assert_not_called()
    assert not checkpoint.exists()


# ---------------------------------------------------------------------------
# (f) holon review, review-loop, pr-review-loop and calibrate
# ---------------------------------------------------------------------------

_DIRECT_COMMANDS = [
    ["review-loop", "I-1/P-2/E-3/_", "--local", "--dry-run"],
    ["review", "I-1/P-2/E-3/_", "--local", "--dry-run"],
    ["pr-review-loop", "I-1/P-2/E-3/_"],
    ["calibrate", "I-1/P-2/E-3/_"],
    ["calibrate", "I-1/P-2/E-3/_", "--no-commit"],
]


def _run_direct(argv: list[str]) -> tuple[int, str, list[MagicMock]]:
    # Outside a sandbox, as on CI: HOLON_ROLE or HOLON_IN_SANDBOX would turn the container dispatch into a local run.
    env = {k: v for k, v in os.environ.items() if k not in ("HOLON_ROLE", "HOLON_IN_SANDBOX")}
    env.update(HOLON_AGENT="antigravity-agent", HOLON_MODEL="gemini-3.8-flash-medium")
    with (
        patch.dict(os.environ, env, clear=True),
        patch("sandbox_executor.cli.detect_holon_coherence", return_value=False),
        patch("sandbox_executor.flow.run_review_loop_cli", return_value=0) as local_review,
        patch("sandbox_executor.cli.run_docker_container", return_value=0) as docker,
        patch("sandbox_executor.cli.run_calibrate") as calibrate,
        patch("sys.stderr", new_callable=io.StringIO) as stderr,
        patch("sys.argv", ["holon", *argv]),
        pytest.raises(SystemExit) as exit_info,
    ):
        cli_main()
    return int(exit_info.value.code or 0), stderr.getvalue(), [local_review, docker, calibrate]


@pytest.mark.parametrize("argv", _DIRECT_COMMANDS, ids=lambda argv: " ".join(argv[:1] + argv[2:]))
def test_direct_entry_points_refuse_a_default_branch_worktree(main_repo: dict[str, Path], argv: list[str]) -> None:
    checkout = main_repo["checkout"]
    before = _snapshot(checkout)

    code, stderr, callees = _run_direct([*argv, "--target-worktree", str(checkout)])

    assert code == 1
    assert f"Error: Target worktree '{checkout}' has the default branch 'main' checked out." in stderr
    assert f"holon {argv[0]} checks out branches" in stderr
    assert "--target-worktree <path>" in stderr
    for callee in callees:
        callee.assert_not_called()
    assert _snapshot(checkout) == before


@pytest.mark.parametrize("argv", _DIRECT_COMMANDS, ids=lambda argv: " ".join(argv[:1] + argv[2:]))
def test_direct_entry_points_run_on_another_branch(main_repo: dict[str, Path], argv: list[str]) -> None:
    feature = main_repo["root"] / "feature"
    # On the review branch itself: the review-loop container dispatch reviews that branch's worktree in place.
    _git(main_repo["checkout"], "worktree", "add", "-q", "-b", "I-1/P-2/E-3/_", str(feature), "origin/main")

    code, stderr, callees = _run_direct([*argv, "--target-worktree", str(feature)])

    assert code == 0, stderr
    assert sum(callee.call_count for callee in callees) == 1
