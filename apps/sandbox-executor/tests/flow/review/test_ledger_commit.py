"""Stage 4's ledger commits: only the three ledgers, the review-loop identity, a fast-forward push, never a raise.

Every test runs real git against a local bare ``origin`` under ``tmp_path``; nothing reaches the network.
"""

from __future__ import annotations

import subprocess

import pytest
from sandbox_executor.agent_runner import SANDBOX_WORKSPACE_MOUNT
from sandbox_executor.flow import FlowContext
from sandbox_executor.flow.review import ledger_commit
from sandbox_executor.review_target import ReviewTarget
from sandbox_executor.worktree_head import restore_branch

from tests.flow.helpers import _git
from tests.review_flow_repo import LEDGER, added_rows, append_row, changed_paths, flow_worktree_copy
from tests.review_loop_stubs import EXEC_TARGET

PLANS = "holon-knowledge/ledger/plans.jsonl"


def _context(work, **kwargs) -> FlowContext:
    return FlowContext(repo_dir=str(work), execution_branch=kwargs.pop("branch", EXEC_TARGET), **kwargs)


def _origin_tip(origin, branch: str = EXEC_TARGET) -> str:
    return _git(origin, "rev-parse", f"refs/heads/{branch}")


def test_only_the_ledgers_are_committed_and_pushed_while_code_changes_stay_put(tmp_path, tmp_path_factory):
    origin, work = flow_worktree_copy(tmp_path_factory, tmp_path)
    (work / "src" / "app.py").write_text("x = 2\n", encoding="utf-8")
    _git(work, "add", "src/app.py")
    (work / "src" / "other.py").write_text("y = 1\n", encoding="utf-8")
    append_row(work, {"execution_id": "E-row"})
    append_row(work, {"plan_id": "P-new"}, PLANS)

    commit = ledger_commit._commit_pending_ledger_rows(_context(work), 3)

    assert commit is not None and commit.pushed
    assert sorted(changed_paths(work)) == [LEDGER, PLANS]
    assert [row["execution_id"] for row in added_rows(work)] == ["E-row"]
    assert _origin_tip(origin) == commit.sha == _git(work, "rev-parse", "HEAD")
    assert _git(work, "diff", "--cached", "--name-only") == "src/app.py"
    assert "?? src/other.py" in _git(work, "status", "--porcelain")
    assert ledger_commit._pending_ledger_paths(str(work)) == []


def test_the_commit_carries_the_review_identity_and_no_co_author(tmp_path, tmp_path_factory):
    _, work = flow_worktree_copy(tmp_path_factory, tmp_path)
    append_row(work, {"execution_id": "E-row"})

    ledger_commit._commit_pending_ledger_rows(_context(work), 2)

    message = _git(work, "log", "-1", "--format=%B")
    assert message.splitlines()[0] == "chore(review): record review ledger rows (iteration 2)"
    assert "Action-Origin: pr_review" in message and "Review-Iteration: 2" in message
    assert "co-authored-by" not in message.lower()
    assert _git(work, "log", "-1", "--format=%an <%ae>|%cn") == (
        "Holon Review Loop Agent <review-loop-agent@holon-agentic-coder.com>|Holon Review Loop Agent"
    )


def test_nothing_pending_commits_nothing(tmp_path, tmp_path_factory):
    _, work = flow_worktree_copy(tmp_path_factory, tmp_path)
    head = _git(work, "rev-parse", "HEAD")

    assert ledger_commit._commit_pending_ledger_rows(_context(work), 1) is None
    assert _git(work, "rev-parse", "HEAD") == head


@pytest.mark.parametrize(
    "case", ["dry_run", "detached", "other_branch", "protected", "container", "nested", "no_branch"]
)
def test_the_rows_stay_uncommitted_when_committing_is_not_allowed(tmp_path, tmp_path_factory, monkeypatch, case):
    origin, work = flow_worktree_copy(tmp_path_factory, tmp_path)
    before = _origin_tip(origin)
    repo_dir, kwargs = work, {}
    if case == "dry_run":
        kwargs["dry_run"] = True
    elif case == "detached":
        _git(work, "checkout", "-q", "--detach")
    elif case == "other_branch":
        _git(work, "checkout", "-q", "-b", "elsewhere")
    elif case == "protected":
        _git(work, "checkout", "-q", "main")
        kwargs["branch"] = "main"
    elif case == "container":
        monkeypatch.setenv("HOLON_TARGET_WORKTREE", SANDBOX_WORKSPACE_MOUNT)
    elif case == "nested":
        repo_dir = work / "src"
    else:
        kwargs["branch"] = None
    head = _git(work, "rev-parse", "HEAD")
    append_row(work, {"execution_id": "E-row"})
    append_row(repo_dir, {"execution_id": "E-nested"})

    assert ledger_commit._commit_pending_ledger_rows(_context(repo_dir, **kwargs), 1) is None
    assert _git(work, "rev-parse", "HEAD") == head
    assert _origin_tip(origin) == before
    assert LEDGER in _git(work, "status", "--porcelain", "--", LEDGER)


def test_skip_push_commits_without_pushing(tmp_path, tmp_path_factory):
    origin, work = flow_worktree_copy(tmp_path_factory, tmp_path)
    before = _origin_tip(origin)
    append_row(work, {"execution_id": "E-row"})

    commit = ledger_commit._commit_pending_ledger_rows(_context(work, skip_push=True), 1)

    assert commit is not None and not commit.pushed
    assert changed_paths(work) == [LEDGER]
    assert _origin_tip(origin) == before


def test_a_rejected_push_keeps_the_local_commit_and_never_raises(tmp_path, tmp_path_factory):
    origin, work = flow_worktree_copy(tmp_path_factory, tmp_path)
    stream = (
        f"commit refs/heads/{EXEC_TARGET}\n"
        "committer Holon Test <test@holon.invalid> 1700000000 +0000\n"
        "data 13\norigin moved\n"
        f"from refs/heads/{EXEC_TARGET}^0\n"
        "M 100644 inline src/app.py\n"
        "data 6\nx = 3\n\n"
    ).encode()
    subprocess.run(["git", "fast-import", "--quiet"], cwd=origin, input=stream, check=True, capture_output=True)
    moved = _origin_tip(origin)
    append_row(work, {"execution_id": "E-row"})
    context = _context(work)

    commit = ledger_commit._commit_pending_ledger_rows(context, 1)

    assert commit is not None and not commit.pushed
    assert _git(work, "rev-parse", "HEAD") == commit.sha
    assert _origin_tip(origin) == moved
    assert any("pushing ledger commit" in line and "Warning" in line for line in context.logs)


def test_a_failing_git_command_is_logged_not_raised(tmp_path, tmp_path_factory, monkeypatch):
    _, work = flow_worktree_copy(tmp_path_factory, tmp_path)
    append_row(work, {"execution_id": "E-row"})
    context = _context(work)

    def broken(*_args, **_kwargs):
        raise RuntimeError("git exploded")

    monkeypatch.setattr(ledger_commit, "_commit_ledger_rows", broken)

    assert ledger_commit._commit_pending_ledger_rows(context, 1) is None
    assert any("committing ledger rows failed: git exploded" in line for line in context.logs)


def test_the_reviewed_head_follows_a_ledger_commit_made_on_it():
    target = ReviewTarget(raw=EXEC_TARGET, head_branch=EXEC_TARGET, head_sha="a" * 40)
    on_head = ledger_commit._LedgerCommit(sha="b" * 40, parent="a" * 40, pushed=True)
    elsewhere = ledger_commit._LedgerCommit(sha="c" * 40, parent="d" * 40, pushed=True)

    moved, prev = ledger_commit._advance_past_ledger_commit(target, "a" * 40, on_head)
    assert (moved.head_sha, prev) == ("b" * 40, "b" * 40)
    assert ledger_commit._advance_past_ledger_commit(target, "e" * 40, on_head)[1] == "e" * 40
    assert ledger_commit._advance_past_ledger_commit(target, "a" * 40, elsewhere) == (target, "a" * 40)
    assert ledger_commit._advance_past_ledger_commit(target, "a" * 40, None) == (target, "a" * 40)
    assert ledger_commit._advance_past_ledger_commit(None, "", on_head) == (None, "")


def test_a_ledger_commit_on_an_unpushed_code_commit_is_never_pushed(tmp_path, tmp_path_factory):
    origin, work = flow_worktree_copy(tmp_path_factory, tmp_path)
    before = _origin_tip(origin)
    (work / "src" / "app.py").write_text("x = 9\n", encoding="utf-8")
    _git(work, "commit", "-q", "-am", "resolver commit the host refused to push")
    append_row(work, {"execution_id": "E-row"})
    context = _context(work)

    commit = ledger_commit._commit_pending_ledger_rows(context, 1)

    assert commit is not None and not commit.pushed
    assert _origin_tip(origin) == before
    assert any("refused: it sits on unpushed non-ledger commits" in line for line in context.logs)


def test_a_ledger_commit_on_an_earlier_unpushed_ledger_commit_pushes_both(tmp_path, tmp_path_factory):
    origin, work = flow_worktree_copy(tmp_path_factory, tmp_path)
    append_row(work, {"execution_id": "E-early"})
    _git(work, "commit", "-q", "-am", "earlier ledger rows")
    append_row(work, {"execution_id": "E-row"})

    commit = ledger_commit._commit_pending_ledger_rows(_context(work), 1)

    assert commit is not None and commit.pushed
    assert _origin_tip(origin) == commit.sha


def test_stage_4_exit_reattaches_a_detached_head_before_committing_the_rows(tmp_path, tmp_path_factory):
    origin, work = flow_worktree_copy(tmp_path_factory, tmp_path)
    append_row(work, {"execution_id": "E-row"})
    _git(work, "checkout", "-q", "--detach")
    context = _context(work)

    ledger_commit._close_review_stage(context, [EXEC_TARGET], restore_branch)

    assert _git(work, "rev-parse", "--abbrev-ref", "HEAD") == EXEC_TARGET
    assert _git(work, "status", "--porcelain") == ""
    assert [row["execution_id"] for row in added_rows(work)] == ["E-row"]
    assert _origin_tip(origin) == _git(work, "rev-parse", "HEAD")
