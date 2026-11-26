from __future__ import annotations

import functools
import math
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sandbox_executor.budgets import StageTimer, load_budgets
from sandbox_executor.entrypoint.conflict_resolver import run_agent_turn
from sandbox_executor.entrypoint.test_failure_classify import BaseSnapshot, ShortSummaryRunner, run_failing_ids
from sandbox_executor.entrypoint.test_failure_loop import (
    FixRequest,
    FixTurn,
    LoopOutcome,
    LoopPorts,
    TestFailureLoop,
    fix_prompt,
    measure_selection,
    measurement_passed,
)
from sandbox_executor.entrypoint.test_failure_record import LEDGER_RELPATH, STAGE_3, record_test_failure
from sandbox_executor.entrypoint.test_measurement import TestMeasurement, run_test_task
from sandbox_executor.model_tiers import start_tier, tier_ladder

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


@dataclass(frozen=True)
class FixRunner:
    runner: str
    model: str
    stage_timer: StageTimer | None = None


class _StageClock:
    def __init__(self, timer: StageTimer | None) -> None:
        self._timer = timer or StageTimer("execute", load_budgets(os.environ).stage_seconds("execute"))
        self.longest_run = 0.0
        self.longest_fix = 0.0

    def remaining(self) -> float:
        left = self._timer.remaining()
        return math.inf if left is None else left - self.longest_run - self.longest_fix

    def timed_run(self, run: Callable[[], tuple[TestMeasurement, str]]) -> tuple[TestMeasurement, str]:
        started = time.monotonic()
        try:
            return run()
        finally:
            self.longest_run = max(self.longest_run, time.monotonic() - started)


def _fix_turn(workspace: str, repo: Any, fixer: FixRunner, branch: str, clock: _StageClock) -> FixTurn:
    def fix(request: FixRequest) -> object:
        started = time.monotonic()
        try:
            verdict = run_agent_turn(workspace, fixer.runner, request.tier or fixer.model, fix_prompt(request))
        finally:
            clock.longest_fix = max(clock.longest_fix, time.monotonic() - started)
        if verdict is not None:
            raise RuntimeError(verdict.reason or verdict.verdict)
        if _git_output(repo, "rev-parse", "--abbrev-ref", "HEAD") != branch:
            raise RuntimeError("fix turn moved HEAD branch")
        return verdict

    return fix


def default_failure_loop(
    workspace: str, repo: Any, fixer: FixRunner | None = None, first_run_seconds: float = 0.0
) -> TestFailureLoop:
    branch = _git_output(repo, "rev-parse", "--abbrev-ref", "HEAD")
    execution_id = next((part for part in branch.split("/") if part.startswith("E-")), "")
    ladder = tier_ladder(fixer.runner, fixer.model) if fixer else ()
    clock = _StageClock(fixer.stage_timer if fixer else None)
    clock.longest_run = first_run_seconds
    ports = LoopPorts(
        run_selection=functools.partial(clock.timed_run, functools.partial(measure_selection, workspace)),
        rerun_ids=functools.partial(run_failing_ids, cwd=workspace),
        base_rerun=BaseSnapshot(workspace, os.environ.get("HOLON_TARGET_BRANCH") or "main"),
        record=functools.partial(
            record_test_failure, os.path.join(workspace, *LEDGER_RELPATH), branch=branch, execution_id=execution_id
        ),
        stage=STAGE_3,
        head_sha=lambda: _git_output(repo, "rev-parse", "HEAD"),
        fix_turn=_fix_turn(workspace, repo, fixer, branch, clock) if fixer else None,
        ladder=ladder,
        start_tier=start_tier(fixer.runner, fixer.model, ladder) if fixer else None,
        remaining_seconds=clock.remaining,
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
    workspace: str,
    repo: Any,
    exec_status: str,
    *,
    fixer: FixRunner | None = None,
    failure_loop: FailureLoopFactory | None = None,
) -> dict[str, Any]:
    if exec_status != "success":
        return {}
    before = _changed_paths(repo)
    summary_runner = ShortSummaryRunner()
    started = time.monotonic()
    measurement = run_test_task(workspace, runner=summary_runner)
    first_run_seconds = time.monotonic() - started
    stray = sorted(_changed_paths(repo) - before)
    first = (measurement, summary_runner.last_output or measurement.output_excerpt)
    factory = failure_loop or functools.partial(default_failure_loop, fixer=fixer, first_run_seconds=first_run_seconds)
    outcome = _run_failure_loop(workspace, repo, first, factory)
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
