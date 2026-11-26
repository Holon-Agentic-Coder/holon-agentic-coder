from __future__ import annotations

import glob
import os
import shutil
import signal
import subprocess
import tarfile
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from sandbox_executor.entrypoint.review_failure_detail import with_short_summary
from sandbox_executor.entrypoint.test_failure_record import IDENTITY_PREFIXES, failing_ids_from_output
from sandbox_executor.entrypoint.test_measurement import configured_timeout
from sandbox_executor.entrypoint.uv_lock import frozen_env
from sandbox_executor.git import run_git

PYTEST_NO_TESTS_COLLECTED = 5
PYTEST_USAGE_ERROR = 4
# why: xdist start-up dominates for a handful of ids, and a serial run keeps each excerpt readable.
RERUN_COMMAND = (
    "uv",
    "run",
    "--no-sync",
    "python",
    "-m",
    "pytest",
    "-p",
    "no:cacheprovider",
    "-q",
    "-rfE",
    "--tb=short",
)


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
SessionRun = Callable[[list[str], str, float, Mapping[str, str]], "subprocess.CompletedProcess[str]"]


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


def run_in_session(
    argv: list[str], cwd: str, timeout: float, env: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    with subprocess.Popen(
        argv,
        cwd=cwd,
        env=dict(env),
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


class ShortSummaryRunner:
    def __init__(self, session: SessionRun = run_in_session) -> None:
        self._session = session
        self.last_output = ""

    def __call__(self, argv: list[str], cwd: str, timeout: float) -> subprocess.CompletedProcess[str]:
        self.last_output = ""
        result = self._session(argv, cwd, timeout, with_short_summary(frozen_env(cwd=cwd)))
        self.last_output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        return result


def rerun_ids_command(failing_ids: tuple[str, ...]) -> list[str]:
    return [*RERUN_COMMAND, *failing_ids]


def run_failing_ids(
    failing_ids: tuple[str, ...], env: Mapping[str, str], cwd: str, session: SessionRun = run_in_session
) -> RerunResult:
    try:
        result = session(rerun_ids_command(failing_ids), cwd, configured_timeout(), env)
    except (OSError, subprocess.SubprocessError):
        return NOT_RUN
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    return RerunResult(result.returncode == 0, failing_ids_from_output(output), True, output, result.returncode)


def _merge_base(workspace: str, target_ref: str) -> str | None:
    for ref in (f"origin/{target_ref}", target_ref):
        result = run_git(["merge-base", "HEAD", ref], cwd=workspace, check=False)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return None


def _extract_snapshot(workspace: str, commit: str, root: str) -> bool:
    with subprocess.Popen(["git", "archive", "--format=tar", commit], cwd=workspace, stdout=subprocess.PIPE) as proc:
        assert proc.stdout is not None
        with tarfile.open(fileobj=proc.stdout, mode="r|") as archive:
            archive.extractall(root, filter="data")
        return proc.wait() == 0


class BaseSnapshot:
    def __init__(self, workspace: str, target_ref: str, session: SessionRun = run_in_session) -> None:
        self.workspace = workspace
        self.target_ref = target_ref
        self._session = session

    def environment(self, root: str) -> dict[str, str]:
        env = dict(os.environ)
        env.setdefault("UV_PROJECT_ENVIRONMENT", os.path.join(self.workspace, ".venv"))
        env["PYTHONPATH"] = os.pathsep.join(sorted(glob.glob(os.path.join(root, "apps", "*", "src"))))
        return env

    def __call__(self, failing_ids: tuple[str, ...]) -> RerunResult:
        commit = _merge_base(self.workspace, self.target_ref)
        if commit is None:
            return NOT_RUN
        root = tempfile.mkdtemp(prefix="holon-test-base-")
        try:
            if not _extract_snapshot(self.workspace, commit, root):
                return NOT_RUN
            return run_failing_ids(failing_ids, self.environment(root), root, self._session)
        except Exception:
            return NOT_RUN
        finally:
            shutil.rmtree(root, ignore_errors=True)
