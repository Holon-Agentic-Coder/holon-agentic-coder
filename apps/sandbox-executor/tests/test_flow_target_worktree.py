"""Fail fast when the flow target worktree is not an attached, non-bare git working tree."""

import io
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor import flow as flow_module
from sandbox_executor.cli import main as cli_main
from sandbox_executor.flow import FlowContext, FlowStage, PipelineEngine, StageResult, StageStatus, save_checkpoint
from sandbox_executor.git_flow import is_working_tree, target_worktree_problem

_FLOW_RUNNER_ARGS = ["--agent", "antigravity-agent", "--model", "gemini-3.8-flash-medium", "--dry-run", "--json"]


def _git(*args: str, cwd: str | None = None) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Hermetic source repository, bare clone, attached worktree, plain directory and intent file.

    The cwd is pinned to a non-git directory, so the base repository lookup in ``ensure_worktree`` can never fall back
    to the developer's real repository.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    src = tmp_path / "src_repo"
    src.mkdir()
    _git("init", "-q", cwd=str(src))
    _git("config", "user.name", "Hermetic Test", cwd=str(src))
    _git("config", "user.email", "test@hermetic.local", cwd=str(src))
    _git("checkout", "-q", "-B", "main", cwd=str(src))
    (src / "README.md").write_text("# Hermetic\n", encoding="utf-8")
    (src / "pkg").mkdir()
    (src / "pkg" / "mod.py").write_text("", encoding="utf-8")
    _git("add", ".", cwd=str(src))
    _git("commit", "-q", "-m", "Initial commit", cwd=str(src))
    bare = tmp_path / "bare.git"
    _git("clone", "-q", "--bare", str(src), str(bare))
    attached = tmp_path / "attached"
    _git("worktree", "add", "-q", "-b", "attached-branch", str(attached), "main", cwd=str(src))
    plain = tmp_path / "plain"
    plain.mkdir()
    intent = tmp_path / "intent.json"
    intent.write_text(json.dumps({"slug": "wt-check", "target_branch": "main"}), encoding="utf-8")
    return {"root": tmp_path, "src": src, "bare": bare, "attached": attached, "plain": plain, "intent": intent}


# ---------------------------------------------------------------------------
# target_worktree_problem
# ---------------------------------------------------------------------------


def test_problem_is_none_for_working_trees_and_their_subdirectories(repos: dict[str, Path]) -> None:
    assert target_worktree_problem(str(repos["src"])) is None
    assert target_worktree_problem(str(repos["attached"])) is None
    assert target_worktree_problem(str(repos["src"] / "pkg")) is None


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("bare", "is a bare repository with no working tree"),
        ("plain", "is not inside a git working tree"),
        ("file", "is not a directory"),
        ("missing", "does not exist (it may have been deleted)"),
    ],
)
def test_problem_names_path_reason_and_fix(repos: dict[str, Path], name: str, reason: str) -> None:
    target = repos.get(name, repos["root"] / name)
    if name == "file":
        target.write_text("x", encoding="utf-8")

    problem = target_worktree_problem(str(target))

    assert problem is not None
    assert problem.startswith(f"Target worktree '{os.path.abspath(target)}' {reason}.")
    assert "git worktree add <path> <branch>" in problem
    assert "attached, non-bare git worktree" in problem


def test_problem_uses_caller_subject_and_hint(repos: dict[str, Path]) -> None:
    problem = target_worktree_problem(str(repos["plain"]), subject="Checkpoint 'c.json' target_worktree", hint=", or x")

    assert problem is not None
    assert problem.startswith(f"Checkpoint 'c.json' target_worktree '{repos['plain']}' is not inside")
    assert problem.endswith(", or x.")


# ---------------------------------------------------------------------------
# PipelineEngine pre-run guard
# ---------------------------------------------------------------------------


def test_engine_guard_fails_first_stage_before_any_stage_or_checkpoint(repos: dict[str, Path]) -> None:
    checkpoint = repos["root"] / "checkpoint.json"
    ctx = FlowContext(target_worktree=str(repos["plain"]), dry_run=True)
    engine = PipelineEngine(ctx, from_stage="review", checkpoint_path=str(checkpoint))
    engine.worktree_validator = target_worktree_problem

    with patch.object(PipelineEngine, "execute_stage") as execute_stage:
        results = engine.run()

    assert list(results) == ["review"]
    assert results["review"].status == StageStatus.FAILED
    assert results["review"].error == target_worktree_problem(str(repos["plain"]))
    execute_stage.assert_not_called()
    assert not checkpoint.exists()


def test_engine_without_validator_keeps_arbitrary_targets(repos: dict[str, Path]) -> None:
    ctx = FlowContext(target_worktree=str(repos["plain"]), dry_run=True)
    passed = StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS)

    with patch.object(PipelineEngine, "execute_stage", return_value=passed) as execute_stage:
        results = PipelineEngine(ctx, from_stage="calibrate").run()

    execute_stage.assert_called_once_with(FlowStage.CALIBRATE)
    assert results["calibrate"].status == StageStatus.SUCCESS


def test_run_flow_pipeline_wires_the_worktree_validator(repos: dict[str, Path]) -> None:
    checkpoint = repos["root"] / "checkpoint.json"
    ctx = FlowContext(target_worktree=str(repos["plain"]), dry_run=True)

    with patch.object(PipelineEngine, "execute_stage") as execute_stage:
        flow_module.run_flow_pipeline(ctx, from_stage="plan", checkpoint_path=str(checkpoint))

    assert ctx.stage_results["plan"].status == StageStatus.FAILED
    assert "is not inside a git working tree" in (ctx.stage_results["plan"].error or "")
    execute_stage.assert_not_called()
    assert not checkpoint.exists()


def test_run_flow_pipeline_accepts_an_attached_worktree(repos: dict[str, Path]) -> None:
    ctx = FlowContext(target_worktree=str(repos["attached"]), dry_run=True)
    passed = StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS)

    with patch.object(PipelineEngine, "execute_stage", return_value=passed) as execute_stage:
        flow_module.run_flow_pipeline(ctx, from_stage="calibrate", checkpoint_path=str(repos["root"] / "c.json"))

    execute_stage.assert_called_once_with(FlowStage.CALIBRATE)
    assert ctx.stage_results["calibrate"].status == StageStatus.SUCCESS


# ---------------------------------------------------------------------------
# holon flow CLI: fail fast on the resolved --target-worktree or checkpoint target_worktree
# ---------------------------------------------------------------------------


def _run_flow(argv: list[str]) -> tuple[MagicMock, MagicMock, str]:
    """Run ``holon flow`` with a patched pipeline and ``sys.exit``; return (pipeline mock, exit mock, stderr)."""
    with (
        patch("sandbox_executor.cli.run_flow_pipeline") as mock_pipeline,
        patch("sys.exit") as mock_exit,
        patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
        patch("sys.argv", ["holon", "flow", *argv]),
    ):
        mock_pipeline.return_value = {"status": "ok"}
        cli_main()
    return mock_pipeline, mock_exit, mock_stderr.getvalue()


def _flow_siblings(path: Path) -> list[str]:
    return sorted(p.name for p in path.parent.iterdir() if p.name.startswith("flow-"))


def _write_checkpoint(repos: dict[str, Path], target_worktree: Path) -> Path:
    checkpoint = repos["root"] / "checkpoint.json"
    ctx = FlowContext(target_worktree=str(target_worktree), intent_data={"slug": "wt-check", "target_branch": "main"})
    save_checkpoint(ctx, str(checkpoint))
    return checkpoint


def test_flow_rejects_plain_non_git_target_worktree(repos: dict[str, Path]) -> None:
    plain = repos["plain"]

    pipeline, exit_mock, stderr = _run_flow([str(repos["intent"]), *_FLOW_RUNNER_ARGS, "--target-worktree", str(plain)])

    exit_mock.assert_called_once_with(1)
    pipeline.assert_not_called()
    assert f"Target worktree '{plain}' is not inside a git working tree" in stderr
    assert "git worktree add" in stderr
    assert _flow_siblings(plain) == []


def test_flow_rejects_deleted_target_worktree(repos: dict[str, Path]) -> None:
    deleted = repos["root"] / "deleted"

    pipeline, exit_mock, stderr = _run_flow(
        [str(repos["intent"]), *_FLOW_RUNNER_ARGS, "--target-worktree", str(deleted)]
    )

    exit_mock.assert_called_once_with(1)
    pipeline.assert_not_called()
    assert "does not exist" in stderr
    assert "git worktree add" in stderr
    assert _flow_siblings(deleted) == []


def _worktree_names(repo: Path) -> list[str]:
    out = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=str(repo), check=True, capture_output=True)
    lines = out.stdout.decode().splitlines()
    return sorted(os.path.basename(line.split(" ", 1)[1]) for line in lines if line.startswith("worktree "))


def test_flow_rejects_deleted_target_worktree_with_cwd_inside_a_repo(
    repos: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repos["src"])
    before = _worktree_names(repos["src"])
    deleted = repos["root"] / "deleted"

    pipeline, exit_mock, stderr = _run_flow(
        [str(repos["intent"]), *_FLOW_RUNNER_ARGS, "--target-worktree", str(deleted)]
    )

    exit_mock.assert_called_once_with(1)
    pipeline.assert_not_called()
    assert f"Target worktree '{deleted}' does not exist" in stderr
    assert _flow_siblings(deleted) == []
    assert _worktree_names(repos["src"]) == before


def test_flow_provisions_absent_derived_flow_target(repos: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(repos["src"])
    derived = repos["root"] / "flow-wt-check"

    pipeline, exit_mock, stderr = _run_flow(
        [str(repos["intent"]), *_FLOW_RUNNER_ARGS, "--target-worktree", str(derived)]
    )

    pipeline.assert_called_once()
    assert os.path.realpath(pipeline.call_args[1]["context"].target_worktree) == os.path.realpath(derived)
    assert is_working_tree(str(derived))
    exit_mock.assert_called_with(0)
    assert "Error:" not in stderr


def test_flow_provisions_absent_target_under_a_repo(repos: dict[str, Path]) -> None:
    missing = repos["src"] / "missing"

    pipeline, exit_mock, _ = _run_flow([str(repos["intent"]), *_FLOW_RUNNER_ARGS, "--target-worktree", str(missing)])

    pipeline.assert_called_once()
    target = pipeline.call_args[1]["context"].target_worktree
    assert os.path.basename(target) == "flow-wt-check"
    assert is_working_tree(target)
    exit_mock.assert_called_with(0)


def test_flow_reports_the_provisioning_error_for_a_bare_target(repos: dict[str, Path]) -> None:
    with patch("sandbox_executor.cli.commands._flow_worktree.ensure_worktree", side_effect=RuntimeError("worktree add exploded")):
        pipeline, exit_mock, stderr = _run_flow(
            [str(repos["intent"]), *_FLOW_RUNNER_ARGS, "--target-worktree", str(repos["bare"])]
        )

    exit_mock.assert_called_once_with(1)
    pipeline.assert_not_called()
    assert "is a bare repository with no working tree" in stderr
    assert "(auto-provisioning from this target failed: worktree add exploded)" in stderr


def test_flow_accepts_attached_worktree(repos: dict[str, Path]) -> None:
    attached = str(repos["attached"])

    pipeline, exit_mock, stderr = _run_flow([str(repos["intent"]), *_FLOW_RUNNER_ARGS, "--target-worktree", attached])

    pipeline.assert_called_once()
    assert pipeline.call_args[1]["context"].target_worktree == attached
    exit_mock.assert_called_with(0)
    assert "Error:" not in stderr


@pytest.mark.parametrize("from_stage", [[], ["--from-stage", "review"]])
@pytest.mark.parametrize("recorded", ["plain", "deleted"])
def test_flow_checkpoint_resume_rejects_non_worktree_target(
    repos: dict[str, Path], recorded: str, from_stage: list[str]
) -> None:
    target = repos.get(recorded, repos["root"] / recorded)
    checkpoint = _write_checkpoint(repos, target)

    pipeline, exit_mock, stderr = _run_flow(["--checkpoint", str(checkpoint), *_FLOW_RUNNER_ARGS, *from_stage])

    exit_mock.assert_called_once_with(1)
    pipeline.assert_not_called()
    assert f"Checkpoint '{checkpoint}' target_worktree '{target}'" in stderr
    assert "pass --target-worktree to resume against a different attached worktree" in stderr
    assert _flow_siblings(target) == []


def test_flow_checkpoint_resume_accepts_explicit_valid_target_worktree(repos: dict[str, Path]) -> None:
    checkpoint = _write_checkpoint(repos, repos["plain"])
    attached = str(repos["attached"])

    pipeline, exit_mock, _ = _run_flow(
        ["--checkpoint", str(checkpoint), *_FLOW_RUNNER_ARGS, "--target-worktree", attached]
    )

    pipeline.assert_called_once()
    assert pipeline.call_args[1]["context"].target_worktree == attached
    exit_mock.assert_called_with(0)


def test_flow_checkpoint_resume_provisions_recorded_bare_target(repos: dict[str, Path]) -> None:
    checkpoint = _write_checkpoint(repos, repos["bare"])

    pipeline, exit_mock, _ = _run_flow(["--checkpoint", str(checkpoint), *_FLOW_RUNNER_ARGS])

    pipeline.assert_called_once()
    target = pipeline.call_args[1]["context"].target_worktree
    assert os.path.basename(target) == "flow-wt-check"
    assert is_working_tree(target)
    exit_mock.assert_called_with(0)
