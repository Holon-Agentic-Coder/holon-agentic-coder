from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor.flow import FlowContext, StageStatus
from sandbox_executor.flow.checkpoint import load_checkpoint
from sandbox_executor.flow.execution_recovery import (
    RECOVERY_REF_PREFIX,
    keep_rejected_executions,
    republish_recorded_execution,
)
from sandbox_executor.flow.host_remote import HostRemoteError, push_commit
from sandbox_executor.flow.stages.execute import ExecuteStage
from sandbox_executor.git import run_git as real_run_git

from tests.flow.helpers import _append_jsonl, _configure_identity, _git
from tests.flow.sleep_patch import scoped_sleep_patch

EXECUTE = "sandbox_executor.flow.stages.execute"
HOST_REMOTE = "sandbox_executor.flow.host_remote"
PLAN_PREFIX = "I-1-x/P-2-CLA-CO5.5"
PLAN_BRANCH = f"{PLAN_PREFIX}/_"
EXEC_ID = "E-3-CLA-CO5.5"
EXEC_BRANCH = f"{PLAN_PREFIX}/{EXEC_ID}/_"
WORKFLOW_REFUSAL = (
    "refusing to allow an OAuth App to create or update workflow `.github/workflows/ci.yml` without `workflow` scope"
)


def _install_hook(origin: Path, body: str) -> Path:
    hook = origin / "hooks" / "pre-receive"
    hook.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    # why: the suite's global git config points core.hooksPath at an empty directory; the remote opts back in.
    _git(origin, "config", "core.hooksPath", str(hook.parent))
    return hook


def _remove_hook(origin: Path) -> None:
    hook = origin / "hooks" / "pre-receive"
    if hook.exists():
        hook.unlink()


def _seed_plan_branch(host: Path) -> None:
    _configure_identity(host)
    _git(host, "checkout", "-q", "-B", PLAN_BRANCH, "main")
    (host / "plans").mkdir(exist_ok=True)
    (host / "plans" / "P-2-CLA-CO5.5.md").write_text("# Plan\n", encoding="utf-8")
    _git(host, "add", "-A")
    _git(host, "commit", "-q", "-m", "plan: P-2-CLA-CO5.5")
    _git(host, "push", "-q", "origin", PLAN_BRANCH)
    _git(host, "checkout", "-q", "main")


def _commit_execution_branch(host: Path, branch: str = EXEC_BRANCH, with_summary: bool = True) -> str:
    _configure_identity(host)
    _git(host, "checkout", "-q", "-B", branch, PLAN_BRANCH)
    (host / "src").mkdir(exist_ok=True)
    (host / "src" / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    if with_summary:
        exec_id = branch.removesuffix("/_").rsplit("/", 1)[-1]
        _append_jsonl(
            host / "holon-knowledge" / "ledger" / "executions.jsonl",
            {
                "entry_type": "summary",
                "execution_id": exec_id,
                "plan_branch": PLAN_BRANCH,
                "status": "success",
                "summary": "Implemented feature",
                "ledger_revision": 1,
            },
        )
    _git(host, "add", "-A")
    _git(host, "commit", "-q", "-m", f"execute: {branch}")
    tip = _git(host, "rev-parse", "HEAD")
    _git(host, "checkout", "-q", "main")
    return tip


def _make_context(host: Path, checkpoint_path: Path | None = None) -> FlowContext:
    return FlowContext(
        repo_dir=str(host),
        plan_branch=PLAN_BRANCH,
        intent_data={"slug": "x", "target_branch": "main"},
        agent="claude-agent",
        model="claude-opus-5-5",
        checkpoint_path=str(checkpoint_path) if checkpoint_path is not None else None,
    )


def _run_stage3_real_git(context: FlowContext, dispatch, resume_partial: bool = False):
    with (
        patch(f"{EXECUTE}.token_scope_failure", return_value=None),
        patch(f"{EXECUTE}.at_stage_boundary", return_value=None),
        patch(f"{EXECUTE}._plan_budget_decision", return_value=None),
        patch(f"{EXECUTE}.admission_stage_failure", return_value=None),
        patch(f"{EXECUTE}.plan_refresh_stage_failure", return_value=None),
        patch(f"{EXECUTE}.touch_set_violation_result", return_value=None),
        patch(f"{EXECUTE}._dispatch_role", side_effect=dispatch),
    ):
        return ExecuteStage(context, resume_partial=resume_partial).run()


def _remote_execution_branches(origin: Path) -> list[str]:
    out = _git(origin, "for-each-ref", "--format=%(refname:short)", f"refs/heads/{PLAN_PREFIX}/")
    return [line for line in out.splitlines() if line.startswith(f"{PLAN_PREFIX}/E-")]


def test_fake_remote_rejects_once_then_accepts_on_retry(flow_repos, monkeypatch):
    host, origin = flow_repos
    _seed_plan_branch(host)
    tip = _commit_execution_branch(host)
    push_calls = 0

    def flaky_run_git(argv, **kwargs):
        nonlocal push_calls
        if argv and argv[0] == "push":
            push_calls += 1
            if push_calls == 1:
                return subprocess.CompletedProcess(
                    ["git", *argv], 1, "", f" ! [remote rejected] {EXEC_BRANCH} -> {EXEC_BRANCH} (failed)"
                )
        return real_run_git(argv, **kwargs)

    monkeypatch.setattr(f"{HOST_REMOTE}.run_git", flaky_run_git)
    logs: list[str] = []
    with scoped_sleep_patch(HOST_REMOTE) as sleep_mock:
        push_commit(str(host), f"refs/heads/{EXEC_BRANCH}", EXEC_BRANCH, log=logs.append)

    assert push_calls == 2
    assert _git(origin, "rev-parse", f"refs/heads/{EXEC_BRANCH}") == tip
    assert [call.args[0] for call in sleep_mock.call_args_list] == [1.0]
    assert len(logs) == 1
    assert "attempt 1 of 3" in logs[0] and "(failed)" in logs[0]


def test_always_refused_push_halts_with_branch_recorded_and_resume_republishes_without_dispatch(flow_repos, tmp_path):
    host, origin = flow_repos
    _seed_plan_branch(host)
    _install_hook(origin, "echo 'transient server error (failed)' >&2\nexit 1")
    checkpoint_path = tmp_path / "checkpoint.json"
    context = _make_context(host, checkpoint_path=checkpoint_path)
    committed_tip = ""

    def fake_dispatch(_ctx, _role, _args):
        nonlocal committed_tip
        committed_tip = _commit_execution_branch(host)
        return 0

    with scoped_sleep_patch(HOST_REMOTE) as sleep_mock:
        failed_result = _run_stage3_real_git(context, fake_dispatch)

    assert failed_result.status == StageStatus.FAILED
    assert failed_result.payload["execution_branch"] == EXEC_BRANCH
    assert [call.args[0] for call in sleep_mock.call_args_list] == [1.0, 2.0]
    assert _git(host, "rev-parse", f"{RECOVERY_REF_PREFIX}{EXEC_ID}") == committed_tip
    assert any("attempt 1 of 3" in line for line in context.logs)
    assert any("attempt 2 of 3" in line for line in context.logs)
    assert any("attempt 3 of 3" in line for line in context.logs)

    saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert saved["context"]["execution_branch"] == EXEC_BRANCH
    loaded = load_checkpoint(str(checkpoint_path))
    assert loaded.context.execution_branch == EXEC_BRANCH

    _remove_hook(origin)
    resumed_context = loaded.context
    resumed_context.checkpoint_path = str(checkpoint_path)

    def forbidden_dispatch(*_args, **_kwargs):
        raise AssertionError("resume after refused push must not dispatch the executor again")

    with scoped_sleep_patch(HOST_REMOTE) as resume_sleep:
        resumed_result = _run_stage3_real_git(resumed_context, forbidden_dispatch, resume_partial=True)

    assert resumed_result.status == StageStatus.SUCCESS
    assert resumed_result.payload["execution_branch"] == EXEC_BRANCH
    assert resume_sleep.call_args_list == []
    assert _remote_execution_branches(origin) == [EXEC_BRANCH]
    assert _git(origin, "rev-parse", f"refs/heads/{EXEC_BRANCH}") == committed_tip


def test_resume_republishes_from_recovery_ref_when_local_branch_is_gone(flow_repos, tmp_path):
    host, origin = flow_repos
    _seed_plan_branch(host)
    _install_hook(origin, "echo 'transient server error (failed)' >&2\nexit 1")
    checkpoint_path = tmp_path / "checkpoint.json"
    context = _make_context(host, checkpoint_path=checkpoint_path)
    committed_tip = ""

    def fake_dispatch(_ctx, _role, _args):
        nonlocal committed_tip
        committed_tip = _commit_execution_branch(host)
        return 0

    with scoped_sleep_patch(HOST_REMOTE):
        failed_result = _run_stage3_real_git(context, fake_dispatch)
    assert failed_result.status == StageStatus.FAILED

    _git(host, "branch", "-q", "-D", EXEC_BRANCH)
    assert _git(host, "branch", "--list", EXEC_BRANCH) == ""
    _remove_hook(origin)

    resumed_context = load_checkpoint(str(checkpoint_path)).context
    resumed_context.checkpoint_path = str(checkpoint_path)

    def forbidden_dispatch(*_args, **_kwargs):
        raise AssertionError("resume from recovery ref must not dispatch the executor again")

    with scoped_sleep_patch(HOST_REMOTE):
        resumed_result = _run_stage3_real_git(resumed_context, forbidden_dispatch, resume_partial=True)

    assert resumed_result.status == StageStatus.SUCCESS
    assert resumed_result.payload["execution_branch"] == EXEC_BRANCH
    assert _remote_execution_branches(origin) == [EXEC_BRANCH]
    assert _git(origin, "rev-parse", f"refs/heads/{EXEC_BRANCH}") == committed_tip
    assert _git(host, "rev-parse", f"refs/heads/{EXEC_BRANCH}") == committed_tip


def test_reuse_skips_branch_already_on_origin(flow_repos):
    host, _origin = flow_repos
    _seed_plan_branch(host)
    _commit_execution_branch(host)
    _git(host, "push", "-q", "origin", EXEC_BRANCH)
    context = _make_context(host)
    context.execution_branch = EXEC_BRANCH

    branch, error = republish_recorded_execution(context, PLAN_PREFIX, set())

    assert (branch, error) == (None, None)


def test_reuse_never_pushes_recorded_branch_without_summary_row(flow_repos):
    host, origin = flow_repos
    _seed_plan_branch(host)
    _commit_execution_branch(host, with_summary=False)
    keep_rejected_executions(str(host), PLAN_PREFIX, set())
    _git(host, "branch", "-q", "-D", EXEC_BRANCH)
    context = _make_context(host)
    context.execution_branch = EXEC_BRANCH

    branch, error = republish_recorded_execution(context, PLAN_PREFIX, set())

    assert (branch, error) == (None, None)
    assert _remote_execution_branches(origin) == []
    assert _git(host, "branch", "--list", EXEC_BRANCH) == ""


def test_workflow_scope_refusal_is_not_retried(flow_repos):
    host, origin = flow_repos
    _seed_plan_branch(host)
    _commit_execution_branch(host)
    _install_hook(origin, f"echo '{WORKFLOW_REFUSAL}' >&2\nexit 1")
    logs: list[str] = []

    with scoped_sleep_patch(HOST_REMOTE) as sleep_mock, pytest.raises(HostRemoteError):
        push_commit(str(host), f"refs/heads/{EXEC_BRANCH}", EXEC_BRANCH, log=logs.append)

    assert sleep_mock.call_args_list == []
    assert len(logs) == 1
    assert "attempt 1 of 3" in logs[0] and "workflow" in logs[0].lower()


def test_real_bare_remote_rejects_first_push_and_accepts_the_retry(flow_repos):
    host, origin = flow_repos
    _seed_plan_branch(host)
    marker = origin / "hooks" / "rejected-once"
    _install_hook(
        origin,
        f'if [ ! -f "{marker}" ]; then\n'
        f'  touch "{marker}"\n'
        '  echo "transient ref-update failure" >&2\n'
        "  exit 1\n"
        "fi\n"
        "exit 0",
    )

    first_branch = f"{PLAN_PREFIX}/E-10-CLA-CO5.5/_"
    first_tip = _commit_execution_branch(host, branch=first_branch)
    push_logs: list[str] = []
    with scoped_sleep_patch(HOST_REMOTE) as sleep_mock:
        push_commit(str(host), f"refs/heads/{first_branch}", first_branch, log=push_logs.append)

    assert _git(origin, "rev-parse", f"refs/heads/{first_branch}") == first_tip
    assert [call.args[0] for call in sleep_mock.call_args_list] == [1.0]
    assert len(push_logs) == 1
    assert "attempt 1 of 3" in push_logs[0] and "transient ref-update failure" in push_logs[0]

    marker.unlink()
    second_tip = _commit_execution_branch(host, branch=EXEC_BRANCH)
    context = _make_context(host)
    context.execution_branch = EXEC_BRANCH
    with scoped_sleep_patch(HOST_REMOTE) as republish_sleep:
        branch, error = republish_recorded_execution(context, PLAN_PREFIX, {first_branch})

    assert (branch, error) == (EXEC_BRANCH, None)
    assert _git(origin, "rev-parse", f"refs/heads/{EXEC_BRANCH}") == second_tip
    assert [call.args[0] for call in republish_sleep.call_args_list] == [1.0]
    assert any("attempt 1 of 3" in line and "transient ref-update failure" in line for line in context.logs)
