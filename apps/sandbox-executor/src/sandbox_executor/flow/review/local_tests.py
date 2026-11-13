"""Stage 4 local test measurement: when no CI registers and Stage 3 recorded no rate, run the test task here.

The result is cached per head commit, so the review loop measures once per head and re-measures only after the resolver
pushes a fix.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sandbox_executor.entrypoint.test_measurement import TestMeasurement, run_test_task
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.dispatch import _test_evidence
from sandbox_executor.git import run_git

TEST_SOURCE_STAGE_3 = "stage_3"
TEST_SOURCE_CI = "ci"
TEST_SOURCE_REVIEW_LOCAL = "review_local"

# The test run; replaced in tests so no real ``uv run task test`` starts.
run_measurement: Callable[[str], TestMeasurement] = run_test_task


@dataclass(frozen=True)
class _LocalTestOutcome:
    """The review-local measurement as the loop consumes it: a rate (None when unmeasured) and whether it failed."""

    rate: float | None
    failed: bool
    measurement: TestMeasurement


def _failed(measurement: TestMeasurement) -> bool:
    """A measured rate below 1.0 or a non-zero exit of a completed run; an unrunnable run is unmeasured, not failed."""
    if measurement.run_error:
        return False
    return measurement.exit_code != 0 or (measurement.rate is not None and measurement.rate < 1.0)


class _ReviewLocalTests:
    """Measures the project's tests in the review workspace, once per head commit."""

    def __init__(self, repo_dir: str, head_sha: str = "") -> None:
        self._repo_dir = repo_dir
        self._head_sha = head_sha
        self._cache: dict[str, _LocalTestOutcome] = {}

    def _head(self) -> str:
        res = run_git(["rev-parse", "HEAD"], cwd=self._repo_dir, check=False)
        return res.stdout.strip() if res.returncode == 0 and res.stdout.strip() else self._head_sha

    def measure(self) -> _LocalTestOutcome:
        """The measurement for the current head, running the test task only when this head is not yet cached."""
        head = self._head() or "unknown"
        if head not in self._cache:
            measurement = run_measurement(self._repo_dir)
            self._cache[head] = _LocalTestOutcome(measurement.rate, _failed(measurement), measurement)
        return self._cache[head]


def _applies(ci_status: dict[str, Any], stage_3_rate: float | None, stage_3_failed: bool) -> bool:
    """True when the probe fell back to ``local_tests`` and Stage 3 left the tests unmeasured."""
    return ci_status.get("source") == "local_tests" and stage_3_rate is None and not stage_3_failed


def _attribute_test_source(
    ci_status: dict[str, Any], stage_3: dict[str, Any] | None, review_local: _LocalTestOutcome | None
) -> None:
    """Record on the CI probe where its test evidence came from (``stage_3``, ``ci`` or ``review_local``)."""
    if ci_status.get("source") == "github_cli":
        ci_status["test_source"] = TEST_SOURCE_CI
        return
    if review_local is not None:
        m = review_local.measurement
        ci_status["test_source"] = TEST_SOURCE_REVIEW_LOCAL
        ci_status["test_counts"] = {"passed": m.passed, "failed": m.failed, "errored": m.errored}
        if m.run_error:
            ci_status["test_run_error"] = m.run_error
    elif stage_3 and (stage_3.get("test_pass_rate") is not None or stage_3.get("test_run_error")):
        ci_status["test_source"] = TEST_SOURCE_STAGE_3
        ci_status["test_counts"] = {
            "passed": stage_3.get("tests_passed"),
            "failed": stage_3.get("tests_failed"),
            "errored": stage_3.get("tests_errored"),
        }


class _ReviewTestGate:
    """Stage 3's test evidence for the review loop, topped up by a local measurement when CI cannot supply it."""

    def __init__(self, context: FlowContext, repo_dir: str) -> None:
        exec_result = context.stage_results.get("execute")
        self._context = context
        self._stage_3 = (exec_result.payload if exec_result else None) or {}
        rate, exit_code = _test_evidence(exec_result)
        self.stage_3_evidence = (rate, exit_code != 0 or (rate is not None and rate < 1.0))
        self._tester = _ReviewLocalTests(repo_dir)

    def evidence(self, ci_status: dict[str, Any]) -> tuple[float | None, bool]:
        """The ``(test_pass_rate, tests_failed)`` for the CI gate, attributing the probe to its source.

        Measures locally only when the probe fell back to ``local_tests`` and Stage 3 recorded neither a rate nor a
        failure.
        """
        rate, failed = self.stage_3_evidence
        if not _applies(ci_status, rate, failed):
            _attribute_test_source(ci_status, self._stage_3, None)
            return rate, failed
        outcome = self._tester.measure()
        label = "not measured" if outcome.rate is None else f"rate {outcome.rate:.3f}"
        self._context.log(
            f"[Review Loop] No CI registered and Stage 3 recorded no rate; measured tests locally: {label}."
        )
        _attribute_test_source(ci_status, self._stage_3, outcome)
        return outcome.rate, outcome.failed
