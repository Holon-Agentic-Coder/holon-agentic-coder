"""Stage 4 hands the shared flow worktree back on the execution branch, whatever the review loop did to HEAD."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sandbox_executor.flow import FlowContext, StageStatus, run_review_stage
from sandbox_executor.git import run_git

from tests.review_fakes import clean_reviewer

EXEC = "I-1-x/P-2-y/E-3-z/_"
LEDGER = Path("holon-knowledge") / "ledger" / "executions.jsonl"
STAGE = "sandbox_executor.flow.stages.review"
PACKAGE = Path(__file__).resolve().parents[3] / "src" / "sandbox_executor"
#: The modules that run during Stage 4, host and container side.
REVIEW_STAGE_MODULES = [
    "flow/stages/review.py",
    *sorted(str(p.relative_to(PACKAGE)) for p in (PACKAGE / "flow" / "review").glob("*.py")),
    "flow/stage_conflicts.py",
    "flow/git_refs.py",
    "resolver_evidence.py",
    "entrypoint/review_loop.py",
    "entrypoint/review_verification.py",
]
_SHA = re.compile(r"^[0-9a-f]{40}$")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def flow_worktree(tmp_path: Path) -> Path:
    """A flow worktree with the execution branch checked out and a committed ledger."""
    path = tmp_path / "flow"
    subprocess.run(["git", "init", "-q", "-b", EXEC, str(path)], capture_output=True, check=True)
    (path / LEDGER).parent.mkdir(parents=True)
    (path / LEDGER).write_text('{"execution_id": "E-3"}\n', encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "execution")
    return path


def _detach_and_append_a_row(repo_dir: str) -> None:
    """What the incident's unnamed step did: detach HEAD at the branch tip while a ledger row sits uncommitted."""
    subprocess.run(["git", "checkout", "-q", "--detach", "HEAD"], cwd=repo_dir, check=True)
    with (Path(repo_dir) / LEDGER).open("a", encoding="utf-8") as ledger:
        ledger.write('{"action_id": "ACT-REV-1"}\n')


def _approved_loop(context: FlowContext, *, github: Any = None) -> dict[str, Any]:
    _detach_and_append_a_row(context.target_worktree)
    return {
        "consensus": {"approved": True, "status": "approved", "iterations": 1},
        "ensemble_verdict": "APPROVED",
        "reviewers": [clean_reviewer(1)],
        "mergeability": {"is_dirty": False},
    }


def _crashing_loop(context: FlowContext, *, github: Any = None) -> dict[str, Any]:
    _detach_and_append_a_row(context.target_worktree)
    raise RuntimeError("container dispatch crashed")


def _run_stage(worktree: Path, loop) -> tuple[FlowContext, Any, list[list[str]]]:
    ctx = FlowContext(target_worktree=str(worktree), execution_branch=EXEC, agent="a", model="m")
    calls: list[list[str]] = []

    def recording_git(args: list[str], cwd: str | None = None, **kwargs: Any):
        if cwd == str(worktree):
            calls.append(list(args))
        return run_git(args, cwd=cwd, **kwargs)

    with (
        patch(f"{STAGE}.run_git", side_effect=recording_git),
        patch(f"{STAGE}.at_stage_boundary", return_value=None),
        patch(f"{STAGE}._run_engine_review_loop", side_effect=loop),
    ):
        try:
            return ctx, run_review_stage(ctx), calls
        except RuntimeError as exc:
            return ctx, exc, calls


def _assert_on_branch_with_the_row_committed(worktree: Path) -> None:
    """The detached HEAD is re-attached first, so the exit commits the row on the branch and leaves a clean tree."""
    assert _git(worktree, "symbolic-ref", "--short", "HEAD") == EXEC
    assert _git(worktree, "status", "--porcelain") == ""
    assert '"ACT-REV-1"' in _git(worktree, "show", f"HEAD:{LEDGER.as_posix()}")


def test_after_a_review_iteration_the_worktree_is_on_the_execution_branch(flow_worktree: Path) -> None:
    ctx, result, calls = _run_stage(flow_worktree, _approved_loop)

    assert result.status is StageStatus.APPROVED
    _assert_on_branch_with_the_row_committed(flow_worktree)
    assert any("Stage 4 review loop left the worktree HEAD detached at" in line for line in ctx.logs)
    assert any(f"re-attached '{EXEC}'" in line for line in ctx.logs)
    # Acceptance criterion 3: the stage itself never checks out a bare commit in the shared worktree.
    bare = [
        c for c in calls if c[:1] in (["checkout"], ["switch"]) and any(a == "--detach" or _SHA.match(a) for a in c)
    ]
    assert bare == []


def test_a_review_loop_that_raises_still_hands_back_the_execution_branch(flow_worktree: Path) -> None:
    ctx, outcome, _calls = _run_stage(flow_worktree, _crashing_loop)

    assert isinstance(outcome, RuntimeError) and "container dispatch crashed" in str(outcome)
    _assert_on_branch_with_the_row_committed(flow_worktree)
    assert any("Stage 4 review loop left the worktree HEAD detached at" in line for line in ctx.logs)


def test_a_head_detached_at_the_tip_before_the_stage_is_reattached_not_checked_out(flow_worktree: Path) -> None:
    _git(flow_worktree, "checkout", "-q", "--detach", "HEAD")
    loop = {"consensus": {"approved": True}, "ensemble_verdict": "APPROVED", "reviewers": [clean_reviewer(1)]}

    ctx, result, calls = _run_stage(flow_worktree, lambda _ctx, **_kw: loop)

    assert result.status is StageStatus.APPROVED
    assert _git(flow_worktree, "symbolic-ref", "--short", "HEAD") == EXEC
    assert ["checkout", EXEC] not in calls
    assert (ctx.starting_branch, ctx.starting_detached) == (EXEC, False)


def test_a_failed_pre_review_checkout_is_not_blamed_on_the_review_loop(flow_worktree: Path) -> None:
    _git(flow_worktree, "checkout", "-q", "-b", "operator")
    (flow_worktree / LEDGER).write_text('{"execution_id": "operator"}\n', encoding="utf-8")
    _git(flow_worktree, "commit", "-qam", "operator")
    (flow_worktree / LEDGER).write_text('{"execution_id": "uncommitted"}\n', encoding="utf-8")

    ctx, result, calls = _run_stage(flow_worktree, _approved_loop)

    assert result.status is StageStatus.FAILED
    assert "could not check out execution branch" in (result.error or "")
    assert _git(flow_worktree, "symbolic-ref", "--short", "HEAD") == "operator"
    assert calls.count(["checkout", EXEC]) == 1
    assert not any("Stage 4 review loop" in line for line in ctx.logs)


@pytest.mark.parametrize("module", REVIEW_STAGE_MODULES)
def test_no_review_stage_module_checks_out_a_bare_commit(module: str) -> None:
    source = (PACKAGE / module).read_text(encoding="utf-8")
    for verb in ("checkout", "switch"):
        assert not re.search(rf'"{verb}",\s*"--detach"', source), f"{module} detaches HEAD with git {verb}"


def _add_submodule_and_break_gitfile(worktree: Path, tmp_path: Path, *, switch_to: str | None = None) -> Path:
    sub_src = tmp_path / "holon_coherence_src"
    subprocess.run(["git", "init", "-q", "-b", "main", str(sub_src)], check=True)
    (sub_src / "README.md").write_text("coherence\n", encoding="utf-8")
    _git(sub_src, "add", "README.md")
    _git(sub_src, "commit", "-qm", "init submodule")
    _git(worktree, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub_src), "holon-coherence")
    _git(worktree, "commit", "-qm", "add submodule")
    if switch_to is not None:
        _git(worktree, "checkout", "-q", "-b", switch_to)
    raw_mod = _git(worktree, "rev-parse", "--git-path", "modules/holon-coherence")
    module_git_dir = Path(raw_mod) if os.path.isabs(raw_mod) else worktree / raw_mod
    (worktree / "holon-coherence" / ".git").write_text(
        "gitdir: /nonexistent-container/workspace/.git/modules/holon-coherence\n",
        encoding="utf-8",
    )
    _git(
        worktree,
        "config",
        "--file",
        str(module_git_dir / "config"),
        "core.worktree",
        "/nonexistent-container/workspace/holon-coherence",
    )
    return module_git_dir


def test_stage4_repairs_broken_submodule_gitfile_before_branch_switch(flow_worktree: Path, tmp_path: Path) -> None:
    _add_submodule_and_break_gitfile(flow_worktree, tmp_path, switch_to="operator")

    ctx, result, _calls = _run_stage(flow_worktree, _approved_loop)

    assert result.status is StageStatus.APPROVED
    gitdir_line = (flow_worktree / "holon-coherence" / ".git").read_text(encoding="utf-8").strip()
    assert gitdir_line.startswith("gitdir: ")
    assert not os.path.isabs(gitdir_line.removeprefix("gitdir: "))
    assert _git(flow_worktree / "holon-coherence", "rev-parse", "--is-inside-work-tree") == "true"
    assert any("holon-coherence/.git" in line for line in ctx.logs)


def test_stage4_repairs_broken_submodule_gitfile_when_already_on_execution_branch(
    flow_worktree: Path,
    tmp_path: Path,
) -> None:
    _add_submodule_and_break_gitfile(flow_worktree, tmp_path)

    ctx, result, calls = _run_stage(flow_worktree, _approved_loop)

    assert result.status is StageStatus.APPROVED
    assert ["checkout", EXEC] not in calls
    gitdir_line = (flow_worktree / "holon-coherence" / ".git").read_text(encoding="utf-8").strip()
    assert gitdir_line.startswith("gitdir: ")
    assert not os.path.isabs(gitdir_line.removeprefix("gitdir: "))
    assert _git(flow_worktree / "holon-coherence", "rev-parse", "--is-inside-work-tree") == "true"
    assert any("holon-coherence/.git" in line for line in ctx.logs)


def test_stage4_fails_closed_when_submodule_gitfile_cannot_be_repaired(flow_worktree: Path, tmp_path: Path) -> None:
    module_git_dir = _add_submodule_and_break_gitfile(flow_worktree, tmp_path, switch_to="operator")
    shutil.rmtree(module_git_dir)

    _ctx, result, calls = _run_stage(flow_worktree, _approved_loop)

    assert result.status is StageStatus.FAILED
    assert "holon-coherence/.git" in (result.error or "")
    assert ["checkout", EXEC] not in calls
