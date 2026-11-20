"""Normalise GitHub Actions jobs into ``JobDuration`` records for the CI duration report.

``normalise_job`` turns one job of a run into a record: duration ``completed_at - started_at``, queue time
``started_at - created_at`` (clamped at 0), the attempt (re-runs are attempt 2 and later), the pull request whose base
is this repository, the ref class and links built from the configured server. ``collect`` reads every run of the window
through ``scripts.ci_durations_api``. Standard library only, like the timing gate.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime

from scripts.ci_durations_api import ApiClient, list_jobs, list_runs
from scripts.ci_timing_gate import BASELINE_BRANCH, FETCH_ERRORS

PULL_REQUEST_EVENTS = ("pull_request", "pull_request_target")
DROPPED_CONCLUSIONS = ("skipped",)
SAMPLE_FAILURES = 5  # failed job listings named in the single warning; the rest are only counted
_MATRIX_SUFFIX = re.compile(r"\s*\(([^()]*)\)\s*$")
_SHA = re.compile(r"[0-9a-f]{7,64}")


@dataclass(frozen=True)
class JobDuration:
    """One completed job attempt: its timings, outcome, attribution and links."""

    workflow: str
    workflow_path: str
    job: str
    runner_os: str
    run_id: int
    run_attempt: int
    event: str
    branch: str
    ref_class: str
    head_sha: str
    pr_number: int | None
    created_at: str
    started_at: str
    completed_at: str
    duration_seconds: float
    queue_seconds: float
    conclusion: str
    run_url: str
    commit_url: str
    pr_url: str | None

    @property
    def is_rerun(self) -> bool:
        """Return true for a job of a re-run attempt (attempt 2 or later)."""
        return self.run_attempt > 1

    @property
    def job_key(self) -> str:
        """Return the job name without its matrix suffix, so a series groups across matrix values."""
        return job_key(self.job)

    def to_json(self) -> dict[str, object]:
        """Return the record as a JSON-serialisable mapping."""
        return {**asdict(self), "is_rerun": self.is_rerun, "job_key": self.job_key}


@dataclass(frozen=True)
class Collection:
    """The collected job records, the number of runs read and any non-fatal warnings."""

    rows: list[JobDuration]
    run_count: int
    warnings: list[str]


def parse_time(text: str) -> datetime:
    """Parse an ISO-8601 API timestamp (``2026-11-20T14:16:59Z``) into an aware UTC ``datetime``."""
    stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def seconds_between(start: str, end: str) -> float:
    """Return the seconds from ``start`` to ``end``, clamped at 0 so clock skew never yields a negative time."""
    return max(0.0, (parse_time(end) - parse_time(start)).total_seconds())


def job_key(name: str) -> str:
    """Strip the matrix suffix from an API job name: ``unit (ubuntu-latest)`` becomes ``unit``."""
    return _MATRIX_SUFFIX.sub("", name)


def runner_os(job: dict[str, object]) -> str:
    """Return the runner label: ``labels[0]``, else the matrix suffix of the name, else ``runner_name``."""
    labels = job.get("labels") or []
    if isinstance(labels, list) and labels:
        return str(labels[0])
    suffix = _MATRIX_SUFFIX.search(str(job.get("name", "")))
    if suffix and suffix.group(1).strip():
        return suffix.group(1).split(",")[0].strip()
    return str(job.get("runner_name") or "unknown")


def _same_repo(pull: dict[str, object], run: dict[str, object], repo: str) -> bool:
    """Return true when the pull request's base is this repository (or the payload names no base)."""
    base = pull.get("base")
    base = base.get("repo") if isinstance(base, dict) else None
    if not isinstance(base, dict):
        return True
    run_repo = run.get("repository") or {}
    if base.get("id") is not None and isinstance(run_repo, dict) and run_repo.get("id") is not None:
        return base["id"] == run_repo["id"]
    return base.get("full_name") == repo or str(base.get("url", "")).endswith(f"/repos/{repo}")


def pr_number(run: dict[str, object], repo: str) -> int | None:
    """Return the first pull request number whose base is this repository; fork pull requests list none."""
    for pull in run.get("pull_requests") or []:
        if isinstance(pull, dict) and isinstance(pull.get("number"), int) and _same_repo(pull, run, repo):
            return pull["number"]
    return None


def classify_ref(run: dict[str, object], number: int | None) -> str:
    """Return ``main`` for a push to main, ``pr`` for a pull-request head, else ``other``."""
    if run.get("event") == "push" and run.get("head_branch") == BASELINE_BRANCH:
        return "main"
    if number is not None or run.get("event") in PULL_REQUEST_EVENTS:
        return "pr"
    return "other"


def _links(client: ApiClient, run: dict[str, object], job: dict[str, object], number: int | None) -> dict[str, object]:
    """Build the run, commit and pull request links from the configured server, never from API-supplied URLs."""
    sha = str(run.get("head_sha", ""))
    run_url = client.web_url(f"actions/runs/{int(run['id'])}")
    if isinstance(job.get("id"), int):
        run_url += f"/job/{job['id']}"
    return {
        "run_url": run_url,
        "commit_url": client.web_url(f"commit/{sha}") if _SHA.fullmatch(sha) else "",
        "pr_url": client.web_url(f"pull/{number}") if number is not None else None,
    }


def normalise_job(client: ApiClient, run: dict[str, object], job: dict[str, object]) -> JobDuration | None:
    """Return the job's record, or ``None`` when it is skipped, still running or never started."""
    started, completed = job.get("started_at"), job.get("completed_at")
    if job.get("conclusion") in DROPPED_CONCLUSIONS or not started or not completed or not job.get("conclusion"):
        return None
    created = str(job.get("created_at") or run.get("created_at") or started)
    number = pr_number(run, client.repo)
    return JobDuration(
        workflow=str(run.get("name", "")),
        workflow_path=str(run.get("path", "")).split("@")[0],
        job=str(job.get("name", "")),
        runner_os=runner_os(job),
        run_id=int(run["id"]),
        run_attempt=int(job.get("run_attempt") or run.get("run_attempt") or 1),
        event=str(run.get("event", "")),
        branch=str(run.get("head_branch") or ""),
        ref_class=classify_ref(run, number),
        head_sha=str(run.get("head_sha", "")),
        pr_number=number,
        created_at=created,
        started_at=str(started),
        completed_at=str(completed),
        duration_seconds=seconds_between(str(started), str(completed)),
        queue_seconds=seconds_between(created, str(started)),
        conclusion=str(job["conclusion"]),
        **_links(client, run, job, number),
    )


def collect(client: ApiClient, since: date, max_runs: int) -> Collection:
    """Collect every job record since ``since``; failed job listings are merged into one warning.

    A failure listing the runs themselves propagates: the report never renders partial or invented data silently.
    """
    runs, truncated = list_runs(client, since, max_runs)
    warnings = []
    if truncated:
        warnings.append(
            f"::warning::The run listing was truncated after {max_runs} runs or by the API's 1000-result cap; "
            "the report covers only the newest runs of the window."
        )
    rows: list[JobDuration] = []
    failed: list[str] = []
    for run in runs:
        try:
            jobs = list_jobs(client, int(run["id"]))
            rows.extend(row for job in jobs if (row := normalise_job(client, run, job)) is not None)
        except FETCH_ERRORS as exc:
            failed.append(f"{run.get('id')} ({type(exc).__name__})")
    if failed:
        more = f" and {len(failed) - SAMPLE_FAILURES} more" if len(failed) > SAMPLE_FAILURES else ""
        noun = "run" if len(failed) == 1 else f"{len(failed)} runs:"
        warnings.append(f"::warning::Could not read the jobs of {noun} {', '.join(failed[:SAMPLE_FAILURES])}{more}.")
    rows.sort(key=lambda row: (row.started_at, row.workflow, row.job, row.run_attempt))
    return Collection(rows, len(runs), warnings)
