"""The slowest tests of a failed CI job, so a timing-gate failure is fixed where the time goes.

Two sources, tried in order through ``gh`` on the host: the per-test durations that ``scripts/ci_timing_gate.py``
uploads as the ``ci-timing-*`` artefact (``record.json``), then the ``--durations`` section of the job log. Every
error yields an empty list. Test names are untrusted text: they are stripped of control characters, truncated and
redacted before a prompt sees them.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from sandbox_executor.flow.github.ci_failure_detail import STEP_LOOKUP_TIMEOUT_SECONDS, parse_run_id
from sandbox_executor.knowledge import CredentialRedactor

SLOWEST_TEST_COUNT = 5
MAX_TEST_NAME_CHARS = 200
ARTEFACT_PATTERN = "ci-timing-*"
RECORD_FILE = "record.json"
_JOB_ID_PATTERN = re.compile(r"/job/(\d+)")
_DURATION_LINE = re.compile(r"^\s*(\d+(?:\.\d+)?)s\s+(?:call|setup|teardown)\s+(\S.*?)\s*$")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

SlowestLookup = Callable[[dict[str, Any]], "list[tuple[str, float]]"]


def clean_test_name(name: str) -> str:
    """A test name safe to show: control characters removed, truncated and redacted."""
    return CredentialRedactor.redact_text(_CONTROL_CHARS.sub("", str(name)))[:MAX_TEST_NAME_CHARS]


def top_slowest(durations: Mapping[str, float], count: int = SLOWEST_TEST_COUNT) -> list[tuple[str, float]]:
    """The ``count`` slowest tests as ``(name, seconds)``, slowest first, with non-numeric durations dropped."""
    numeric = [(clean_test_name(k), float(v)) for k, v in durations.items() if isinstance(v, int | float)]
    return sorted(numeric, key=lambda item: item[1], reverse=True)[:count]


def durations_from_records(directory: Path, job_name: str) -> dict[str, float]:
    """Per-test durations from the ``record.json`` files under ``directory``, preferring the record of ``job_name``."""
    records: list[dict[str, Any]] = []
    for path in sorted(directory.rglob(RECORD_FILE)):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("tests"), dict):
            records.append(data)
    matching = [r for r in records if str(r.get("job") or "") and str(r["job"]) in job_name]
    chosen = matching or records
    return dict(chosen[0]["tests"]) if chosen else {}


def durations_from_log(log: str) -> dict[str, float]:
    """Per-test durations from the ``--durations`` section of a pytest log; the longest of repeated names wins."""
    found: dict[str, float] = {}
    for line in log.splitlines():
        match = _DURATION_LINE.search(re.sub(r"^\S+\s+\S+\s+\d{4}-\S+Z\s", "", line))
        if match:
            found[match.group(2)] = max(found.get(match.group(2), 0.0), float(match.group(1)))
    return found


def gh_slowest_lookup(
    repo_dir: str, proxy_env: Mapping[str, str], run: Callable[..., Any] = subprocess.run
) -> SlowestLookup:
    """A lookup of the slowest tests of a failed check through ``gh run download`` then ``gh run view --log``."""
    env = {**os.environ, **proxy_env, "DOCKER_CLI_HINTS": "false"}

    def call(args: list[str]) -> Any:
        return run(
            ["gh", *args],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
            timeout=STEP_LOOKUP_TIMEOUT_SECONDS,
            env=env,
        )

    def lookup(check: dict[str, Any]) -> list[tuple[str, float]]:
        run_id = parse_run_id(check.get("link"))
        if run_id is None:
            return []
        try:
            with tempfile.TemporaryDirectory() as tmp:
                downloaded = call(["run", "download", run_id, "--pattern", ARTEFACT_PATTERN, "--dir", tmp])
                if downloaded.returncode == 0:
                    durations = durations_from_records(Path(tmp), str(check.get("name") or ""))
                    if durations:
                        return top_slowest(durations)
            job = _JOB_ID_PATTERN.search(str(check.get("link") or ""))
            logged = call(["run", "view", run_id, "--log", *(["--job", job.group(1)] if job else [])])
            return top_slowest(durations_from_log(logged.stdout or "")) if logged.returncode == 0 else []
        except Exception:
            return []

    return lookup
