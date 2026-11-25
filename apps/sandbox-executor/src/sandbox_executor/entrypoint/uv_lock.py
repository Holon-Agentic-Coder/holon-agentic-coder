import os
import subprocess
from collections.abc import Callable, Mapping

from sandbox_executor.entrypoint.review_verification import (
    MISSING_TOOL_EXIT_CODE,
    MISSING_TOOL_MARKER,
    missing_tool_result,
)

FROZEN_ENV_NAME = "UV_FROZEN"
LOCK_FILE = "uv.lock"
PROJECT_FILE = "pyproject.toml"
LOCK_CHECK_COMMAND = ["uv", "lock", "--check"]

LockRunner = Callable[[list[str], str], "subprocess.CompletedProcess[str]"]


class LockfileMismatchError(RuntimeError):
    pass


def frozen_env(base: Mapping[str, str] | None = None, cwd: str | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    has_lockfile = os.path.exists(os.path.join(cwd or os.getcwd(), LOCK_FILE))
    if has_lockfile:
        env[FROZEN_ENV_NAME] = "1"
    return env


def _default_runner(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    except OSError as exc:
        return missing_tool_result(args, exc)


def check_lockfile(directory: str, runner: LockRunner | None = None) -> str | None:
    has_project_and_lockfile = all(os.path.exists(os.path.join(directory, name)) for name in (LOCK_FILE, PROJECT_FILE))
    if not has_project_and_lockfile:
        return None
    result = (runner or _default_runner)(list(LOCK_CHECK_COMMAND), directory)
    if result.returncode == 0:
        return None
    is_missing_tool = result.returncode == MISSING_TOOL_EXIT_CODE and MISSING_TOOL_MARKER in (result.stderr or "")
    if is_missing_tool:
        return (result.stderr or "").strip()
    detail = (result.stderr or result.stdout or "").strip()
    raise LockfileMismatchError(
        f"{LOCK_FILE} does not match {PROJECT_FILE} in {directory}: regenerate it with the image's pinned uv "
        f"(`uv lock`, Python 3.13) and commit the result. {detail}".strip()
    )
