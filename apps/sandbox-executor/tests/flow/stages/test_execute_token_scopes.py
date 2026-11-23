"""Stage 3 token scope pre-flight: a workflow-touching plan with a token lacking ``workflow`` stops before dispatch."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.flow import StageStatus
from sandbox_executor.flow.execution_recovery import RecoveredExecution
from sandbox_executor.flow.github.token_scopes import REFRESH_COMMAND, TokenScopeReport, scope_report
from sandbox_executor.flow.host_remote import HostRemoteError
from sandbox_executor.flow.stages.token_scope_guard import declared_paths, token_scope_failure
from sandbox_executor.touch_set import TouchSet, TouchSetEntry

from tests.flow.stages.test_execute import EXEC_BRANCH, EXEC_ID, run_stage3, stage3_context

SENTINEL = "ghp_TESTSENTINEL"
SCOPE_REPORT = "sandbox_executor.flow.github.token_scopes.scope_report"
TOUCH_SET_READER = "sandbox_executor.flow.stages.token_scope_guard.read_plan_touch_set"
WORKFLOW = ".github/workflows/ci.yml"


def workflow_refusal(*_args: object) -> None:
    raise HostRemoteError(
        "push", EXEC_BRANCH, "origin", "refusing to allow an OAuth App to update workflow without `workflow` scope"
    )


def _touch_set(*paths: str) -> TouchSet:
    return TouchSet(tuple(TouchSetEntry(path) for path in paths))


def _run(tmp_path, report: TokenScopeReport, touch_set: TouchSet | None, scope: list[str] | None = None):
    intent = {"slug": "x", **({"scope": scope} if scope is not None else {})}
    ctx = stage3_context(tmp_path, intent_data=intent)
    report_fn = MagicMock(return_value=report)
    with patch(TOUCH_SET_READER, return_value=touch_set), patch(SCOPE_REPORT, report_fn):
        result, mocks = run_stage3(ctx)
    return ctx, result, mocks, report_fn


@pytest.fixture(autouse=True)
def _sentinel_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", SENTINEL)


def test_a_token_lacking_workflow_fails_a_workflow_touch_set_before_any_container_starts(tmp_path):
    report = TokenScopeReport("classic", "GITHUB_TOKEN", ("repo",))
    ctx, result, mocks, _ = _run(tmp_path, report, _touch_set(WORKFLOW, "src/app.py"))
    assert result.status == StageStatus.FAILED
    assert result.error is not None and REFRESH_COMMAND in result.error and WORKFLOW in result.error
    assert result.payload == {"dispatched": False, "token_scope": "missing", "workflow_paths": [WORKFLOW]}
    for name in ("_dispatch_role", "admission_stage_failure", "plan_refresh_stage_failure", "_snapshot_child_branches"):
        mocks[name].assert_not_called()
    assert ctx.stage_results["execute"] is result
    assert all(SENTINEL not in line for line in ctx.logs) and SENTINEL not in result.error


def test_the_intent_scope_alone_triggers_the_pre_flight(tmp_path):
    report = TokenScopeReport("oauth", "gh", ("repo", "read:org"))
    _, result, mocks, _ = _run(tmp_path, report, None, scope=["apps/x.py", WORKFLOW])
    assert result.status == StageStatus.FAILED and REFRESH_COMMAND in str(result.error)
    mocks["_dispatch_role"].assert_not_called()


def test_the_same_token_passes_a_touch_set_without_workflow_files_without_a_request(tmp_path):
    report = TokenScopeReport("classic", "GITHUB_TOKEN", ("repo",))
    _, result, mocks, report_fn = _run(tmp_path, report, _touch_set("src/app.py", ".github/CODEOWNERS"))
    assert result.status == StageStatus.SUCCESS
    report_fn.assert_not_called()
    mocks["_dispatch_role"].assert_called_once()


def test_a_token_with_the_workflow_scope_passes_a_workflow_touch_set(tmp_path):
    report = TokenScopeReport("classic", "GITHUB_TOKEN", ("repo", "workflow"))
    ctx, result, mocks, report_fn = _run(tmp_path, report, _touch_set(WORKFLOW))
    assert result.status == StageStatus.SUCCESS
    report_fn.assert_called_once()
    mocks["_dispatch_role"].assert_called_once()
    assert any("scopes=repo,workflow" in line for line in ctx.logs)


def test_an_unverifiable_fine_grained_token_passes_with_a_note(tmp_path):
    report = TokenScopeReport("fine-grained", "GITHUB_TOKEN", None, "fine-grained tokens carry no scopes header")
    ctx, result, mocks, _ = _run(tmp_path, report, _touch_set(".github/**"))
    assert result.status == StageStatus.SUCCESS
    mocks["_dispatch_role"].assert_called_once()
    assert any("could not be verified" in line for line in ctx.logs)


def test_the_real_report_over_a_failed_request_leaks_no_token(tmp_path, caplog):
    """The default report path with a fetcher that fails: unverifiable, logged without the token."""
    caplog.set_level(logging.DEBUG)
    ctx = stage3_context(tmp_path, intent_data={"slug": "x", "scope": [WORKFLOW]})

    def failing_fetch(url, headers, timeout):
        raise OSError(f"blocked {headers['Authorization']}")

    with patch(TOUCH_SET_READER, return_value=None):
        result = token_scope_failure(ctx, "2026-11-23T00:00:00Z", lambda: scope_report(fetch=failing_fetch))
    assert result is None
    assert all(SENTINEL not in line for line in ctx.logs) and SENTINEL not in caplog.text
    assert any("could not be verified" in line for line in ctx.logs)


def test_declared_paths_tolerates_an_unreadable_touch_set(tmp_path):
    ctx = stage3_context(tmp_path, intent_data={"slug": "x", "scope": ["a.py", 3]})
    with patch(TOUCH_SET_READER, side_effect=RuntimeError("no git")):
        assert declared_paths(ctx) == ["a.py"]


def test_a_rejected_push_names_the_recovery_ref_in_the_stage_error(tmp_path):
    """Stage level: a refused host push fails with the recovery ref the commits were kept on."""
    kept = RecoveredExecution(EXEC_BRANCH, f"refs/holon/recovery/{EXEC_ID}", "a" * 40, None)
    ctx = stage3_context(tmp_path)
    with (
        patch(TOUCH_SET_READER, return_value=None),
        patch("sandbox_executor.flow.execution_recovery.keep_rejected_executions", return_value=[kept]) as keep,
    ):
        result, _ = run_stage3(ctx, publish_new_child_branches={"side_effect": workflow_refusal})
    assert result.status == StageStatus.FAILED
    assert "refs/holon/recovery/" in str(result.error) and REFRESH_COMMAND in str(result.error)
    assert result.payload["recovery_refs"] == [f"refs/holon/recovery/{EXEC_ID}"]
    assert result.payload["recovery_pushed"] is False
    keep.assert_called_once()
