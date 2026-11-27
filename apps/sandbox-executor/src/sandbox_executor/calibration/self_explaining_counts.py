from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

SELF_EXPLAINING_SCRIPT = Path(__file__).resolve().parents[5] / "scripts" / "check_self_explaining.py"
# why: covers the script's own 300-second fetch of origin/main plus the diff and classification
SELF_EXPLAINING_TIMEOUT_SECONDS = 600

logger = logging.getLogger(__name__)


def _unavailable_counts(reason: str) -> dict[str, Any]:
    logger.warning("Self-explaining counts unavailable: %s", reason)
    return {"unavailable": reason}


def _last_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[-1] if lines else ""


def measure_self_explaining_counts(target_worktree: str, head_ref: str) -> dict[str, Any]:
    if not SELF_EXPLAINING_SCRIPT.is_file():
        return _unavailable_counts(f"{SELF_EXPLAINING_SCRIPT} is missing")
    command = [sys.executable, str(SELF_EXPLAINING_SCRIPT), "--root", target_worktree, "--head", head_ref]
    try:
        completed = subprocess.run(
            [*command, "--counts-json"],
            cwd=target_worktree,
            capture_output=True,
            text=True,
            check=False,
            timeout=SELF_EXPLAINING_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return _unavailable_counts(type(error).__name__)
    if completed.returncode != 0:
        return _unavailable_counts(_last_line(completed.stderr) or f"exit code {completed.returncode}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return _unavailable_counts("unreadable counts output")
