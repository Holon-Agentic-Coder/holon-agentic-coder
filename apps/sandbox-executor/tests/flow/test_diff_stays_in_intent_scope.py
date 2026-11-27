from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sandbox_executor.flow import FlowContext
from sandbox_executor.flow.review import resolver
from sandbox_executor.flow.stage5 import (
    Stage5Steps,
    _check_stage5_readiness,
    _Stage5TouchSetError,
    run_stage5,
    standalone_context,
)
from sandbox_executor.flow.stages.touch_set_guard import TOUCH_SET_VIOLATION
from sandbox_executor.review_target import ReviewTarget

from tests.flow.helpers import _configure_identity, _git

INTENT_BRANCH = "I-1795734341-test-failure-record-diagnose-fix"
PLAN_ID = "P-1795734362-CLA-CO5.5"
PLAN_BRANCH = f"{INTENT_BRANCH}/{PLAN_ID}/_"
EXEC_BRANCH = f"{INTENT_BRANCH}/{PLAN_ID}/E-1795734755-CLA-CO5.5/_"

PR_360_INTENT_SCOPE = (
    "apps/sandbox-executor/src/sandbox_executor/entrypoint/test_failure_record.py",
    "apps/sandbox-executor/src/sandbox_executor/entrypoint/test_failure_classify.py",
    "apps/sandbox-executor/src/sandbox_executor/entrypoint/test_failure_loop.py",
    "apps/sandbox-executor/src/sandbox_executor/entrypoint/executor_test_run.py",
    "apps/sandbox-executor/src/sandbox_executor/flow/review/local_tests.py",
    "apps/sandbox-executor/tests/test_test_failure_record.py",
    "apps/sandbox-executor/tests/test_test_failure_classify.py",
    "apps/sandbox-executor/tests/test_test_failure_loop.py",
    "apps/sandbox-executor/tests/flow/test_review_local_tests.py",
    "apps/sandbox-executor/tests/test_executor_test_run.py",
)

PR_360_OUT_OF_SCOPE = (
    "apps/sandbox-executor/src/sandbox_executor/entrypoint/executor/outcome.py",
    "apps/sandbox-executor/src/sandbox_executor/entrypoint/resolver_test_failure.py",
    "apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py",
    "apps/sandbox-executor/src/sandbox_executor/entrypoint/test_measurement.py",
    "apps/sandbox-executor/src/sandbox_executor/flow/conflict_resolver_tiers.py",
    "apps/sandbox-executor/src/sandbox_executor/flow/review/local_test_finding.py",
    "apps/sandbox-executor/src/sandbox_executor/flow/review/loop.py",
    "apps/sandbox-executor/src/sandbox_executor/model_tiers.py",
    "apps/sandbox-executor/tests/conftest.py",
    "apps/sandbox-executor/tests/flow/test_conflict_resolver_tiers.py",
    "apps/sandbox-executor/tests/flow/test_local_test_finding.py",
    "apps/sandbox-executor/tests/flow/test_package_exports.py",
    "apps/sandbox-executor/tests/test_executor_outcome.py",
    "apps/sandbox-executor/tests/test_resolver_test_failure.py",
    "apps/sandbox-executor/tests/test_review_local_test_failure.py",
    "apps/sandbox-executor/tests/test_uv_lock.py",
)


def _commit_files(repo: Path, files: dict[str, str], message: str) -> str:
    for rel, text in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


def _append_plan_row(repo: Path, row: dict[str, object], message: str) -> str:
    ledger = repo / "holon-knowledge" / "ledger" / "plans.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    _git(repo, "add", "holon-knowledge/ledger/plans.jsonl")
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


def _init_flow_repo(tmp_path: Path, touch_paths: tuple[str, ...]) -> tuple[Path, str]:
    _git(tmp_path, "init", "-q", "-b", "main")
    _configure_identity(tmp_path)
    _commit_files(tmp_path, {"README.md": "init\n"}, "initial")
    _git(tmp_path, "checkout", "-q", "-b", PLAN_BRANCH)
    plan_row = {
        "plan_id": PLAN_ID,
        "intent_branch": INTENT_BRANCH,
        "touch_set": [{"path": path} for path in touch_paths],
        "ledger_revision": 1,
    }
    _append_plan_row(tmp_path, plan_row, "record initial plan touch set")
    _git(tmp_path, "checkout", "-q", "-b", EXEC_BRANCH)
    base_sha = _commit_files(tmp_path, {touch_paths[0]: "x = 1\n"}, "in-scope execution commit")
    return tmp_path, base_sha


def _stage5_steps(calibrate_mock: MagicMock) -> Stage5Steps:
    return Stage5Steps(
        calibrate=calibrate_mock,
        reconcile=MagicMock(),
        promote=MagicMock(),
        commit_reconciliation=MagicMock(),
        commit_promoted=MagicMock(),
        warn_ledger_failure=MagicMock(),
    )


@pytest.fixture
def pushes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(resolver, "push_commit", lambda *args: calls.append(args))
    return calls


def test_resolver_refuses_out_of_scope_commit_and_pushes_in_scope_commit(tmp_path: Path, pushes: list):
    repo, base_sha = _init_flow_repo(tmp_path, ("src/allowed.py",))
    ctx = FlowContext(repo_dir=str(repo), plan_branch=PLAN_BRANCH, execution_branch=EXEC_BRANCH)

    in_scope_sha = _commit_files(repo, {"src/allowed.py": "x = 2\n"}, "resolver in-scope fix")
    target = ReviewTarget(raw=EXEC_BRANCH, head_branch=EXEC_BRANCH, head_sha=base_sha)
    assert resolver._push_resolver_head(ctx, target, in_scope_sha, None) == (True, None)
    assert len(pushes) == 1

    out_of_scope_sha = _commit_files(repo, {"src/forbidden.py": "y = 1\n"}, "resolver out-of-scope edit")
    next_target = ReviewTarget(raw=EXEC_BRANCH, head_branch=EXEC_BRANCH, head_sha=in_scope_sha)
    pushed, error = resolver._push_resolver_head(ctx, next_target, out_of_scope_sha, None)
    assert pushed is False
    assert error is not None
    assert TOUCH_SET_VIOLATION in error
    assert "src/forbidden.py" in error
    assert len(pushes) == 1

    skip_ctx = FlowContext(repo_dir=str(repo), plan_branch=PLAN_BRANCH, execution_branch=EXEC_BRANCH, skip_push=True)
    skip_pushed, skip_error = resolver._push_resolver_head(skip_ctx, next_target, out_of_scope_sha, None)
    assert skip_pushed is False
    assert skip_error is not None and TOUCH_SET_VIOLATION in skip_error


def test_resolver_refuses_out_of_scope_earlier_commit_in_multi_commit_range(tmp_path: Path, pushes: list):
    repo, base_sha = _init_flow_repo(tmp_path, ("src/allowed.py",))
    ctx = FlowContext(repo_dir=str(repo), plan_branch=PLAN_BRANCH, execution_branch=EXEC_BRANCH)

    _commit_files(repo, {"src/sneaky.py": "z = 1\n"}, "first resolver commit out of scope")
    head_sha = _commit_files(repo, {"src/allowed.py": "x = 3\n"}, "second resolver commit in scope")
    target = ReviewTarget(raw=EXEC_BRANCH, head_branch=EXEC_BRANCH, head_sha=base_sha)

    pushed, error = resolver._push_resolver_head(ctx, target, head_sha, None)
    assert pushed is False
    assert error is not None
    assert TOUCH_SET_VIOLATION in error
    assert "src/sneaky.py" in error
    assert pushes == []


def test_resolver_pass_injected_seam_refuses_out_of_scope_commit(tmp_path: Path):
    repo, base_sha = _init_flow_repo(tmp_path, ("src/allowed.py",))
    head_sha = _commit_files(repo, {"src/forbidden.py": "y = 1\n"}, "resolver out-of-scope edit")
    ctx = FlowContext(repo_dir=str(repo), plan_branch=PLAN_BRANCH, execution_branch=EXEC_BRANCH)
    target = ReviewTarget(raw=EXEC_BRANCH, head_branch=EXEC_BRANCH, head_sha=base_sha)
    resolver_input = resolver.ResolverInput(findings=())

    outcome = resolver._run_resolver_pass(
        ctx,
        target,
        1,
        resolver_input,
        lambda **_kw: resolver.ResolverOutcome(head_sha=head_sha, pushed=True),
    )
    assert outcome.pushed is False
    assert outcome.dispositions == ()
    assert outcome.error is not None and TOUCH_SET_VIOLATION in outcome.error and "src/forbidden.py" in outcome.error


def test_recorded_replan_widening_touch_set_allows_resolver_and_stage5(tmp_path: Path, pushes: list):
    repo, base_sha = _init_flow_repo(tmp_path, ("src/allowed.py",))
    ctx = FlowContext(repo_dir=str(repo), plan_branch=PLAN_BRANCH, execution_branch=EXEC_BRANCH)

    _commit_files(repo, {"src/widened.py": "w = 1\n"}, "resolver edits new file")
    replan_row = {
        "plan_id": PLAN_ID,
        "intent_branch": INTENT_BRANCH,
        "touch_set": [{"path": "src/allowed.py"}, {"path": "src/widened.py"}],
        "ledger_revision": 2,
    }
    head_sha = _append_plan_row(repo, replan_row, "append widening re-plan to plans ledger")
    target = ReviewTarget(raw=EXEC_BRANCH, head_branch=EXEC_BRANCH, head_sha=base_sha)

    assert resolver._push_resolver_head(ctx, target, head_sha, None) == (True, None)
    assert len(pushes) == 1

    readiness = _check_stage5_readiness(ctx)
    assert readiness.ready is True
    assert readiness.out_of_scope_files == ()

    calibrate_mock = MagicMock(return_value=MagicMock(calibrated_branch=None, to_dict=MagicMock(return_value={})))
    outcome = run_stage5(ctx, _stage5_steps(calibrate_mock), verdict="success", report_only=True)
    calibrate_mock.assert_called_once()
    assert outcome.readiness["ready"] is True


def test_stage5_refuses_calibration_and_lists_out_of_scope_files(tmp_path: Path):
    repo, _base_sha = _init_flow_repo(tmp_path, ("src/allowed.py",))
    _commit_files(repo, {"src/extra_a.py": "a = 1\n", "src/extra_b.py": "b = 2\n"}, "out of scope edits")
    ctx = standalone_context(EXEC_BRANCH, str(repo), no_commit=True)

    readiness = _check_stage5_readiness(ctx)
    assert readiness.ready is False
    assert readiness.reason == TOUCH_SET_VIOLATION
    assert set(readiness.out_of_scope_files) == {"src/extra_a.py", "src/extra_b.py"}
    formatted = "\n".join(readiness.format_lines())
    assert TOUCH_SET_VIOLATION in formatted
    assert "src/extra_a.py" in formatted and "src/extra_b.py" in formatted

    calibrate_mock = MagicMock()
    with pytest.raises(_Stage5TouchSetError) as exc_info:
        run_stage5(ctx, _stage5_steps(calibrate_mock), verdict="success", report_only=True)

    calibrate_mock.assert_not_called()
    assert exc_info.value.failure_reason == TOUCH_SET_VIOLATION
    assert set(exc_info.value.out_of_scope_files) == {"src/extra_a.py", "src/extra_b.py"}
    logged = "\n".join(ctx.logs)
    assert "src/extra_a.py" in logged and "src/extra_b.py" in logged


def test_pr_360_sixteen_out_of_scope_files_refused_by_resolver_and_stage5(tmp_path: Path, pushes: list):
    assert len(PR_360_INTENT_SCOPE) == 10
    assert len(PR_360_OUT_OF_SCOPE) == 16
    repo, base_sha = _init_flow_repo(tmp_path, PR_360_INTENT_SCOPE)

    in_scope_edits = {path: f"# why: fixture\nVALUE = {idx}\n" for idx, path in enumerate(PR_360_INTENT_SCOPE)}
    out_of_scope_edits = {path: f"# why: fixture\nEXTRA = {idx}\n" for idx, path in enumerate(PR_360_OUT_OF_SCOPE)}
    head_sha = _commit_files(repo, {**in_scope_edits, **out_of_scope_edits}, "reproduce PR 360 diff")

    ctx = FlowContext(
        repo_dir=str(repo),
        plan_branch=PLAN_BRANCH,
        execution_branch=EXEC_BRANCH,
        intent_data={"scope": list(PR_360_INTENT_SCOPE)},
    )
    target = ReviewTarget(raw=EXEC_BRANCH, head_branch=EXEC_BRANCH, head_sha=base_sha)

    pushed, error = resolver._push_resolver_head(ctx, target, head_sha, None)
    assert pushed is False
    assert error is not None and TOUCH_SET_VIOLATION in error
    for path in PR_360_OUT_OF_SCOPE:
        assert path in error
    assert pushes == []

    readiness = _check_stage5_readiness(ctx)
    assert readiness.ready is False
    assert readiness.reason == TOUCH_SET_VIOLATION
    assert set(readiness.out_of_scope_files) == set(PR_360_OUT_OF_SCOPE)
    for in_scope_path in PR_360_INTENT_SCOPE:
        assert in_scope_path not in readiness.out_of_scope_files
    formatted = "\n".join(readiness.format_lines())
    for path in PR_360_OUT_OF_SCOPE:
        assert path in formatted

    calibrate_mock = MagicMock()
    with pytest.raises(_Stage5TouchSetError) as exc_info:
        run_stage5(ctx, _stage5_steps(calibrate_mock), verdict="success", report_only=True)
    calibrate_mock.assert_not_called()
    assert set(exc_info.value.out_of_scope_files) == set(PR_360_OUT_OF_SCOPE)
    logged = "\n".join(ctx.logs)
    for path in PR_360_OUT_OF_SCOPE:
        assert path in logged
