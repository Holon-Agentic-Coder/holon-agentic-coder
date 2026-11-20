"""Paginated GitHub Actions REST API access for the CI duration report.

``list_runs`` pages ``actions/runs`` for every run created since a date and ``list_jobs`` pages
``actions/runs/{id}/jobs?filter=all``, so jobs of earlier attempts (the re-runs) are included. All network access goes
through ``ApiClient.opener``, which tests replace with a fake. The token is sent only through
``scripts.ci_timing_gate.build_request`` (an unredirected header) and is never part of a record or of the client's repr.
Standard library only, so the workflow runs on the runner's own ``python3``.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from scripts.ci_timing_gate import API_ROOT, build_request, default_opener

PAGE_SIZE = 100
RESULT_CAP = 1000  # the API answers 422 past the first 1000 results of a listing
REPORT_WORKFLOW = "ci-durations.yml"

Opener = Callable[[urllib.request.Request], bytes]


@dataclass(frozen=True)
class ApiClient:
    """The repository, credentials and transport used for every API call; the opener is the test injection point."""

    repo: str
    token: str = field(repr=False)
    opener: Opener = default_opener
    server: str = "https://github.com"

    def get_json(self, path: str) -> dict[str, object]:
        """Return the decoded JSON of ``GET /repos/{repo}/actions/{path}``."""
        url = f"{API_ROOT}/repos/{self.repo}/actions/{path}"
        return json.loads(self.opener(build_request(url, self.token)))

    def web_url(self, suffix: str) -> str:
        """Return a link into the repository on the configured server."""
        return f"{self.server.rstrip('/')}/{self.repo}/{suffix}"


def _is_reported_run(run: dict[str, object]) -> bool:
    """Return true for a completed run of any workflow other than this report's own."""
    path = str(run.get("path", "")).split("@")[0]
    return run.get("status") == "completed" and not path.endswith(REPORT_WORKFLOW)


def list_runs(client: ApiClient, since: date, max_runs: int) -> tuple[list[dict[str, object]], bool]:
    """Return the completed runs created on or after ``since`` and whether the listing was truncated."""
    created = urllib.parse.quote(f">={since.isoformat()}")
    runs: list[dict[str, object]] = []
    read = total = 0
    page = 1
    limit = min(max_runs, RESULT_CAP)
    while read < limit:
        listing = client.get_json(f"runs?created={created}&per_page={PAGE_SIZE}&page={page}")
        batch = listing.get("workflow_runs") or []
        total = max(total, int(listing.get("total_count") or 0))
        if not batch:
            break
        batch = batch[: limit - read]
        read += len(batch)
        runs.extend(run for run in batch if _is_reported_run(run))
        page += 1
    return runs, total > read


def list_jobs(client: ApiClient, run_id: int) -> list[dict[str, object]]:
    """Return every job of every attempt of run ``run_id``."""
    jobs: list[dict[str, object]] = []
    page = 1
    while True:
        batch = client.get_json(f"runs/{run_id}/jobs?filter=all&per_page={PAGE_SIZE}&page={page}").get("jobs") or []
        jobs.extend(batch)
        if len(batch) < PAGE_SIZE:
            return jobs
        page += 1
