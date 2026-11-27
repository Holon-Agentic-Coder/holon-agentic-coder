from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sandbox_executor.worktree_head import (
    ReattachStatus,
    head_guard,
    read_head_state,
    reattach_branch,
    restore_branch,
)

BRANCH = "I-1-x/P-2-y/E-3-z/_"
LEDGER = Path("holon-knowledge") / "ledger" / "executions.jsonl"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def _uncommitted_state(repo: Path) -> tuple[str, str, str]:
    return (
        _git(repo, "status", "--porcelain", "--untracked-files=all"),
        _git(repo, "diff"),
        _git(repo, "diff", "--cached"),
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "flow"
    subprocess.run(["git", "init", "-q", "-b", BRANCH, str(path)], capture_output=True, check=True)
    (path / LEDGER).parent.mkdir(parents=True)
    (path / LEDGER).write_text('{"execution_id": "E-1"}\n', encoding="utf-8")
    (path / "app.py").write_text("print('one')\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "first")
    (path / "app.py").write_text("print('two')\n", encoding="utf-8")
    _git(path, "commit", "-qam", "second")
    return path


def _leave_changes_as_stage_4_does(repo: Path) -> None:
    (repo / "app.py").write_text("print('edited')\n", encoding="utf-8")
    (repo / "notes.txt").write_text("scratch\n", encoding="utf-8")
    with (repo / LEDGER).open("a", encoding="utf-8") as ledger:
        ledger.write('{"action_id": "ACT-REV-1"}\n')


def test_a_head_on_the_branch_is_left_alone(repo: Path) -> None:
    before = _uncommitted_state(repo)

    outcome = reattach_branch(str(repo), BRANCH)

    assert outcome.status is ReattachStatus.ON_BRANCH
    assert _git(repo, "symbolic-ref", "--short", "HEAD") == BRANCH
    assert _uncommitted_state(repo) == before


def test_a_head_detached_at_the_tip_is_reattached_and_keeps_every_uncommitted_change(repo: Path) -> None:
    tip = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "--detach", "HEAD")
    _leave_changes_as_stage_4_does(repo)
    _git(repo, "add", "notes.txt")
    before = _uncommitted_state(repo)
    state = read_head_state(str(repo), BRANCH)
    assert state.detached_at_tip and not state.detached_elsewhere and not state.on_branch

    outcome = reattach_branch(str(repo), BRANCH)

    assert outcome.status is ReattachStatus.REATTACHED
    assert f"HEAD was detached at its tip {tip[:12]}" in outcome.message
    assert _git(repo, "symbolic-ref", "--short", "HEAD") == BRANCH
    assert _git(repo, "rev-parse", "HEAD") == tip
    assert _uncommitted_state(repo) == before
    assert '"ACT-REV-1"' in (repo / LEDGER).read_text(encoding="utf-8")


def test_a_head_detached_elsewhere_is_refused_and_nothing_changes(repo: Path) -> None:
    parent = _git(repo, "rev-parse", "HEAD~1")
    tip = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "--detach", "HEAD~1")
    _leave_changes_as_stage_4_does(repo)
    before = _uncommitted_state(repo)

    outcome = reattach_branch(str(repo), BRANCH)

    assert outcome.status is ReattachStatus.REFUSED
    assert parent[:12] in outcome.message and tip[:12] in outcome.message and f"'{BRANCH}'" in outcome.message
    assert "refusing to re-attach" in outcome.message
    assert subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=repo).returncode != 0
    assert _git(repo, "rev-parse", "HEAD") == parent
    assert _uncommitted_state(repo) == before


def test_a_missing_local_branch_is_refused(repo: Path) -> None:
    _git(repo, "checkout", "-q", "--detach", "HEAD")

    outcome = reattach_branch(str(repo), "feature/gone")

    assert outcome.status is ReattachStatus.REFUSED
    assert "the local branch 'feature/gone' does not exist" in outcome.message


def test_another_checked_out_branch_is_refused(repo: Path) -> None:
    _git(repo, "checkout", "-q", "-b", "operator-work")

    outcome = reattach_branch(str(repo), BRANCH)

    assert outcome.status is ReattachStatus.REFUSED
    assert _git(repo, "symbolic-ref", "--short", "HEAD") == "operator-work"


def test_the_guard_names_the_step_and_reattaches_after_a_block_that_detaches(repo: Path) -> None:
    logs: list[str] = []

    with head_guard(str(repo), BRANCH, "reviewer ensemble (iteration 2)", logs.append) as guard:
        _git(repo, "checkout", "-q", "--detach", "HEAD")
        _leave_changes_as_stage_4_does(repo)

    assert guard.outcome is not None and guard.outcome.status is ReattachStatus.REATTACHED
    assert not guard.refused
    assert _git(repo, "symbolic-ref", "--short", "HEAD") == BRANCH
    assert any("reviewer ensemble (iteration 2) left the worktree HEAD detached at" in line for line in logs)
    assert "app.py" in _git(repo, "status", "--porcelain")


def test_the_guard_repairs_and_re_raises_the_blocks_own_exception(repo: Path) -> None:
    logs: list[str] = []

    with pytest.raises(RuntimeError, match="turn crashed"), head_guard(str(repo), BRANCH, "resolver turn", logs.append):
        _git(repo, "checkout", "-q", "--detach", "HEAD")
        raise RuntimeError("turn crashed")

    assert _git(repo, "symbolic-ref", "--short", "HEAD") == BRANCH


def test_the_guard_reports_a_refusal_when_the_block_moves_head_to_another_commit(repo: Path) -> None:
    logs: list[str] = []

    with head_guard(str(repo), BRANCH, "resolver turn", logs.append) as guard:
        _git(repo, "checkout", "-q", "--detach", "HEAD~1")

    assert guard.refused
    assert subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=repo).returncode != 0


def test_the_guard_is_not_armed_when_the_branch_was_not_checked_out(repo: Path) -> None:
    _git(repo, "checkout", "-q", "--detach", "HEAD~1")
    logs: list[str] = []

    with head_guard(str(repo), BRANCH, "reviewer ensemble", logs.append) as guard:
        pass

    assert guard.outcome is None
    assert logs == []


def test_restore_falls_back_to_a_plain_checkout_that_carries_changes(repo: Path) -> None:
    _git(repo, "checkout", "-q", "--detach", "HEAD~1")
    (repo / "notes.txt").write_text("scratch\n", encoding="utf-8")
    logs: list[str] = []

    restore_branch(str(repo), BRANCH, "Stage 4 review loop", logs.append)

    assert _git(repo, "symbolic-ref", "--short", "HEAD") == BRANCH
    assert (repo / "notes.txt").exists()
    assert any("checked out" in line for line in logs)


def test_restore_leaves_a_directory_that_is_not_a_git_worktree_alone(tmp_path: Path) -> None:
    logs: list[str] = []

    restore_branch(str(tmp_path), BRANCH, "Stage 4 review loop", logs.append)

    assert logs == []
