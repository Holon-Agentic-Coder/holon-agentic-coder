from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from sandbox_executor.entrypoint.test_failure_record import IDENTITY_PREFIXES

PYTEST_NO_TESTS_COLLECTED = 5
PYTEST_USAGE_ERROR = 4


class FailureClass(StrEnum):
    INTRODUCED = "introduced"
    FLAKY = "flaky"
    ENVIRONMENT = "environment"
    PRE_EXISTING = "pre-existing"


@dataclass(frozen=True)
class RerunResult:
    passed: bool
    failing_ids: tuple[str, ...] = ()
    ran: bool = True
    output: str = ""
    exit_code: int = 0


NOT_RUN = RerunResult(passed=False, ran=False)

Rerun = Callable[[tuple[str, ...], Mapping[str, str]], RerunResult]
BaseRerun = Callable[[tuple[str, ...]], RerunResult]


@dataclass(frozen=True)
class Diagnosis:
    classification: FailureClass
    same_env: RerunResult = NOT_RUN
    scrubbed: RerunResult = NOT_RUN
    base: RerunResult = NOT_RUN
    base_check: str = "not_run"
    environment_suspects: tuple[str, ...] = field(default_factory=tuple)


def identity_suspects(env: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(sorted(name for name in env if name.startswith(IDENTITY_PREFIXES)))


def scrubbed_environment(env: Mapping[str, str], home: str, tmpdir: str) -> dict[str, str]:
    scrubbed = {name: value for name, value in env.items() if not name.startswith(IDENTITY_PREFIXES)}
    original_home = env.get("HOME") or os.path.expanduser("~")
    # why: a clean HOME must not send uv to an empty cache, which would need the network the container lacks.
    scrubbed["UV_CACHE_DIR"] = env.get("UV_CACHE_DIR") or os.path.join(original_home, ".cache", "uv")
    scrubbed["HOME"] = home
    scrubbed["TMPDIR"] = tmpdir
    return scrubbed


def base_check_of(base: RerunResult) -> str:
    if not base.ran:
        return "unavailable"
    if base.exit_code in (PYTEST_USAGE_ERROR, PYTEST_NO_TESTS_COLLECTED) or "not found" in base.output:
        return "absent"
    return "passed" if base.passed else "failed"


def classify(
    same_env: RerunResult, scrubbed: RerunResult, base: RerunResult, failing_ids: tuple[str, ...]
) -> FailureClass:
    if same_env.ran and same_env.passed:
        return FailureClass.FLAKY
    if scrubbed.ran and scrubbed.passed:
        return FailureClass.ENVIRONMENT
    base_check = base_check_of(base)
    if base_check == "failed" and failing_ids and set(failing_ids) <= set(base.failing_ids):
        return FailureClass.PRE_EXISTING
    return FailureClass.INTRODUCED


def _scrubbed_rerun(failing_ids: tuple[str, ...], rerun: Rerun, env: Mapping[str, str]) -> RerunResult:
    with tempfile.TemporaryDirectory(prefix="holon-scrubbed-env-") as root:
        home, tmpdir = os.path.join(root, "home"), os.path.join(root, "tmp")
        os.makedirs(home)
        os.makedirs(tmpdir)
        return rerun(failing_ids, scrubbed_environment(env, home, tmpdir))


def diagnose(failing_ids: tuple[str, ...], rerun: Rerun, base_rerun: BaseRerun, env: Mapping[str, str]) -> Diagnosis:
    suspects = identity_suspects(env)
    if not failing_ids:
        return Diagnosis(FailureClass.INTRODUCED, environment_suspects=suspects)
    same_env = rerun(failing_ids, env)
    if same_env.ran and same_env.passed:
        return Diagnosis(FailureClass.FLAKY, same_env=same_env, environment_suspects=suspects)
    scrubbed = _scrubbed_rerun(failing_ids, rerun, env)
    if scrubbed.ran and scrubbed.passed:
        return Diagnosis(FailureClass.ENVIRONMENT, same_env, scrubbed, environment_suspects=suspects)
    base = base_rerun(failing_ids)
    return Diagnosis(
        classify(same_env, scrubbed, base, failing_ids),
        same_env,
        scrubbed,
        base,
        base_check_of(base),
        suspects,
    )
