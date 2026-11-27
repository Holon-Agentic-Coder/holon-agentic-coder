from __future__ import annotations

import os
import re
import signal
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from sandbox_executor.entrypoint.review_verification import MISSING_TOOL_EXIT_CODE, missing_tool_result
from sandbox_executor.entrypoint.uv_lock import frozen_env
from sandbox_executor.redaction import redact_agent_secrets

TEST_COMMAND = ["uv", "run", "task", "test"]
TEST_TIMEOUT_ENV = "HOLON_TEST_MEASUREMENT_TIMEOUT_SECONDS"
DEFAULT_TEST_TIMEOUT_SECONDS = 900.0
TIMEOUT_EXIT_CODE = 124
EXCERPT_MAX_CHARS = 2000

_SUMMARY_LINE_RE = re.compile(r"^[=\s]*(?P<counts>(?:\d+ [A-Za-z]+(?:, | and )?)+)\s+in\s+[\d.]+s\b")
_COUNT_RE = re.compile(r"(\d+) (passed|failed|errors?)\b")

Runner = Callable[[list[str], str, float], "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class TestMeasurement:
    __test__ = False

    rate: float | None
    passed: int | None
    failed: int | None
    errored: int | None
    exit_code: int
    run_error: str | None
    output_excerpt: str


def parse_pytest_counts(output: str) -> tuple[int, int, int] | None:
    for line in reversed(output.splitlines()):
        match = _SUMMARY_LINE_RE.match(line.strip())
        if not match:
            continue
        counts = {"passed": 0, "failed": 0, "error": 0}
        for number, label in _COUNT_RE.findall(match.group("counts")):
            counts[label.rstrip("s")] += int(number)
        return counts["passed"], counts["failed"], counts["error"]
    return None


def _excerpt(output: str) -> str:
    return redact_agent_secrets(output.strip())[-EXCERPT_MAX_CHARS:]


def _unmeasured(exit_code: int, error: str, output: str = "") -> TestMeasurement:
    return TestMeasurement(None, None, None, None, exit_code, redact_agent_secrets(error), _excerpt(output))


def measure_from_result(result: subprocess.CompletedProcess[str]) -> TestMeasurement:
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.returncode == MISSING_TOOL_EXIT_CODE and "was not found on PATH" in (result.stderr or ""):
        return _unmeasured(result.returncode, (result.stderr or "").strip(), output)
    counts = parse_pytest_counts(output)
    if counts is None:
        return _unmeasured(result.returncode, "no pytest summary line found in the test output", output)
    passed, failed, errored = counts
    total = passed + failed + errored
    if total == 0:
        return _unmeasured(result.returncode, "no tests ran", output)
    return TestMeasurement(passed / total, passed, failed, errored, result.returncode, None, _excerpt(output))


def configured_timeout() -> float:
    try:
        value = float(os.environ.get(TEST_TIMEOUT_ENV, ""))
    except ValueError:
        return DEFAULT_TEST_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TEST_TIMEOUT_SECONDS


def run_in_process_group(
    argv: list[str], cwd: str, timeout: float, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    with subprocess.Popen(
        argv,
        cwd=cwd,
        env=dict(env) if env is not None else frozen_env(cwd=cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        start_new_session=True,
    ) as proc:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise
        return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def run_test_task(workspace: str, runner: Runner | None = None, timeout: float | None = None) -> TestMeasurement:
    run = runner or run_in_process_group
    limit = timeout if timeout is not None else configured_timeout()
    try:
        result = run(list(TEST_COMMAND), workspace, limit)
    except subprocess.TimeoutExpired:
        return _unmeasured(TIMEOUT_EXIT_CODE, f"the test task timed out after {limit:g} seconds")
    except OSError as exc:
        failed = missing_tool_result(list(TEST_COMMAND), exc)
        return _unmeasured(failed.returncode, failed.stderr)
    return measure_from_result(result)
