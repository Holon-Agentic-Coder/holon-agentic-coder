from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sandbox_executor.redaction import redact_agent_secrets

RunFn = Callable[..., Any]
WhichFn = Callable[[str], str | None]

GH_MISSING = "gh not found"
_SPAWN_FAILURE_EXIT_CODE = 127
_NON_INTERACTIVE_GH_ENV = {"DOCKER_CLI_HINTS": "false", "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1"}


@dataclass(frozen=True)
class GhResult:
    available: bool
    returncode: int
    stdout: str
    stderr: str
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.available and self.returncode == 0

    @property
    def output(self) -> str:
        return redact_agent_secrets(f"{self.stdout or ''}\n{self.stderr or ''}".strip())


def gh_env(proxy_env: Mapping[str, str] | None) -> dict[str, str]:
    return {**os.environ, **(proxy_env or {}), **_NON_INTERACTIVE_GH_ENV}


def spawn_gh(run: RunFn, args: list[str], cwd: str, timeout: float, env: dict[str, str]) -> GhResult:
    try:
        proc = run(["gh", *args], cwd=cwd, capture_output=True, text=True, check=False, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        message = redact_agent_secrets(str(exc))
        return GhResult(True, _SPAWN_FAILURE_EXIT_CODE, "", message, error=message or type(exc).__name__)
    stdout = proc.stdout if isinstance(proc.stdout, str) else ""
    stderr = proc.stderr if isinstance(proc.stderr, str) else ""
    return GhResult(True, proc.returncode, stdout, redact_agent_secrets(stderr))
