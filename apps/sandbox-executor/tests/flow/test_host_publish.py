"""The standalone-command host helpers, and the host push of a failed planner's or executor's branch."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor.cli.commands._host_publish import (
    prepare_worktree,
    push_checked_out_branch,
    slug_of_branch,
)
from sandbox_executor.flow import StageStatus, run_execute_stage, run_intent_stage, run_plan_stage

from tests.flow.helpers import FakeRoleAgents, _configure_identity, _dispatch_context, _git

SECRET = "s3cretTOKENvalue99"
TOKENISED_URL = f"https://ci-user:{SECRET}@127.0.0.1:9/acme/widgets.git"


def _commit_on(host: Path, branch: str, name: str) -> str:
    _configure_identity(host)
    _git(host, "checkout", "-q", "-B", branch, "main")
    (host / f"{name}.txt").write_text(name, encoding="utf-8")
    _git(host, "add", "-A")
    _git(host, "commit", "-q", "-m", name)
    return _git(host, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("branch", "slug"),
    [
        ("I-1794391245-worktree/_", "worktree"),
        ("I-1794391245-worktree/P-1-a-b/E-2-a-b/_", "worktree"),
        ("I-1-parent--1-add-the-parser/_", "parent--1-add-the-parser"),
        ("feature/x", "feature"),
        ("main", "main"),
    ],
)
def test_slug_of_branch(branch, slug):
    assert slug_of_branch(branch) == slug


def test_push_checked_out_branch_pushes_an_intent_branch_and_returns_its_name(flow_repos):
    host, origin = flow_repos
    tip = _commit_on(host, "I-1-demo/_", "work")

    assert push_checked_out_branch(str(host)) == "I-1-demo/_"
    assert _git(origin, "rev-parse", "refs/heads/I-1-demo/_") == tip


def test_push_checked_out_branch_refuses_a_detached_head(flow_repos):
    host, origin = flow_repos
    _git(host, "checkout", "-q", "--detach", "main")

    with pytest.raises(RuntimeError, match="no branch is checked out"):
        push_checked_out_branch(str(host))
    assert "I-" not in _git(origin, "for-each-ref")


def test_push_checked_out_branch_refuses_a_branch_that_is_not_an_intent_branch(flow_repos):
    host, origin = flow_repos
    _commit_on(host, "feature/x", "work")

    with pytest.raises(RuntimeError, match="not an I-/P-/E- branch"):
        push_checked_out_branch(str(host))
    assert "feature/x" not in _git(origin, "for-each-ref")


def test_push_checked_out_branch_never_forces_a_non_fast_forward(flow_repos):
    host, origin = flow_repos
    tip = _commit_on(host, "I-1-demo/_", "first")
    push_checked_out_branch(str(host))
    _git(host, "reset", "-q", "--hard", "main")
    _commit_on(host, "I-1-demo/_", "diverged")

    with pytest.raises(RuntimeError, match="host push of 'I-1-demo/_' through remote 'origin' failed"):
        push_checked_out_branch(str(host))
    assert _git(origin, "rev-parse", "refs/heads/I-1-demo/_") == tip


def test_push_checked_out_branch_error_carries_no_credentials(flow_repos):
    host, _origin = flow_repos
    _commit_on(host, "I-1-demo/_", "work")
    _git(host, "config", "remote.origin.pushurl", TOKENISED_URL)

    with patch.dict(os.environ, {"GIT_TERMINAL_PROMPT": "0"}), pytest.raises(RuntimeError) as raised:
        push_checked_out_branch(str(host))

    assert "host push of 'I-1-demo/_' through remote 'origin' failed" in str(raised.value)
    assert SECRET not in str(raised.value)


def test_prepare_worktree_names_a_failed_fetch(flow_repos):
    host, _origin = flow_repos

    with pytest.raises(RuntimeError, match="host fetch of 'no-such-base' through remote 'origin' failed"):
        prepare_worktree("no-such-base", str(host), "demo")


def test_prepare_worktree_provisions_the_flow_sibling_of_a_default_branch_worktree(flow_repos):
    host, _origin = flow_repos

    worktree = prepare_worktree("main", str(host), "demo")

    assert Path(worktree).name == "flow-demo"
    assert _git(Path(worktree), "symbolic-ref", "--short", "HEAD") == "flow-demo"
    assert _git(host, "symbolic-ref", "--short", "HEAD") == "main"


def test_prepare_worktree_keeps_a_worktree_that_is_not_on_the_default_branch(flow_repos):
    host, _origin = flow_repos
    _git(host, "checkout", "-q", "-b", "flow-own")

    assert prepare_worktree("main", str(host), "demo") == str(host)


class _FailingPlanner(FakeRoleAgents):
    """A planner that commits its plan branch, then exits non-zero (as a budget-salvage failure does)."""

    def _plan(self, work, args, kwargs):
        super()._plan(work, args, kwargs)
        return 3


def test_a_failed_planner_exit_still_publishes_the_branch_it_committed(flow_repos, tmp_path):
    host, origin = flow_repos
    ctx = _dispatch_context(host, _FailingPlanner(tmp_path, origin))
    assert run_intent_stage(ctx).status == StageStatus.SUCCESS

    result = run_plan_stage(ctx)

    assert result.status == StageStatus.FAILED
    assert "planner agent exited with code 3" in (result.error or "")
    assert "/P-" in _git(origin, "for-each-ref", "refs/heads")


def test_a_failed_host_push_fails_the_plan_stage_with_the_named_error(flow_repos, tmp_path):
    host, origin = flow_repos
    ctx = _dispatch_context(host, FakeRoleAgents(tmp_path, origin))
    assert run_intent_stage(ctx).status == StageStatus.SUCCESS
    _git(host, "config", "remote.origin.pushurl", TOKENISED_URL)

    with patch.dict(os.environ, {"GIT_TERMINAL_PROMPT": "0"}):
        result = run_plan_stage(ctx)

    assert result.status == StageStatus.FAILED
    assert "host push of" in (result.error or "")
    assert SECRET not in (result.error or "")


def test_a_failed_host_push_fails_the_execute_stage_with_the_named_error(flow_repos, tmp_path):
    host, origin = flow_repos
    ctx = _dispatch_context(host, FakeRoleAgents(tmp_path, origin))
    assert run_intent_stage(ctx).status == StageStatus.SUCCESS
    assert run_plan_stage(ctx).status == StageStatus.SUCCESS
    _git(host, "config", "remote.origin.pushurl", TOKENISED_URL)
    with patch.dict(os.environ, {"GIT_TERMINAL_PROMPT": "0"}):
        result = run_execute_stage(ctx)

    assert result.status == StageStatus.FAILED
    assert "host push of" in (result.error or "")
    assert SECRET not in (result.error or "")
