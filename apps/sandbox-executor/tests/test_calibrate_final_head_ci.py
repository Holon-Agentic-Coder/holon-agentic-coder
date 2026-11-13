"""Stage 5's final-head CI report: every outcome from fakes, with no real gh, docker, git remote or model call."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sandbox_executor.flow import FlowContext, StageStatus
from sandbox_executor.flow.github import ci_final_head
from sandbox_executor.flow.github.ci_final_head import (
    FinalHeadSeams,
    check_final_head_ci,
    format_final_head_ci,
    gh_step_lookup,
    parse_run_id,
)
from sandbox_executor.flow.github.ci_wait import CiWaitPolicy
from sandbox_executor.flow.stages.calibrate import run_calibrate_stage

BRANCH = "I-1-x/P-2-y/E-3-z/_"
GATE = "CI Timing Gate (Check <= 5 min)"


def _status(checks: list[dict], source: str = "github_cli") -> dict:
    failed = sum(c["bucket"] == "fail" for c in checks)
    pending = sum(c["bucket"] == "pending" for c in checks)
    return {
        "source": source,
        "total": len(checks),
        "failed": failed,
        "pending": pending,
        "all_passed": bool(checks) and not failed and not pending,
        "checks": checks,
    }


def _check(name: str, bucket: str) -> dict:
    return {"name": name, "bucket": bucket, "link": f"https://github.com/o/r/actions/runs/42/job/{name}"}


class Fakes:
    def __init__(self, *statuses: dict, steps: dict[str, list[str] | None] | None = None) -> None:
        self.statuses = list(statuses)
        self.probes = 0
        self.now = 0.0
        self.sleeps: list[float] = []
        self.steps = steps or {}

    def probe(self) -> dict:
        self.probes += 1
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def seams(self, budget: float = 600.0) -> FinalHeadSeams:
        return FinalHeadSeams(
            policy=CiWaitPolicy(budget, clock=lambda: self.now, sleep=self.sleep),
            probe=self.probe,
            step_lookup=lambda check: self.steps.get(check["name"]),
            registration_timeout=0,
            pushed_head=lambda: None,
        )


def _ctx(tmp_path: Path, **kwargs: bool) -> FlowContext:
    return FlowContext(repo_dir=str(tmp_path), execution_branch=BRANCH, agent="a", model="m", **kwargs)


def _run(tmp_path: Path, fakes: Fakes, budget: float = 600.0, **kwargs: bool) -> dict:
    return check_final_head_ci(_ctx(tmp_path, **kwargs), str(tmp_path), BRANCH, fakes.seams(budget))


def test_all_green_is_success(tmp_path: Path) -> None:
    record = _run(tmp_path, Fakes(_status([_check("unit", "pass")])))
    assert record["state"] == "success"
    assert format_final_head_ci(record) == "final head CI: success"


def test_failing_test_job_lists_its_name(tmp_path: Path) -> None:
    fakes = Fakes(_status([_check("unit", "fail")]), steps={"unit": ["Run tests"]})
    record = _run(tmp_path, fakes)
    assert record["state"] == "failed"
    assert record["failed_checks"] == ["unit"]
    assert "unit" in format_final_head_ci(record)


def test_timing_gate_only_failure_is_reported_separately(tmp_path: Path) -> None:
    fakes = Fakes(_status([_check("macos-unit", "fail")]), steps={"macos-unit": [GATE]})
    record = _run(tmp_path, fakes)
    assert record["state"] == "timing_gate"
    assert record["timing_gate_checks"] == ["macos-unit"]
    assert record["failed_checks"] == []
    assert format_final_head_ci(record) == "final head CI: timing gate only: macos-unit"


def test_mixed_failure_is_a_test_failure(tmp_path: Path) -> None:
    fakes = Fakes(_status([_check("unit", "fail")]), steps={"unit": [GATE, "Run tests"]})
    assert _run(tmp_path, fakes)["state"] == "failed"


def test_unknown_steps_are_a_plain_failure(tmp_path: Path) -> None:
    assert _run(tmp_path, Fakes(_status([_check("unit", "fail")])))["state"] == "failed"


def test_pending_then_green_within_budget_is_success(tmp_path: Path) -> None:
    fakes = Fakes(_status([_check("unit", "pending")]), _status([_check("unit", "pass")]))
    assert _run(tmp_path, fakes)["state"] == "success"
    assert fakes.sleeps


def test_pending_past_budget_is_unknown_not_success(tmp_path: Path) -> None:
    fakes = Fakes(_status([_check("unit", "pending")]))
    record = _run(tmp_path, fakes, budget=30.0)
    assert record["state"] == "unknown"
    assert "unit" in record["reason"]
    assert record["ci_wait"]["state"] == "pending"


def test_local_tests_source_is_unknown(tmp_path: Path) -> None:
    fakes = Fakes(_status([_check("local_tests", "pass")], source="local_tests"))
    with patch.object(ci_final_head, "_wait_for_ci_registration"):
        assert _run(tmp_path, fakes)["state"] == "unknown"


def test_dry_run_and_skip_push_do_not_probe_or_wait(tmp_path: Path) -> None:
    for flag in ({"dry_run": True}, {"skip_push": True}):
        fakes = Fakes(_status([_check("unit", "pending")]))
        assert _run(tmp_path, fakes, **flag)["state"] == "skipped"
        assert fakes.probes == 0
        assert fakes.sleeps == []


def test_probe_exception_is_unknown(tmp_path: Path) -> None:
    fakes = Fakes(_status([]))
    fakes.probe = MagicMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    record = _run(tmp_path, fakes)
    assert record["state"] == "unknown"
    assert "boom" in record["reason"]


def test_parse_run_id_and_gh_step_lookup() -> None:
    assert parse_run_id("https://github.com/o/r/actions/runs/123/job/9") == "123"
    assert parse_run_id("https://example.com") is None
    steps = [{"name": "s", "conclusion": "success"}, {"name": "g", "conclusion": "failure"}]
    jobs = json.dumps({"jobs": [{"name": "u", "steps": steps}]})
    run = MagicMock(return_value=SimpleNamespace(returncode=0, stdout=jobs))
    lookup = gh_step_lookup("/tmp", {}, run)
    assert lookup({"name": "u", "link": "https://x/actions/runs/7/job/1"}) == ["g"]
    assert lookup({"name": "u", "link": "nope"}) is None
    run.side_effect = OSError("no gh")
    assert lookup({"name": "u", "link": "https://x/actions/runs/7/job/1"}) is None


def test_stage_keeps_success_and_reports_failed_final_head(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.target_worktree = str(tmp_path)
    failed = {"state": "failed", "failed_checks": ["unit"], "timing_gate_checks": [], "ci_wait": None, "reason": ""}
    report = MagicMock(calibrated_branch=BRANCH + "calibrated")
    report.to_dict.return_value = {}
    ok = SimpleNamespace(returncode=0, stdout="", stderr="")
    with (
        patch("sandbox_executor.flow.stages.calibrate._checkout_execution_branch", return_value=None),
        patch("sandbox_executor.flow.stages.calibrate.check_and_resolve_stage_conflicts"),
        patch("sandbox_executor.flow.stages.calibrate.run_calibrate", return_value=report),
        patch("sandbox_executor.flow.stages.calibrate.run_git", return_value=ok),
        patch("sandbox_executor.flow.calibrate_sync.run_git", return_value=ok),
        patch("sandbox_executor.flow.stages.calibrate.check_final_head_ci", return_value=failed),
    ):
        result = run_calibrate_stage(ctx)
    assert result.status == StageStatus.SUCCESS
    assert result.payload["final_head_ci"] == failed


def test_stale_prior_head_checks_are_not_reported_as_success(tmp_path: Path) -> None:
    fakes = Fakes(_status([_check("hygiene", "pass")]))
    heads = iter(["old", "old", "new"])
    seams = FinalHeadSeams(
        policy=CiWaitPolicy(600.0, clock=lambda: fakes.now, sleep=fakes.sleep),
        probe=fakes.probe,
        step_lookup=lambda check: None,
        registration_timeout=60,
        pushed_head=lambda: "new",
        pr_head=lambda: next(heads),
    )
    record = check_final_head_ci(_ctx(tmp_path), str(tmp_path), BRANCH, seams)
    assert record["state"] == "success"
    assert len(fakes.sleeps) == 2
    assert fakes.probes >= 1


def test_pr_head_never_matching_pushed_head_is_unknown(tmp_path: Path) -> None:
    fakes = Fakes(_status([_check("hygiene", "pass")]))
    seams = FinalHeadSeams(
        policy=CiWaitPolicy(600.0, clock=lambda: fakes.now, sleep=fakes.sleep),
        probe=fakes.probe,
        registration_timeout=20,
        pushed_head=lambda: "new",
        pr_head=lambda: "old",
    )
    record = check_final_head_ci(_ctx(tmp_path), str(tmp_path), BRANCH, seams)
    assert record["state"] == "unknown"
    assert fakes.probes == 0


def test_failure_does_not_end_the_wait_while_other_checks_are_pending(tmp_path: Path) -> None:
    fakes = Fakes(
        _status([_check("lint", "fail"), _check("unit", "pending")]),
        _status([_check("lint", "fail"), _check("unit", "fail")]),
    )
    record = _run(tmp_path, fakes)
    assert record["state"] == "failed"
    assert record["failed_checks"] == ["lint", "unit"]
    assert fakes.sleeps
