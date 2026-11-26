"""The final-head CI check measures the pushed commit itself when the PR head lags, with a fake clock and no real gh."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from sandbox_executor.flow import FlowContext
from sandbox_executor.flow.constants import CI_REGISTRATION_POLL_SECONDS
from sandbox_executor.flow.github import ci_final_head
from sandbox_executor.flow.github.ci_final_head import FinalHeadSeams, check_final_head_ci, format_final_head_ci
from sandbox_executor.flow.github.ci_wait import CiWaitPolicy

from tests.flow.github.fake_github import FakeGitHub

BRANCH = "I-1-x/P-2-y/E-3-z/_"
PUSHED = "a" * 40
STALE = "b" * 40
REGISTRATION_TIMEOUT = 180.0
LAGGED = "pull request head lagged the pushed head"
NO_CHECKS = "no checks registered for the pushed head"


def _run(name: str, status: str = "completed", conclusion: str | None = "success", sha: str = PUSHED) -> dict:
    return {
        "id": 1,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "head_sha": sha,
        "details_url": "https://github.com/o/r/actions/runs/7/job/1",
        "check_suite_id": 9,
    }


class Scenario:
    """Scripted commit check runs, PR heads and a stale-green branch probe on a fake clock."""

    def __init__(self, *check_runs: list[dict], pr_heads: list[str | None] | None = None) -> None:
        self.check_runs = list(check_runs) or [[]]
        self.pr_heads = list(pr_heads or [STALE])
        self.now = 0.0
        self.sleeps: list[float] = []
        self.api_calls: list[list[str]] = []
        self.branch_probes = 0
        self.reruns: list[str] = []
        self.github = FakeGitHub(cli=self.cli)

    def cli(self, args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
        self.api_calls.append(args)
        runs = self.check_runs.pop(0) if len(self.check_runs) > 1 else self.check_runs[0]
        stdout = "".join(json.dumps(run) + "\n" for run in runs)
        return subprocess.CompletedProcess(["gh", *args], 0, stdout, "")

    def pr_head(self) -> str | None:
        return self.pr_heads.pop(0) if len(self.pr_heads) > 1 else self.pr_heads[0]

    def branch_probe(self) -> dict:
        self.branch_probes += 1
        checks = [{"name": "stale", "bucket": "pass", "state": "SUCCESS"}]
        return {"source": "github_cli", "total": 1, "failed": 0, "pending": 0, "all_passed": True, "checks": checks}

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def rerun(self, run_id: str) -> bool:
        self.reruns.append(run_id)
        return True

    def seams(self, **overrides: object) -> FinalHeadSeams:
        fields = {
            "policy": CiWaitPolicy(600.0, clock=lambda: self.now, sleep=self.sleep),
            "probe": self.branch_probe,
            "detail_lookup": lambda check: None,
            "registration_timeout": REGISTRATION_TIMEOUT,
            "pushed_head": lambda: PUSHED,
            "pr_head": self.pr_head,
            "rerun_dispatcher": self.rerun,
            "github": self.github,
        }
        return FinalHeadSeams(**{**fields, **overrides})  # type: ignore[arg-type]


def _check(tmp_path: Path, scenario: Scenario, **overrides: object) -> dict:
    ctx = FlowContext(repo_dir=str(tmp_path), execution_branch=BRANCH, agent="a", model="m")
    return check_final_head_ci(ctx, str(tmp_path), BRANCH, scenario.seams(**overrides))


def test_lagging_head_with_registered_passing_checks_is_success_without_waiting(tmp_path: Path) -> None:
    scenario = Scenario([_run("unit")])
    record = _check(tmp_path, scenario)
    assert record["state"] == "success"
    assert scenario.branch_probes == 0
    assert sum(scenario.sleeps) == 0
    assert len(scenario.api_calls) == 1
    assert f"repos/{{owner}}/{{repo}}/commits/{PUSHED}/check-runs" in scenario.api_calls[0]
    assert set(scenario.github.methods()) == {"gh"}


def test_lagging_head_reports_the_pushed_commits_failure_not_the_stale_green(tmp_path: Path) -> None:
    scenario = Scenario([_run("unit", conclusion="failure"), _run("other", sha=STALE)])
    record = _check(tmp_path, scenario)
    assert record["state"] == "failed"
    assert record["failed_checks"] == ["unit"]
    assert scenario.branch_probes == 0


def test_lagging_head_pending_commit_checks_settle_through_the_wait_policy(tmp_path: Path) -> None:
    scenario = Scenario([_run("unit", status="in_progress", conclusion=None)], [_run("unit")])
    record = _check(tmp_path, scenario)
    assert record["state"] == "success"
    assert record["ci_wait"] is not None
    assert scenario.branch_probes == 0


def test_lagging_head_without_commit_checks_retries_once_then_reports_the_lag(tmp_path: Path) -> None:
    scenario = Scenario([])
    record = _check(tmp_path, scenario)
    assert record["state"] == "unknown"
    assert LAGGED in record["reason"]
    assert NO_CHECKS in record["reason"]
    assert f"{STALE[:8]} vs {PUSHED[:8]}" in record["reason"]
    assert len(scenario.api_calls) == 2
    assert scenario.sleeps == [CI_REGISTRATION_POLL_SECONDS]
    assert scenario.now < REGISTRATION_TIMEOUT
    assert scenario.branch_probes == 0
    assert scenario.reruns == []
    assert "success" not in format_final_head_ci(record)


def test_lagging_head_failed_check_keeps_its_real_details_url(tmp_path: Path) -> None:
    scenario = Scenario([_run("unit", conclusion="failure")])
    record, ci_status = ci_final_head._classify_settled(
        ci_final_head._FinalHeadCheck(
            FlowContext(repo_dir=str(tmp_path), execution_branch=BRANCH, agent="a", model="m"),
            str(tmp_path),
            BRANCH,
            scenario.seams(),
            scenario.github,
        )
    )
    assert record["state"] == "failed"
    assert ci_status["checks"][0]["link"] == "https://github.com/o/r/actions/runs/7/job/1"


def test_lagging_head_gh_failure_reports_a_lookup_failure_not_missing_checks(tmp_path: Path) -> None:
    scenario = Scenario([])
    calls: list[list[str]] = []

    def failing_cli(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(["gh", *args], 1, "", "HTTP 403: API rate limit exceeded\nmore")

    scenario.github = FakeGitHub(cli=failing_cli)
    record = _check(tmp_path, scenario)
    assert record["state"] == "unknown"
    assert LAGGED in record["reason"]
    assert "check-runs lookup failed for the pushed head: HTTP 403: API rate limit exceeded" in record["reason"]
    assert NO_CHECKS not in record["reason"]
    assert len(calls) == 2


def test_lagging_head_that_caught_up_on_the_fresh_read_takes_the_branch_path(tmp_path: Path) -> None:
    scenario = Scenario([], pr_heads=[STALE, PUSHED])
    record = _check(tmp_path, scenario)
    assert record["state"] == "success"
    assert len(scenario.api_calls) == 1
    assert scenario.branch_probes == 1


def test_matching_heads_without_registered_checks_report_no_checks(tmp_path: Path) -> None:
    scenario = Scenario(pr_heads=[PUSHED])
    unregistered = {"source": "local_tests", "total": 1, "pending": 0, "all_passed": True, "checks": []}
    probes: list[dict] = []

    def probe() -> dict:
        probes.append(unregistered)
        return unregistered

    with patch.object(ci_final_head, "_wait_for_ci_registration") as registration_wait:
        record = _check(tmp_path, scenario, probe=probe)
    assert registration_wait.call_count == 1
    assert record["state"] == "unknown"
    assert NO_CHECKS in record["reason"]
    assert "lagged" not in record["reason"]
    assert len(probes) == 2
    assert scenario.api_calls == []
    assert scenario.reruns == []
    assert "success" not in format_final_head_ci(record)
