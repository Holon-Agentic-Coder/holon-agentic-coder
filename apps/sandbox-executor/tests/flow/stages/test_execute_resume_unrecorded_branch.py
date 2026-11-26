"""Hermetic tests of a Stage 3 resume adopting an execution branch a host crash left without a summary row."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor.flow import FlowContext, failed_partial_head
from sandbox_executor.flow.partial_salvage import salvage_for_resume
from sandbox_executor.flow.stages.execute import ExecuteStage
from sandbox_executor.flow.unrecorded_branch_adoption import _find_unrecorded_branch

from tests.repo_templates import copy_template, shared_template
from tests.test_partial_salvage import (
    EXEC_BRANCH,
    EXEC_ID,
    LEDGER,
    PLAN,
    _build_work,
    commit_all,
    context,
    git,
    prefix,
    rows,
)

EXECUTE = "sandbox_executor.flow.stages.execute"
NOTES = f"executions/{EXEC_ID}.md"


def branch_of(exec_id: str) -> str:
    return f"{prefix()}/{exec_id}/_"


@pytest.fixture
def work(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A clone on a fresh local execution branch ``E-100`` of the plan, with its local bare origin."""
    copy_template(shared_template(tmp_path_factory, "partial-salvage", _build_work)[0], tmp_path)
    return tmp_path / "work"


def record_row(repo: Path, exec_id: str, status: str, **fields: object) -> None:
    """Commit a summary row for ``exec_id`` on the checked-out branch, as the executor container does."""
    row = {"entry_type": "summary", "execution_id": exec_id, "plan_branch": PLAN, "status": status, **fields}
    with (repo / LEDGER).open("a", encoding="utf-8") as ledger:
        ledger.write(json.dumps(row) + "\n")
    commit_all(repo, f"execute: record {status} row for {exec_id}")


def code_commit(repo: Path, name: str = "src.py", notes: bool = True) -> str:
    """Commit real work (and, by default, the run's notes file) on the checked-out branch; returns the commit."""
    (repo / "executions").mkdir(exist_ok=True)
    (repo / name).write_text(f"# work in {name}\n", encoding="utf-8")
    if notes:
        (repo / NOTES).write_text("# notes\n", encoding="utf-8")
    commit_all(repo, f"work on {name}")
    return git(repo, "rev-parse", "HEAD")


def executor(repo: Path, new_id: str, seen: list[list[str]]) -> Callable[..., int]:
    """A dispatch that acts like the executor: cut ``new_id`` from the partial head or plan tip, commit and record."""

    def dispatch(_ctx: FlowContext, _role: str, args: list[str]) -> int:
        seen.append([*args, git(repo, "status", "--porcelain")])
        partial = args[3] if len(args) > 3 else None
        start = partial and failed_partial_head(str(repo), f"refs/remotes/origin/{partial}", partial.split("/")[-2])
        git(repo, "checkout", "-q", "-b", branch_of(new_id), start or f"origin/{PLAN}")
        (repo / "done.py").write_text(f"# {new_id}\n", encoding="utf-8")
        record_row(repo, new_id, "success")
        return 0

    return dispatch


def resume(ctx: FlowContext, dispatch: Callable[..., int], until_dispatch: bool = False) -> ExecuteStage:
    """Run the Stage 3 steps of a resume up to the adoption of the executor's branch (or up to its dispatch)."""
    stage = ExecuteStage(ctx, resume_partial=True)
    stage.state.target_branch = "main"  # what admit_and_refresh_plan sets
    steps = [stage.preflight, stage.resume_point, stage.salvage_partial]
    if not until_dispatch:
        steps += [stage.dispatch_executor, stage.adopt_execution_branch]
    with patch(f"{EXECUTE}._dispatch_role", side_effect=dispatch):
        for step in steps:
            assert step() is None, ctx.logs
    return stage


def test_unrecorded_branch_on_origin_is_adopted_and_continued(work: Path) -> None:
    tip = code_commit(work)
    git(work, "push", "-q", "origin", EXEC_BRANCH)
    ctx, seen = context(work), []
    stage = resume(ctx, executor(work, "E-200-CLA-CO5.5", seen))
    adopted = [row for row in rows(work) if row.get("execution_id") == EXEC_ID][-1]
    assert adopted["status"] == "failure" and adopted["partial_head"] == tip
    assert adopted["adopted_unrecorded"] is True and adopted["failure_reason"] == "interrupted"
    assert seen[0][:4] == [PLAN, "claude-agent", "claude-opus-5-5", EXEC_BRANCH]
    assert any(f"continuing partial work from {EXEC_BRANCH}" in line for line in ctx.logs)
    assert any("adopted unrecorded execution branch" in line for line in ctx.logs)
    assert stage.state.exec_id == "E-200-CLA-CO5.5"
    assert git(work, "merge-base", "--is-ancestor", tip, "HEAD") == ""


def test_dirty_unrecorded_branch_is_committed_as_partial_work_first(work: Path) -> None:
    code_commit(work)
    git(work, "push", "-q", "origin", EXEC_BRANCH)
    (work / "tests" / "conftest.py").write_text("# interrupted edit\n", encoding="utf-8")
    (work / NOTES).write_text("# notes, half written\n", encoding="utf-8")
    ctx, seen = context(work), []
    resume(ctx, executor(work, "E-200-CLA-CO5.5", seen))
    salvage = git(work, "log", "-1", "--format=%H", f"--grep=execute: {EXEC_ID} partial work (interrupted)", "--all")
    assert sorted(git(work, "show", "--name-only", "--format=", salvage).splitlines()) == [NOTES, "tests/conftest.py"]
    assert seen[0][-1] == ""  # the worktree was clean when the executor started
    assert failed_partial_head(str(work), f"refs/remotes/origin/{EXEC_BRANCH}", EXEC_ID) == salvage
    assert rows(work)[-1]["adopted_unrecorded"] is True


def test_second_resume_adopts_the_minted_branch_and_reads_the_executors_id(work: Path) -> None:
    code_commit(work)
    git(work, "push", "-q", "origin", EXEC_BRANCH)
    ctx = context(work)
    assert salvage_for_resume(ctx, prefix(), {EXEC_BRANCH})[1] is None
    minted = branch_of("E-200-CLA-CO5.5")
    git(work, "checkout", "-q", "-b", minted)
    tip = code_commit(work, "more.py", notes=False)
    ctx = context(work)
    ctx.execution_branch = EXEC_BRANCH  # a stale checkpoint
    seen: list[list[str]] = []
    stage = resume(ctx, executor(work, "E-300-CLA-CO5.5", seen))
    assert failed_partial_head(str(work), f"refs/remotes/origin/{minted}", "E-200-CLA-CO5.5") == tip
    assert minted in stage.state.existing_executions and seen[0][3] == minted
    assert stage.state.exec_id == "E-300-CLA-CO5.5" and stage.state.summary_row is not None
    assert stage.state.summary_row["execution_id"] == "E-300-CLA-CO5.5"
    assert ctx.execution_branch == branch_of("E-300-CLA-CO5.5")


def _no_execution_branch(work: Path) -> None:
    git(work, "checkout", "-q", PLAN)
    git(work, "branch", "-q", "-D", EXEC_BRANCH)


def _record_success(work: Path) -> None:
    code_commit(work)
    record_row(work, EXEC_ID, "success")


def _record_failure(work: Path) -> None:
    record_row(work, EXEC_ID, "failure", partial_head=code_commit(work))


@pytest.mark.parametrize(
    ("setup", "args"),
    [
        (_record_success, 3),
        (_record_failure, 4),
        (_no_execution_branch, 3),
        (lambda work: code_commit(work, "executions/E-100-claude-agent-claude-opus-5-5.md", notes=False), 3),
    ],
    ids=["success-row", "failure-row", "no-execution-branch", "only-metadata-commits"],
)
def test_recorded_or_workless_branches_behave_as_before(work: Path, setup: Callable[[Path], None], args: int) -> None:
    setup(work)
    if args == 4 or setup is _record_success:
        git(work, "push", "-q", "origin", EXEC_BRANCH)
    before = rows(work) if git(work, "ls-remote", "origin", EXEC_BRANCH) else None
    ctx = context(work)
    stage = resume(ctx, executor(work, "E-200-CLA-CO5.5", []), until_dispatch=True)
    assert len(stage.state.executor_args) == args
    assert not any("adopted unrecorded" in line for line in ctx.logs)
    if before is not None:
        assert rows(work) == before
    if args == 3:
        assert any("no failed execution branch carries partial work" in line for line in ctx.logs)


def test_a_blocked_checkout_names_the_files(work: Path) -> None:
    _record_success(work)
    git(work, "push", "-q", "origin", EXEC_BRANCH)
    (work / NOTES).write_text("# notes edited after the run\n", encoding="utf-8")
    ctx = context(work)
    stage = ExecuteStage(ctx, resume_partial=True)
    stage.state.target_branch = "main"
    with patch(f"{EXECUTE}._dispatch_role") as dispatch:
        assert stage.preflight() is None and stage.resume_point() is None
        result = stage.salvage_partial()
    dispatch.assert_not_called()
    assert result is not None and result.error is not None
    assert NOTES in result.error and "would be overwritten" in result.error and "exit 1" not in result.error


def test_diverged_local_and_origin_are_left_alone(work: Path) -> None:
    code_commit(work)
    git(work, "push", "-q", "origin", EXEC_BRANCH)
    git(work, "reset", "-q", "--hard", "HEAD~1")
    code_commit(work, "other.py")
    ctx = context(work)
    found = _find_unrecorded_branch(str(work), PLAN, prefix(), {EXEC_BRANCH}, ctx.log)
    assert found is None and any("diverged" in line for line in ctx.logs)


def test_local_only_branch_is_found(work: Path) -> None:
    tip = code_commit(work)
    found = _find_unrecorded_branch(str(work), PLAN, prefix(), set())
    assert found is not None and (found.branch, found.tip) == (EXEC_BRANCH, tip)
    assert found.checked_out and found.commits_past_plan == 1
