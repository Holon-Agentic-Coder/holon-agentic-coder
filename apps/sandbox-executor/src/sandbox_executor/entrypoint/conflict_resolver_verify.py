from __future__ import annotations

import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sandbox_executor.entrypoint.review_failure_detail import failed_test_ids, with_short_summary
from sandbox_executor.entrypoint.review_verification import missing_tool_result
from sandbox_executor.entrypoint.uv_lock import LockfileMismatchError, check_lockfile, frozen_env
from sandbox_executor.redaction import redact_command
from sandbox_executor.resolver_result import bounded_output_tail

RESOLVED = "resolved"
CANNOT_DECIDE = "cannot_decide"
LINT_FAILED = "lint_failed"
TESTS_FAILED = "tests_failed"
AGENT_FAILED = "agent_failed"
CANNOT_DECIDE_PREFIX = "CANNOT_DECIDE"
MAX_FAILING_TESTS = 20


@dataclass(frozen=True)
class Verdict:
    verdict: str
    reason: str = ""
    lint_exit: int | None = None
    test_exit: int | None = None
    failing_tests: list[str] = field(default_factory=list)
    output_tail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_cmd(args: list[str], cwd: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    print(f"Running: {redact_command(args)}", file=sys.stderr)
    try:
        return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    except OSError as exc:
        return missing_tool_result(args, exc)


def read_decision(decision_file: str) -> str | None:
    try:
        text = Path(decision_file).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.startswith(CANNOT_DECIDE_PREFIX):
        return None
    return text.removeprefix(CANNOT_DECIDE_PREFIX).lstrip(": ").strip() or "the intents are incompatible"


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(part for part in (result.stderr, result.stdout) if part)


# why: never trust what the agent says it ran; lint runs first so a broken resolution never pays for the suite
def verify_resolution(repo_dir: str, decision_file: str) -> Verdict:
    reason = read_decision(decision_file)
    if reason is not None:
        return Verdict(CANNOT_DECIDE, reason=bounded_output_tail(reason))
    try:
        lock_problem = check_lockfile(repo_dir)
    except LockfileMismatchError as exc:
        lock_problem = str(exc)
    if lock_problem:
        return Verdict(LINT_FAILED, reason="uv.lock check failed", output_tail=bounded_output_tail(lock_problem))
    lint = run_cmd(["uv", "run", "task", "lint"], cwd=repo_dir, env=frozen_env(cwd=repo_dir))
    if lint.returncode != 0:
        tail = bounded_output_tail(_output(lint))
        return Verdict(LINT_FAILED, reason="uv run task lint failed", lint_exit=lint.returncode, output_tail=tail)
    test = run_cmd(["uv", "run", "task", "test"], cwd=repo_dir, env=with_short_summary(frozen_env(cwd=repo_dir)))
    if test.returncode != 0:
        output = _output(test)
        return Verdict(
            TESTS_FAILED,
            reason="uv run task test failed",
            lint_exit=0,
            test_exit=test.returncode,
            failing_tests=failed_test_ids(output)[:MAX_FAILING_TESTS],
            output_tail=bounded_output_tail(output),
        )
    return Verdict(RESOLVED, reason="lint and tests passed", lint_exit=0, test_exit=0)
