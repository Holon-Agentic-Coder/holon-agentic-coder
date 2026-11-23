"""The single ``gh`` spawn behind :class:`~sandbox_executor.flow.github.adapter.GitHubAdapter` and its result type.

``gh`` is spawned through ``subprocess.run`` looked up at call time (unless a ``run`` is injected), so a test's global
``patch("subprocess.run")`` still intercepts it and an unmocked call still constructs ``subprocess.Popen``, where the
suite's ``tests/gh_write_guard.py`` refuses every ``gh`` write.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sandbox_executor.redaction import redact_agent_secrets

#: ``subprocess.run``-shaped callable the adapter spawns ``gh`` with.
RunFn = Callable[..., Any]
#: ``shutil.which``-shaped callable the adapter locates ``gh`` with.
WhichFn = Callable[[str], str | None]

GH_MISSING = "gh not found"


@dataclass(frozen=True)
class GhResult:
    """One ``gh`` invocation: ``stdout`` raw (callers parse JSON from it), ``stderr`` and ``error`` redacted.

    ``error`` is non-empty only when ``gh`` could not be run to completion (missing binary, ``OSError`` or a
    timeout); the result then carries returncode 127.
    """

    available: bool
    returncode: int
    stdout: str
    stderr: str
    error: str = ""

    @property
    def ok(self) -> bool:
        """True when ``gh`` was available and exited 0."""
        return self.available and self.returncode == 0

    @property
    def output(self) -> str:
        """The redacted, stripped ``stdout`` and ``stderr``, as the human-readable outcome text."""
        return redact_agent_secrets(f"{self.stdout or ''}\n{self.stderr or ''}".strip())


def gh_env(proxy_env: Mapping[str, str] | None) -> dict[str, str]:
    """Return the ``gh`` environment: the process environment, ``proxy_env`` merged, CLI hints off."""
    return {**os.environ, **(proxy_env or {}), "DOCKER_CLI_HINTS": "false"}


def spawn_gh(run: RunFn, args: list[str], cwd: str, timeout: float, env: dict[str, str]) -> GhResult:
    """Run ``gh <args>`` in ``cwd``; ``OSError`` and a timeout become a returncode-127 result, never a raise."""
    try:
        proc = run(["gh", *args], cwd=cwd, capture_output=True, text=True, check=False, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        message = redact_agent_secrets(str(exc))
        return GhResult(True, 127, "", message, error=message or type(exc).__name__)
    stdout = proc.stdout if isinstance(proc.stdout, str) else ""
    stderr = proc.stderr if isinstance(proc.stderr, str) else ""
    return GhResult(True, proc.returncode, stdout, redact_agent_secrets(stderr))
