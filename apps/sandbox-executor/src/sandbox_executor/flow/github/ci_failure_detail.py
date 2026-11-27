from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

TIMING_GATE_STEP_PREFIX = "CI Timing Gate"
# why: the Workspace survival and build-images jobs run the same gate script in a step named only "Timing gate".
TIMING_GATE_STEP_NAMES = frozenset({"Timing gate"})
STEP_LOOKUP_TIMEOUT_SECONDS = 30.0
MAX_STEP_LOOKUPS = 5
SHORT_SHA_CHARS = 7
_RUN_ID_PATTERN = re.compile(r"/actions/runs/(\d+)")

DetailLookup = Callable[[dict[str, Any]], "dict[str, Any] | None"]


def parse_run_id(link: str | None) -> str | None:
    match = _RUN_ID_PATTERN.search(str(link or ""))
    return match.group(1) if match else None


def _seconds_between(started: Any, completed: Any) -> float | None:
    try:
        start = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(completed).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (end - start).total_seconds())


def _is_timing_gate_step(step: str) -> bool:
    return step.startswith(TIMING_GATE_STEP_PREFIX) or step in TIMING_GATE_STEP_NAMES


def failure_detail(name: str, job: Mapping[str, Any] | None) -> dict[str, Any]:
    steps = (job or {}).get("steps") or []
    failed_steps = [str(s.get("name")) for s in steps if str(s.get("conclusion")).lower() == "failure"]
    return {
        "name": name,
        "failed_steps": failed_steps,
        "duration_seconds": _seconds_between((job or {}).get("startedAt"), (job or {}).get("completedAt")),
        "timing_gate": bool(failed_steps) and all(_is_timing_gate_step(s) for s in failed_steps),
    }


def bare_detail(name: str) -> dict[str, Any]:
    return failure_detail(name, None)


def gh_detail_lookup(
    repo_dir: str, proxy_env: Mapping[str, str], run: Callable[..., Any] = subprocess.run
) -> DetailLookup:
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
        # why: the detail only annotates a failure and never decides one, so every lookup error yields None
        except Exception:
            return None
        return None

    return lookup


def describe_failure(detail: Mapping[str, Any]) -> str:
    name = str(detail.get("name"))
    if not detail.get("timing_gate"):
        return name
    seconds = detail.get("duration_seconds")
    took = f", {float(seconds) / 60:.1f} min" if isinstance(seconds, int | float) else ""
    return f"{name} (CI Timing Gate{took})"


def short_sha(sha: str | None) -> str:
    return str(sha or "")[:SHORT_SHA_CHARS] or "unknown"
