"""Hermetic tests for the CI duration report's collection, statistics, entry point and workflow.

Every API response is a recorded-shape payload served by ``FakeOpener``, which also asserts that no request leaves
``https://api.github.com/repos/<repo>/actions/``; nothing here reaches the network.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from scripts.ci_durations import build_parser, main
from scripts.ci_durations_api import ApiClient, list_runs
from scripts.ci_durations_collect import (
    JobDuration,
    classify_ref,
    collect,
    job_key,
    normalise_job,
    pr_number,
    runner_os,
    seconds_between,
)
from scripts.ci_durations_stats import (
    annotate,
    ceiling_for,
    load_ceilings,
    parse_ceilings,
    rolling_medians,
    slowest_vs_baseline,
)
from tests.test_workflow_triggers import parse_triggers

REPO = "acme/widgets"
REPO_ID = 42
API = f"https://api.github.com/repos/{REPO}/actions/"
SINCE = date(2026, 10, 21)
RUNS_QUERY = "runs?created=%3E%3D2026-10-21&per_page=100"
SHA_MAIN = "a" * 40
SHA_PR = "b" * 40
SHA_FORK = "c" * 40


class FakeOpener:
    """Serve recorded payloads keyed by URL suffix, record every URL and refuse anything outside the actions API."""

    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.urls: list[str] = []
        self.auth: list[str | None] = []

    def __call__(self, request: urllib.request.Request) -> bytes:
        url = request.full_url
        assert url.startswith(API), f"request outside the actions API: {url}"
        self.urls.append(url)
        self.auth.append(request.unredirected_hdrs.get("Authorization"))
        payload = self.responses.get(url.removeprefix(API))
        if payload is None:
            raise urllib.error.URLError(f"no recorded response for {url}")
        if isinstance(payload, Exception):
            raise payload
        return json.dumps(payload).encode()


def run(run_id: int, **overrides: object) -> dict[str, object]:
    """Return a recorded-shape ``actions/runs`` entry: a completed push to main of ``test-unit.yml``."""
    base: dict[str, object] = {
        "id": run_id,
        "name": "Test - Unit",
        "path": ".github/workflows/test-unit.yml",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "head_branch": "main",
        "head_sha": SHA_MAIN,
        "run_attempt": 1,
        "created_at": "2026-11-01T10:00:00Z",
        "pull_requests": [],
        "repository": {"id": REPO_ID, "full_name": REPO},
    }
    return {**base, **overrides}


def job(job_id: int, **overrides: object) -> dict[str, object]:
    """Return a recorded-shape ``runs/{id}/jobs`` entry: a successful 200 s unit job that queued for 30 s."""
    base: dict[str, object] = {
        "id": job_id,
        "name": "unit (ubuntu-latest)",
        "status": "completed",
        "conclusion": "success",
        "run_attempt": 1,
        "created_at": "2026-11-01T10:00:00Z",
        "started_at": "2026-11-01T10:00:30Z",
        "completed_at": "2026-11-01T10:03:50Z",
        "labels": ["ubuntu-latest"],
        "runner_name": "GitHub Actions 7",
        "html_url": f"https://github.com/{REPO}/actions/runs/1/job/{job_id}",
    }
    return {**base, **overrides}


def pull(number: int, repo_id: int = REPO_ID) -> dict[str, object]:
    """Return a ``pull_requests`` entry whose base repository has ``repo_id``."""
    return {
        "number": number,
        "base": {"ref": "main", "repo": {"id": repo_id, "url": f"https://api.github.com/r/{repo_id}"}},
    }


PR_RUN = run(2, event="pull_request", head_branch="feature/x", head_sha=SHA_PR, pull_requests=[pull(7)])
FORK_RUN = run(3, event="pull_request", head_branch="patch-1", head_sha=SHA_FORK, pull_requests=[])
JOBS = {
    1: [job(11)],
    2: [
        job(21, conclusion="failure"),
        job(22, run_attempt=2, started_at="2026-11-01T10:10:00Z", completed_at="2026-11-01T10:13:00Z"),
    ],
    3: [
        job(31, conclusion="cancelled", started_at=None, completed_at="2026-11-01T10:01:00Z"),
        job(32, conclusion="skipped"),
        job(33, conclusion="cancelled", completed_at="2026-11-01T10:01:30Z"),
    ],
}


def client(opener: FakeOpener) -> ApiClient:
    return ApiClient(repo=REPO, token="tok-secret-123", opener=opener)


def recorded_api(runs: list[dict[str, object]] | None = None) -> FakeOpener:
    """Return an opener serving one page of runs, an empty second page, and the jobs of runs 1 to 3."""
    runs = runs if runs is not None else [run(1), PR_RUN, FORK_RUN]
    responses: dict[str, object] = {
        f"{RUNS_QUERY}&page=1": {"total_count": len(runs), "workflow_runs": runs},
        f"{RUNS_QUERY}&page=2": {"total_count": len(runs), "workflow_runs": []},
    }
    for run_id, jobs in JOBS.items():
        responses[f"runs/{run_id}/jobs?filter=all&per_page=100&page=1"] = {"total_count": len(jobs), "jobs": jobs}
    return FakeOpener(responses)


def collected() -> dict[int, JobDuration]:
    rows = collect(client(recorded_api()), SINCE, 500).rows
    return {int(row.run_url.rsplit("/", 1)[1]): row for row in rows}


def test_durations_and_queue_times_are_computed_from_the_job_stamps() -> None:
    rows = collected()
    assert rows[11].duration_seconds == 200.0
    assert rows[11].queue_seconds == 30.0
    assert rows[22].duration_seconds == 180.0
    assert rows[22].queue_seconds == 600.0
    assert seconds_between("2026-11-01T10:00:30Z", "2026-11-01T10:00:00Z") == 0.0


def test_reruns_are_marked_only_from_attempt_two() -> None:
    rows = collected()
    assert rows[22].is_rerun and rows[22].run_attempt == 2
    assert not rows[21].is_rerun
    assert rows[22].to_json()["is_rerun"] is True


def test_pull_request_numbers_and_links_are_attached() -> None:
    rows = collected()
    assert rows[21].pr_number == 7
    assert rows[21].pr_url == f"https://github.com/{REPO}/pull/7"
    assert rows[21].commit_url == f"https://github.com/{REPO}/commit/{SHA_PR}"
    assert rows[21].run_url == f"https://github.com/{REPO}/actions/runs/2/job/21"
    assert rows[21].ref_class == "pr"
    assert rows[11].ref_class == "main" and rows[11].pr_url is None
    assert rows[11].commit_url == f"https://github.com/{REPO}/commit/{SHA_MAIN}"


def test_a_fork_pull_request_has_no_number_but_is_still_a_pr_point() -> None:
    rows = collected()
    assert rows[33].pr_number is None and rows[33].pr_url is None
    assert rows[33].ref_class == "pr" and rows[33].branch == "patch-1"


def test_skipped_and_never_started_jobs_are_dropped_and_cancelled_jobs_kept() -> None:
    rows = collected()
    assert set(rows) == {11, 21, 22, 33}
    assert rows[33].conclusion == "cancelled"
    assert rows[21].conclusion == "failure"


def test_a_pull_request_of_another_repository_is_not_attributed() -> None:
    assert pr_number(run(9, pull_requests=[pull(5, repo_id=99), pull(8)]), REPO) == 8
    assert pr_number(run(9, pull_requests=[{"number": 4}]), REPO) == 4
    assert pr_number(run(9, pull_requests=[{"number": 4, "base": {"repo": {"url": "x/repos/o/other"}}}]), REPO) is None


@pytest.mark.parametrize(
    ("event", "branch", "number", "expected"),
    [
        ("push", "main", None, "main"),
        ("pull_request", "main", None, "pr"),
        ("push", "feature", 3, "pr"),
        ("workflow_dispatch", "main", None, "other"),
        ("schedule", "main", None, "other"),
    ],
)
def test_classify_ref(event: str, branch: str, number: int | None, expected: str) -> None:
    assert classify_ref({"event": event, "head_branch": branch}, number) == expected


def test_runner_os_falls_back_from_labels_to_the_matrix_suffix_then_the_runner_name() -> None:
    assert runner_os({"labels": ["macos-latest"], "name": "unit (ubuntu-latest)"}) == "macos-latest"
    assert runner_os({"labels": [], "name": "unit (ubuntu-latest, 120)"}) == "ubuntu-latest"
    assert runner_os({"labels": [], "name": "build", "runner_name": "self-1"}) == "self-1"
    assert runner_os({"name": "build"}) == "unknown"
    assert job_key("Workspace survival (ubuntu-latest)") == "Workspace survival"


def test_normalise_job_drops_a_job_that_is_still_running() -> None:
    running = job(1, status="in_progress", conclusion=None, completed_at=None)
    assert normalise_job(client(recorded_api()), run(1), running) is None


def test_pagination_reads_every_page_and_excludes_the_report_and_unfinished_runs() -> None:
    page_one = [run(i) for i in range(1, 101)]
    page_two = [
        run(101),
        run(102, path=".github/workflows/ci-durations.yml"),
        run(103, status="in_progress"),
    ]
    opener = FakeOpener(
        {
            f"{RUNS_QUERY}&page=1": {"total_count": 103, "workflow_runs": page_one},
            f"{RUNS_QUERY}&page=2": {"total_count": 103, "workflow_runs": page_two},
            f"{RUNS_QUERY}&page=3": {"total_count": 103, "workflow_runs": []},
        }
    )
    runs, truncated = list_runs(client(opener), SINCE, 500)
    assert [r["id"] for r in runs] == list(range(1, 102))
    assert not truncated
    assert len(opener.urls) == 3


def test_a_run_served_on_two_pages_is_listed_once() -> None:
    opener = FakeOpener(
        {
            f"{RUNS_QUERY}&page=1": {"total_count": 3, "workflow_runs": [run(3), run(2)]},
            f"{RUNS_QUERY}&page=2": {"total_count": 3, "workflow_runs": [run(2), run(1)]},
            f"{RUNS_QUERY}&page=3": {"total_count": 3, "workflow_runs": []},
        }
    )
    runs, _ = list_runs(client(opener), SINCE, 500)
    assert [r["id"] for r in runs] == [3, 2, 1]


def test_max_runs_truncates_the_listing_and_collect_warns() -> None:
    opener = recorded_api([run(1), PR_RUN, FORK_RUN])
    opener.responses[f"{RUNS_QUERY}&page=1"] = {"total_count": 3, "workflow_runs": [run(1), PR_RUN, FORK_RUN]}
    result = collect(client(opener), SINCE, 2)
    assert result.run_count == 2
    assert any("truncated" in warning for warning in result.warnings)


def test_a_failed_job_listing_becomes_a_warning_naming_the_run() -> None:
    opener = recorded_api()
    opener.responses["runs/2/jobs?filter=all&per_page=100&page=1"] = urllib.error.HTTPError(
        "u", 502, "Bad Gateway", None, None
    )
    result = collect(client(opener), SINCE, 500)
    assert {row.run_id for row in result.rows} == {1, 3}
    assert result.warnings == ["::warning::Could not read the jobs of run 2 (HTTPError)."]


def test_a_run_whose_job_fails_to_normalise_contributes_no_rows() -> None:
    opener = recorded_api()
    jobs = [job(21), job(22, started_at="not a time")]
    opener.responses["runs/2/jobs?filter=all&per_page=100&page=1"] = {"total_count": 2, "jobs": jobs}
    result = collect(client(opener), SINCE, 500)
    assert {row.run_id for row in result.rows} == {1, 3}
    assert result.warnings == ["::warning::Could not read the jobs of run 2 (ValueError)."]


def test_many_failed_job_listings_become_one_warning_with_a_count() -> None:
    opener = recorded_api([run(i) for i in range(1, 9)])
    for i in range(1, 9):
        opener.responses[f"runs/{i}/jobs?filter=all&per_page=100&page=1"] = urllib.error.HTTPError(
            "u", 403, "rate limit exceeded", None, None
        )
    result = collect(client(opener), SINCE, 500)
    assert result.rows == []
    assert result.warnings == [
        "::warning::Could not read the jobs of 8 runs: 1 (HTTPError), 2 (HTTPError), 3 (HTTPError), "
        "4 (HTTPError), 5 (HTTPError) and 3 more."
    ]


def test_the_run_listing_stops_at_the_api_result_cap() -> None:
    pages = {
        f"{RUNS_QUERY}&page={page}": {
            "total_count": 1500,
            "workflow_runs": [run(i) for i in range(page * 100 - 99, page * 100 + 1)],
        }
        for page in range(1, 12)
    }
    opener = FakeOpener(pages)
    runs, truncated = list_runs(client(opener), SINCE, 5000)
    assert len(runs) == 1000
    assert truncated
    assert not any(url.endswith("&page=11") for url in opener.urls)


def test_a_failed_run_listing_is_not_hidden() -> None:
    with pytest.raises(urllib.error.URLError):
        collect(client(FakeOpener({})), SINCE, 500)


def test_every_request_stays_on_the_actions_api_with_the_token_unredirected() -> None:
    opener = recorded_api()
    collect(client(opener), SINCE, 500)
    assert all(url.startswith(API) for url in opener.urls)
    assert set(opener.auth) == {"Bearer tok-secret-123"}
    assert "tok-secret-123" not in repr(client(opener))


def started(day: int) -> str:
    return datetime(2026, 11, day, 10, tzinfo=UTC).isoformat().replace("+00:00", "Z")


def test_job_records_round_trip_through_json() -> None:
    row = collected()[22]
    data = json.loads(json.dumps(row.to_json()))
    fields = {key: value for key, value in data.items() if key not in ("is_rerun", "job_key")}
    assert JobDuration(**fields) == row
    assert data["job_key"] == "unit"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT[\d:]+Z", data["started_at"])
    assert started(2) == "2026-11-02T10:00:00Z"


def workflows_dir() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".github" / "workflows").is_dir():
            return parent / ".github" / "workflows"
    raise AssertionError("no .github/workflows directory above the test module")


def point(day: int, duration: float, **overrides: object) -> JobDuration:
    """Return a ``test-unit.yml`` unit point on ubuntu that started on 2026-11-``day``."""
    base: dict[str, object] = {
        "workflow": "Test - Unit",
        "workflow_path": ".github/workflows/test-unit.yml",
        "job": "unit (ubuntu-latest)",
        "runner_os": "ubuntu-latest",
        "run_id": day,
        "run_attempt": 1,
        "event": "push",
        "branch": "main",
        "ref_class": "main",
        "head_sha": SHA_MAIN,
        "pr_number": None,
        "created_at": started(day),
        "started_at": started(day),
        "completed_at": started(day),
        "duration_seconds": duration,
        "queue_seconds": 5.0,
        "conclusion": "success",
        "run_url": f"https://github.com/{REPO}/actions/runs/{day}/job/{day}",
        "commit_url": f"https://github.com/{REPO}/commit/{SHA_MAIN}",
        "pr_url": None,
    }
    return JobDuration(**{**base, **overrides})


def test_rolling_median_uses_the_previous_window_of_main_successes_only() -> None:
    rows = [point(day, 10.0 * day) for day in range(1, 6)] + [point(6, 100.0), point(7, 1.0)]
    medians = rolling_medians(rows, window=5)
    assert [medians[row] for row in rows[:5]] == [None] * 5
    assert medians[rows[5]] == 30.0
    assert medians[rows[6]] == 40.0


def test_an_even_window_takes_the_mean_of_the_two_middle_points() -> None:
    rows = [point(day, value) for day, value in enumerate((10.0, 20.0, 80.0, 40.0, 100.0, 1.0), start=1)]
    medians = rolling_medians(rows, window=4)
    assert medians[rows[4]] == 30.0  # median of 10, 20, 80, 40
    assert medians[rows[5]] == 60.0  # median of 20, 80, 40, 100


def test_failed_and_pull_request_points_do_not_feed_the_baseline_but_get_one() -> None:
    rows = [point(day, 10.0 * day) for day in range(1, 6)]
    rows += [point(6, 999.0, conclusion="failure"), point(7, 999.0, ref_class="pr", event="pull_request")]
    rows += [point(8, 60.0)]
    medians = rolling_medians(rows, window=5)
    assert medians[rows[5]] == medians[rows[6]] == medians[rows[7]] == 30.0


def test_medians_are_computed_separately_per_runner_os() -> None:
    ubuntu = [point(day, 10.0) for day in range(1, 4)]
    macos = [point(day, 50.0, runner_os="macos-latest", job="unit (macos-latest)") for day in range(1, 4)]
    ubuntu.append(point(4, 1.0))
    medians = rolling_medians(ubuntu + macos, window=3)
    assert medians[ubuntu[3]] == 10.0
    assert medians[macos[2]] is None


def test_ceilings_are_read_from_the_committed_workflows() -> None:
    ceilings = load_ceilings(workflows_dir())
    expected = {
        ("build-images.yml", "build"): 600,
        ("docker-build.yml", "build"): 300,
        ("make.yml", "build"): 300,
        ("test-hygiene.yml", "hygiene"): 300,
        ("test-integration.yml", "integration"): 300,
        ("test-stress.yml", "workspace-survival"): 600,
        ("test-stress.yml", "Workspace survival"): 600,
        ("test-unit.yml", "unit"): 600,
        ("ci-durations.yml", "report"): 600,
    }
    assert {key: ceilings.get(key) for key in expected} == expected
    assert ceiling_for(point(1, 1.0), ceilings) == 600
    assert (
        ceiling_for(
            point(1, 1.0, job="Workspace survival (ubuntu-latest)", workflow_path="x/test-stress.yml"), ceilings
        )
        == 600
    )
    assert ceiling_for(point(1, 1.0, job="lint"), ceilings) is None


_COMPOSITE = """\
name: Snippet
on: workflow_dispatch
jobs:
  plain:
    name: Plain (${{ matrix.os }})
    strategy:
      matrix:
        include:
          - os: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - name: Start
        uses: ./.github/actions/ci-timing-gate
        with:
          phase: start
          max-seconds: 50
      - name: Check
        if: always()
        uses: ./.github/actions/ci-timing-gate
        with:
          phase: check
  custom:
    steps:
      - uses: ./.github/actions/ci-timing-gate
        with:
          phase: "check"
          max-seconds: "900"
  ungated:
    steps:
      - run: echo --ceiling 5
"""


def test_the_composite_gate_defaults_to_300_and_honours_max_seconds() -> None:
    assert parse_ceilings(_COMPOSITE) == {"plain": 300, "Plain": 300, "custom": 900}


def test_slowest_versus_baseline_ranks_by_delta() -> None:
    unit = [point(day, 100.0) for day in range(1, 6)] + [point(6, 250.0)]
    macos = [point(day, 200.0, runner_os="macos-latest", job="unit (macos-latest)") for day in range(1, 6)]
    macos.append(point(6, 500.0, runner_os="macos-latest", job="unit (macos-latest)"))
    fresh = [point(1, 900.0, job="lint")]
    faster = [point(day, 300.0, job="docs") for day in range(1, 6)] + [point(6, 100.0, job="docs")]
    rows = unit + macos + fresh + faster
    ranking = slowest_vs_baseline(rows, rolling_medians(rows), limit=10)
    assert [(r.row.runner_os, r.median, r.delta, r.ratio) for r in ranking] == [
        ("macos-latest", 200.0, 300.0, 2.5),
        ("ubuntu-latest", 100.0, 150.0, 2.5),
    ]
    assert len(slowest_vs_baseline(rows, rolling_medians(rows), limit=1)) == 1


def test_annotate_adds_the_baseline_and_the_ceiling() -> None:
    rows = [point(day, 10.0) for day in range(1, 7)]
    data = annotate(rows, rolling_medians(rows), {("test-unit.yml", "unit"): 600})
    assert data[5]["baseline_median"] == 10.0 and data[5]["ceiling"] == 600
    assert data[0]["baseline_median"] is None


NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)
SECRET = "tok-secret-123"


def main_history_api() -> FakeOpener:
    """Serve six main pushes of test-unit (the last one 300 s against 100 s before) plus the PR and fork runs."""
    opener = recorded_api()
    history = [run(100 + day, created_at=started(day)) for day in range(1, 7)]
    runs = [*history, PR_RUN, FORK_RUN]
    opener.responses[f"{RUNS_QUERY}&page=1"] = {"total_count": len(runs), "workflow_runs": runs}
    for day in range(1, 7):
        stop = datetime(2026, 11, day, 10, tzinfo=UTC).timestamp() + (300 if day == 6 else 100)
        completed = datetime.fromtimestamp(stop, UTC).isoformat().replace("+00:00", "Z")
        jobs = [job(1000 + day, created_at=started(day), started_at=started(day), completed_at=completed)]
        opener.responses[f"runs/{100 + day}/jobs?filter=all&per_page=100&page=1"] = {"jobs": jobs}
    return opener


def run_cli(tmp_path: Path, opener: FakeOpener, *extra: str) -> int:
    args = ["--repo", REPO, "--out-dir", str(tmp_path / "out"), "--summary", str(tmp_path / "summary.md")]
    return main([*args, "--workflows-dir", str(workflows_dir()), "--window", "5", *extra], opener=opener, now=NOW)


def test_the_cli_writes_the_json_the_html_and_the_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", SECRET)
    opener = main_history_api()
    assert run_cli(tmp_path, opener) == 0
    data = json.loads((tmp_path / "out" / "ci-durations.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == 1 and data["since"] == "2026-10-21" and data["days"] == 30
    assert data["run_count"] == 8
    latest = next(row for row in data["rows"] if row["run_id"] == 106)
    assert latest["duration_seconds"] == 300.0 and latest["baseline_median"] == 100.0 and latest["ceiling"] == 600
    fields = {k: v for k, v in latest.items() if k not in ("is_rerun", "job_key", "baseline_median", "ceiling")}
    assert JobDuration(**fields).run_url.endswith("/actions/runs/106/job/1006")
    assert (tmp_path / "out" / "ci-durations.html").read_text(encoding="utf-8").startswith("<!doctype html>")
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "| Test - Unit / unit | ubuntu-latest | 300 s | 100 s | +200 s | 3.00x | 600 s |" in summary
    assert all(url.startswith(API) for url in opener.urls)


def test_the_token_never_reaches_an_output(tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", SECRET)
    opener = main_history_api()
    opener.responses["runs/2/jobs?filter=all&per_page=100&page=1"] = urllib.error.URLError("refused")
    assert run_cli(tmp_path, opener) == 0
    captured = capsys.readouterr()
    assert "::warning::Could not read the jobs of run 2" in captured.out
    for text in (
        (tmp_path / "out" / "ci-durations.json").read_text(encoding="utf-8"),
        (tmp_path / "out" / "ci-durations.html").read_text(encoding="utf-8"),
        (tmp_path / "summary.md").read_text(encoding="utf-8"),
        captured.out,
        captured.err,
    ):
        assert SECRET not in text


def test_a_missing_token_exits_2_and_writes_nothing(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    opener = main_history_api()
    assert run_cli(tmp_path, opener) == 2
    assert "::error::GITHUB_TOKEN" in capsys.readouterr().out
    assert not (tmp_path / "out").exists() and not (tmp_path / "summary.md").exists()
    assert opener.urls == []


def test_a_failed_run_listing_exits_1_and_writes_nothing(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", SECRET)
    assert run_cli(tmp_path, FakeOpener({})) == 1
    assert "::error::Could not list the workflow runs" in capsys.readouterr().out
    assert not (tmp_path / "out").exists()


def test_the_summary_says_when_no_job_has_a_baseline(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", SECRET)
    assert run_cli(tmp_path, recorded_api()) == 0
    assert "No job has a baseline yet." in (tmp_path / "summary.md").read_text(encoding="utf-8")


def test_the_parser_defaults_to_30_days_and_the_step_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", "/tmp/step-summary.md")
    args = build_parser().parse_args([])
    assert args.days == 30 and args.summary == "/tmp/step-summary.md"


def test_days_must_be_positive(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", SECRET)
    assert run_cli(tmp_path, recorded_api(), "--days", "0") == 2


def test_the_workflow_is_read_only_and_runs_on_schedule_and_dispatch() -> None:
    text = (workflows_dir() / "ci-durations.yml").read_text(encoding="utf-8")
    triggers = parse_triggers(text)
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    block = re.search(r"^permissions:\n((?:  .*\n)+)", text, re.MULTILINE)
    assert block is not None
    assert sorted(line.strip() for line in block.group(1).splitlines()) == ["actions: read", "contents: read"]
    assert text.count("permissions:") == 1
    assert "write" not in re.sub(r"#.*", "", text)


def test_the_workflow_never_interpolates_an_input_into_a_shell_line() -> None:
    text = (workflows_dir() / "ci-durations.yml").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip().startswith("run:"):
            assert "${{" not in line, line
    assert "python3 -m scripts.ci_durations" in text
    assert "actions/upload-artifact" in text and "name: ci-durations" in text
