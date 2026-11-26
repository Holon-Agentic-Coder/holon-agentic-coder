from __future__ import annotations

import functools
import math
import os
import sys
import time
from collections.abc import Callable
from typing import Any

from sandbox_executor.budgets import load_budgets
from sandbox_executor.entrypoint.conflict_resolver import run_agent_turn
from sandbox_executor.entrypoint.execution_args import ExecutionArgs
from sandbox_executor.entrypoint.test_failure_classify import BaseSnapshot, ShortSummaryRunner, run_failing_ids
from sandbox_executor.entrypoint.test_failure_loop import (
    FixRequest,
    LoopOutcome,
    LoopPorts,
    TestFailureLoop,
    fix_prompt,
    measure_selection,
    measurement_passed,
)
from sandbox_executor.entrypoint.test_failure_record import LEDGER_RELPATH, STAGE_3, record_test_failure
from sandbox_executor.entrypoint.test_measurement import TestMeasurement, run_test_task
from sandbox_executor.flow.conflict_resolver_tiers import tier_ladder

TEST_SOURCE_STAGE_3 = "stage_3"
_MAX_LOGGED_STRAY_PATHS = 5

FailureLoopFactory = Callable[[str, Any], "TestFailureLoop | None"]


def measurement_fields(measurement: TestMeasurement) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "test_pass_rate": measurement.rate,
        "tests_passed": measurement.passed,
        "tests_failed": measurement.failed,
        "tests_errored": measurement.errored,
        "test_exit_code": measurement.exit_code,
        "test_source": TEST_SOURCE_STAGE_3,
    }
    if measurement.run_error:
        fields["test_run_error"] = measurement.run_error
    return fields


def _changed_paths(repo: Any) -> set[str]:
    result = repo.run(["status", "--porcelain"], check=False)
    return set(result.stdout.splitlines()) if result.returncode == 0 else set()


def _git_output(repo: Any, *args: str) -> str:
    result = repo.run(list(args), check=False)
    return (result.stdout or "").strip() if result.returncode == 0 else ""


def _runner_and_model(argv: list[str]) -> tuple[str, str] | None:
    if os.path.basename(argv[0] if argv else "").startswith("executor") and len(argv) > 2:
        args = ExecutionArgs.from_argv(argv)
        return args.agent_name, args.model_name
    runner = os.environ.get("HOLON_AGENT", "").strip()
    model = os.environ.get("HOLON_MODEL", "").strip()
    return (runner, model) if runner and model else None


def _fix_turn(workspace: str, repo: Any, resolved: tuple[str, str], branch: str) -> Callable[[FixRequest], object]:
    runner, model = resolved

    def fix(request: FixRequest) -> object:
        verdict = run_agent_turn(workspace, runner, request.tier or model, fix_prompt(request))
        if _git_output(repo, "rev-parse", "--abbrev-ref", "HEAD") != branch:
            raise RuntimeError("fix turn moved HEAD branch")
        return verdict

    return fix


def _remaining_seconds() -> Callable[[], float]:
    budget = load_budgets(os.environ).stage_seconds("execute")
    started = time.monotonic()
    return lambda: math.inf if budget is None else budget - (time.monotonic() - started)


def default_failure_loop(workspace: str, repo: Any) -> TestFailureLoop:
    branch = _git_output(repo, "rev-parse", "--abbrev-ref", "HEAD")
    execution_id = next((part for part in branch.split("/") if part.startswith("E-")), "")
    resolved = _runner_and_model(sys.argv)
    ladder = tier_ladder(*resolved) if resolved else ()
    start_tier = resolved[1] if resolved and resolved[1] in ladder else (ladder[0] if ladder else None)
    ports = LoopPorts(
        run_selection=functools.partial(measure_selection, workspace),
        rerun_ids=functools.partial(run_failing_ids, cwd=workspace),
        base_rerun=BaseSnapshot(workspace, os.environ.get("HOLON_TARGET_BRANCH") or "main"),
        record=functools.partial(
            record_test_failure, os.path.join(workspace, *LEDGER_RELPATH), branch=branch, execution_id=execution_id
        ),
        stage=STAGE_3,
        head_sha=lambda: _git_output(repo, "rev-parse", "HEAD"),
        fix_turn=_fix_turn(workspace, repo, resolved, branch) if resolved else None,
        ladder=ladder,
        start_tier=start_tier,
        remaining_seconds=_remaining_seconds(),
    )
    return TestFailureLoop(ports)


def loop_fields(outcome: LoopOutcome) -> dict[str, Any]:
    return {
        "test_failure_classification": outcome.classification,
        "test_failure_status": outcome.status,
        "test_failure_blocked_reason": outcome.blocked_reason,
        "test_failure_rows": [row.get("action_id") for row in outcome.rows],
        "test_fix_tier": outcome.final_tier,
    }


def _run_failure_loop(
    workspace: str, repo: Any, first: tuple[TestMeasurement, str], factory: FailureLoopFactory
) -> LoopOutcome | None:
    if measurement_passed(first[0]):
        return None
    try:
        loop = factory(workspace, repo)
        outcome = loop.run(*first) if loop is not None else None
    except Exception as exc:
        print(f"Warning: the test failure loop could not run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None
    if outcome is not None and outcome.status in ("blocked", "exhausted"):
        print(f"Test failure {outcome.status}: {outcome.evidence_text}", file=sys.stderr)
    return outcome


def measure_executor_tests(
    workspace: str, repo: Any, exec_status: str, *, failure_loop: FailureLoopFactory | None = None
) -> dict[str, Any]:
    if exec_status != "success":
        return {}
    before = _changed_paths(repo)
    summary_runner = ShortSummaryRunner()
    measurement = run_test_task(workspace, runner=summary_runner)
    stray = sorted(_changed_paths(repo) - before)
    first = (measurement, summary_runner.last_output or measurement.output_excerpt)
    outcome = _run_failure_loop(workspace, repo, first, failure_loop or default_failure_loop)
    measurement = outcome.measurement if outcome is not None else measurement
    if measurement.rate is None:
        print(f"Test measurement: not measured ({measurement.run_error}); exit code {measurement.exit_code}")
    else:
        print(
            f"Test measurement: rate {measurement.rate:.3f} ({measurement.passed} passed, {measurement.failed} failed, "
            f"{measurement.errored} errored); exit code {measurement.exit_code}"
        )
    if stray:
        logged_paths = "; ".join(stray[:_MAX_LOGGED_STRAY_PATHS])
        print(f"Warning: the test run changed {len(stray)} path(s) in the worktree: {logged_paths}")
    return {**measurement_fields(measurement), **(loop_fields(outcome) if outcome is not None else {})}
