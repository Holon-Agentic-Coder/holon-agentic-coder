from __future__ import annotations

import stat
from pathlib import Path

import pytest
from sandbox_executor.flow.execution_recovery import (
    RECOVERY_BRANCH_PREFIX,
    RECOVERY_REF_PREFIX,
    keep_rejected_executions,
    rejected_push_error,
    rejected_push_recovery,
)
from sandbox_executor.flow.github.token_scopes import REFRESH_COMMAND
from sandbox_executor.flow.host_remote import HostRemoteError, publish_new_child_branches, remote_error

from tests.flow.helpers import _configure_identity, _git

PLAN_PREFIX = "I-1-x/P-2-CLA-CO5.5"
EXEC_ID = "E-3-CLA-CO5.5"
EXEC_BRANCH = f"{PLAN_PREFIX}/{EXEC_ID}/_"
WORKFLOW_REFUSAL = (
    "refusing to allow an OAuth App to create or update workflow `.github/workflows/ci.yml` without `workflow` scope"
)


def _install_hook(origin: Path, body: str) -> None:
    hook = origin / "hooks" / "pre-receive"
    hook.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    # why: the suite's global git config points core.hooksPath at an empty directory; the remote opts back in.
    _git(origin, "config", "core.hooksPath", str(hook.parent))


def _reject_everything(origin: Path) -> None:
    _install_hook(origin, f"echo '{WORKFLOW_REFUSAL}' >&2\nexit 1")


def _reject_plan_branches(origin: Path) -> None:
    _install_hook(
        origin,
        "while read old new ref; do\n"
        f'  case "$ref" in refs/heads/{PLAN_PREFIX}/*) echo "protected branch" >&2; exit 1;; esac\n'
        "done\nexit 0",
    )


def _commit_execution(host: Path) -> str:
    _configure_identity(host)
    _git(host, "checkout", "-q", "-B", EXEC_BRANCH, "main")
    (host / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (host / ".github" / "workflows" / "ci.yml").write_text("name: ci\n", encoding="utf-8")
    _git(host, "add", "-A")
    _git(host, "commit", "-q", "-m", "execution work")
    tip = _git(host, "rev-parse", "HEAD")
    _git(host, "checkout", "-q", "main")
    return tip


def test_a_workflow_scope_refusal_keeps_the_commits_on_a_local_recovery_ref(flow_repos):
    host, origin = flow_repos
    tip = _commit_execution(host)
    _reject_everything(origin)

    with pytest.raises(HostRemoteError):
        publish_new_child_branches(str(host), PLAN_PREFIX, "E", set())
    recovered = keep_rejected_executions(str(host), PLAN_PREFIX, set())

    assert len(recovered) == 1
    kept = recovered[0]
    assert kept.local_ref == f"{RECOVERY_REF_PREFIX}{EXEC_ID}" and kept.sha == tip
    # why: the content-based refusal refuses the recovery branch too
    assert kept.remote_branch is None
    assert _git(host, "rev-parse", kept.local_ref) == tip
    error = rejected_push_error(WORKFLOW_REFUSAL, recovered)
    assert kept.local_ref in error and REFRESH_COMMAND in error
    assert f"git push origin {kept.local_ref}:refs/heads/{EXEC_BRANCH}" in error


def test_a_branch_specific_refusal_also_reaches_the_remote_recovery_branch(flow_repos):
    host, origin = flow_repos
    tip = _commit_execution(host)
    _reject_plan_branches(origin)

    push_error = remote_error(publish_new_child_branches, str(host), PLAN_PREFIX, "E", set())
    assert push_error is not None
    recovery = rejected_push_recovery(str(host), PLAN_PREFIX, set(), push_error)

    assert recovery is not None
    error, payload = recovery
    remote_branch = f"{RECOVERY_BRANCH_PREFIX}{EXEC_ID}"
    assert payload == {"recovery_refs": [f"{RECOVERY_REF_PREFIX}{EXEC_ID}"], "recovery_pushed": True}
    assert _git(origin, "rev-parse", f"refs/heads/{remote_branch}") == tip
    assert f"pushed to origin {remote_branch}" in error and REFRESH_COMMAND not in error


def test_the_commits_stay_reachable_after_the_execution_branch_is_deleted(flow_repos):
    host, origin = flow_repos
    tip = _commit_execution(host)
    _reject_everything(origin)

    recovered = keep_rejected_executions(str(host), PLAN_PREFIX, set())
    _git(host, "branch", "-q", "-D", EXEC_BRANCH)

    assert _git(host, "cat-file", "-t", tip) == "commit"
    assert _git(host, "rev-parse", recovered[0].local_ref) == tip


def test_branches_in_the_before_snapshot_are_not_kept(flow_repos):
    host, origin = flow_repos
    _commit_execution(host)
    _reject_everything(origin)

    assert keep_rejected_executions(str(host), PLAN_PREFIX, {EXEC_BRANCH}) == []


def test_a_successful_push_needs_no_recovery(flow_repos):
    host, _origin = flow_repos
    assert rejected_push_recovery(str(host), PLAN_PREFIX, set(), None) is None
