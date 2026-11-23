"""The PR head commit as GitHub reports it, read once via ``gh pr view --json headRefOid``."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Mapping

PR_HEAD_TIMEOUT_SECONDS = 10.0


def pr_head_sha(
    repo_dir: str, target: str, proxy_env: Mapping[str, str] | None = None, timeout: float = PR_HEAD_TIMEOUT_SECONDS
) -> str | None:
    """The head commit of the PR for ``target`` (branch or number), or None when it cannot be read."""
    if not target or not shutil.which("gh"):
        return None
    env = {**os.environ, **(proxy_env or {}), "DOCKER_CLI_HINTS": "false"}
    try:
        proc = subprocess.run(
            ["gh", "pr", "view", target, "--json", "headRefOid"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=env,
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        return str(json.loads(proc.stdout.strip()).get("headRefOid") or "") or None
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return None
