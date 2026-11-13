"""Keep the committed ``uv.lock`` out of the flow's way: freeze it for every flow-owned ``uv`` call.

``UV_FROZEN=1`` makes ``uv run`` trust the lock as is, so a stage can never rewrite it. Frozen mode never reports a
disagreement, so ``check_lockfile`` runs ``uv lock --check`` first and fails with a named ``LockfileMismatchError``.
"""

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
    """The committed ``uv.lock`` disagrees with ``pyproject.toml`` (or the interpreter) and needs regenerating."""


def frozen_env(base: Mapping[str, str] | None = None, cwd: str | None = None) -> dict[str, str]:
    """Return a copy of ``base`` (default: the process environment), freezing the lock when ``cwd`` has one.

    A directory without ``uv.lock`` keeps the inherited environment: frozen mode would make ``uv run`` fail there.
    """
    env = dict(os.environ if base is None else base)
    if os.path.exists(os.path.join(cwd or os.getcwd(), LOCK_FILE)):
        env[FROZEN_ENV_NAME] = "1"
    return env


def _default_runner(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    except OSError as exc:
        return missing_tool_result(args, exc)


def check_lockfile(directory: str, runner: LockRunner | None = None) -> str | None:
    """Fail with ``LockfileMismatchError`` when ``directory``'s lock needs a rewrite.

    Returns ``None`` when the lock is current. A directory without both files has nothing to check. A missing ``uv``
    returns its structured message (the caller reports it as an unrunnable tool) instead of raising.
    """
    if not all(os.path.exists(os.path.join(directory, name)) for name in (LOCK_FILE, PROJECT_FILE)):
        return None
    result = (runner or _default_runner)(list(LOCK_CHECK_COMMAND), directory)
    if result.returncode == 0:
        return None
    if result.returncode == MISSING_TOOL_EXIT_CODE and MISSING_TOOL_MARKER in (result.stderr or ""):
        return (result.stderr or "").strip()
    detail = (result.stderr or result.stdout or "").strip()
    raise LockfileMismatchError(
        f"{LOCK_FILE} does not match {PROJECT_FILE} in {directory}: regenerate it with the image's pinned uv "
        f"(`uv lock`, Python 3.13) and commit the result. {detail}".strip()
    )
