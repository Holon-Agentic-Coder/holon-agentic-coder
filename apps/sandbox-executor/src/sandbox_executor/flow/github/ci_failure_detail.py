"""Detail of a failed CI check for reports: failed step names and the job duration, through ``gh run view``.

The lookup only annotates a failure. It never decides whether a check failed, and every error yields None.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

TIMING_GATE_STEP_PREFIX = "CI Timing Gate"
STEP_LOOKUP_TIMEOUT_SECONDS = 30.0
MAX_STEP_LOOKUPS = 5
SHORT_SHA_CHARS = 7
_RUN_ID_PATTERN = re.compile(r"/actions/runs/(\d+)")

DetailLookup = Callable[[dict[str, Any]], "dict[str, Any] | None"]


def parse_run_id(link: str | None) -> str | None:
    """The Actions run id in a check link, or None when the link is not an Actions run URL."""
    match = _RUN_ID_PATTERN.search(str(link or ""))
    return match.group(1) if match else None


def _seconds_between(started: Any, completed: Any) -> float | None:
    """Whole seconds between two ISO 8601 timestamps, or None when either is missing or malformed."""
    try:
        start = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(completed).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (end - start).total_seconds())


def failure_detail(name: str, job: Mapping[str, Any] | None) -> dict[str, Any]:
    """The detail record of one failed check: its failed step names, duration and timing gate flag."""
    steps = (job or {}).get("steps") or []
    failed_steps = [str(s.get("name")) for s in steps if str(s.get("conclusion")).lower() == "failure"]
    return {
        "name": name,
        "failed_steps": failed_steps,
        "duration_seconds": _seconds_between((job or {}).get("startedAt"), (job or {}).get("completedAt")),
        "timing_gate": bool(failed_steps) and all(s.startswith(TIMING_GATE_STEP_PREFIX) for s in failed_steps),
    }


def bare_detail(name: str) -> dict[str, Any]:
    """The detail record of a failed check nothing more is known about."""
    return failure_detail(name, None)


def gh_detail_lookup(
    repo_dir: str, proxy_env: Mapping[str, str], run: Callable[..., Any] = subprocess.run
) -> DetailLookup:
    """A lookup of the detail of a failed check through ``gh run view``; every error yields None."""
    env = {**os.environ, **proxy_env, "DOCKER_CLI_HINTS": "false"}

    def lookup(check: dict[str, Any]) -> dict[str, Any] | None:
        run_id = parse_run_id(check.get("link"))
        if run_id is None:
            return None
        try:
            proc = run(
                ["gh", "run", "view", run_id, "--json", "jobs"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=STEP_LOOKUP_TIMEOUT_SECONDS,
                env=env,
            )
            if proc.returncode != 0:
                return None
            for job in json.loads(proc.stdout).get("jobs") or []:
                if job.get("name") == check.get("name"):
                    return failure_detail(str(check.get("name")), job)
        except Exception:
            return None
        return None

    return lookup


def describe_failure(detail: Mapping[str, Any]) -> str:
    """``name`` or ``name (CI Timing Gate, 6.0 min)`` for a report line."""
    name = str(detail.get("name"))
    if not detail.get("timing_gate"):
        return name
    seconds = detail.get("duration_seconds")
    took = f", {float(seconds) / 60:.1f} min" if isinstance(seconds, int | float) else ""
    return f"{name} (CI Timing Gate{took})"


def short_sha(sha: str | None) -> str:
    """The first seven characters of a commit SHA, or ``unknown`` when none is recorded."""
    return str(sha or "")[:SHORT_SHA_CHARS] or "unknown"
