"""Stage 4 reruns a noise failure once inside the iteration that saw it; a defect or an objection never reruns."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from sandbox_executor.flow import FlowContext
from sandbox_executor.flow.github.ci_wait import CiWaitPolicy
from sandbox_executor.flow.review.ci_failure_routing import DEFECT, NOISE_TIMING, RESOLVE, _last_routing, _reruns_spent
from sandbox_executor.flow.review.ci_inspection import CiInspectionInputs, inspect_ci
from sandbox_executor.flow.review.ci_noise_rerun import rerun_noise_once
from sandbox_executor.flow.review.ci_outcome import describe_ci_outcome

HEAD = "a" * 40
LINK = "https://github.com/o/r/actions/runs/11/job/5"
RED = {
    "source": "github_cli",
    "all_passed": False,
    "passed": 0,
    "failed": 1,
    "pending": 0,
    "total": 1,
    "checks": [{"name": "test", "bucket": "fail", "state": "FAILURE", "link": LINK}],
}
GREEN = {
    "source": "github_cli",
    "all_passed": True,
    "passed": 1,
    "failed": 0,
    "pending": 0,
    "total": 1,
    "checks": [{"name": "test", "bucket": "pass", "state": "SUCCESS", "link": LINK}],
}


class _TestGate:
    """A Stage 3 evidence fake whose tests passed or failed."""

    def __init__(self, tests_failed: bool = False) -> None:
        self.tests_failed = tests_failed

    def evidence(self, ci_status: dict[str, Any], dry_run: bool = False) -> tuple[float, bool]:
        del ci_status, dry_run
        return (0.0 if self.tests_failed else 1.0), self.tests_failed

    def needs_ci_registration(self) -> bool:
        return False


class _Ci:
    """A pipeline that reports ``before`` until a rerun is dispatched, then ``after``."""

    def __init__(self, after: dict[str, Any], ok: bool = True) -> None:
        self.after, self.ok, self.reruns = after, ok, []

    def probe(self) -> dict[str, Any]:
        return dict(self.after if self.reruns and self.ok else RED)

    def rerun(self, run_id: str) -> bool:
        self.reruns.append(run_id)
        return self.ok


def _detail(steps: list[str]) -> Any:
    gate = all(s.startswith("CI Timing Gate") for s in steps)
    return lambda check: {"name": check["name"], "failed_steps": steps, "duration_seconds": 1.0, "timing_gate": gate}


class _Clock:
    """A fake clock that moves only when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _inputs(ci: _Ci, steps: list[str] | None = None, **kwargs: Any) -> CiInspectionInputs:
    clock = _Clock()
    base: dict[str, Any] = {
        "repo_dir": "/repo",
        "review_target": "feature",
        "proxy_env": {},
        "github": MagicMock(available=True),
        "test_gate": _TestGate(),
        "ci_policy": CiWaitPolicy(60.0, 1.0, clock=clock, sleep=clock.sleep),
        "probe_ci": ci.probe,
        "wait_registration": MagicMock(),
        "pr_allowed": True,
        "ci_registration_timeout": 30.0,
        "ci_poll_interval": 1.0,
        "clock": clock,
        "sleep": clock.sleep,
        "detail_lookup": _detail(steps or ["CI Timing Gate (fast suite)"]),
        "rerun_failed": ci.rerun,
    }
    return CiInspectionInputs(**{**base, **kwargs})


def _ctx(tmp_path: Path) -> FlowContext:
    (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True, exist_ok=True)
    return FlowContext(repo_dir=str(tmp_path), execution_branch="I-1-x/P-1-A/E-1-A/_", agent="a", model="m")


def test_a_timing_failure_after_a_clean_review_is_rerun_once_and_inspected_again(tmp_path: Path) -> None:
    ctx, ci = _ctx(tmp_path), _Ci(GREEN)
    inputs = _inputs(ci)
    after = rerun_noise_once(ctx, inputs, inspect_ci(inputs, ctx), HEAD)
    assert ci.reruns == ["11"]
    assert after.ci_green is True and not after.ci_finding
    assert _reruns_spent(ctx, HEAD, "11") == 1
    assert any("CI noise (timing gate)" in line and "the diff approval stands" in line for line in ctx.logs)


def test_noise_still_red_after_its_rerun_is_returned_red_and_the_budget_is_spent(tmp_path: Path) -> None:
    ctx, ci = _ctx(tmp_path), _Ci(RED)
    inputs = _inputs(ci)
    after = rerun_noise_once(ctx, inputs, inspect_ci(inputs, ctx), HEAD)
    assert ci.reruns == ["11"] and after.ci_green is False and after.ci_finding
    assert _last_routing(ctx) == {**(_last_routing(ctx) or {}), "classification": NOISE_TIMING, "decision": RESOLVE}
    assert any("still red after its rerun" in line for line in ctx.logs)
    again = rerun_noise_once(ctx, inputs, after, HEAD)
    assert ci.reruns == ["11"] and again is after
    assert _last_routing(ctx) == {**(_last_routing(ctx) or {}), "classification": NOISE_TIMING, "decision": RESOLVE}


def test_a_defect_is_never_rerun(tmp_path: Path) -> None:
    ctx, ci = _ctx(tmp_path), _Ci(GREEN)
    inputs = _inputs(ci, ["Run tests"])
    first = inspect_ci(inputs, ctx)
    assert rerun_noise_once(ctx, inputs, first, HEAD) is first
    assert ci.reruns == []
    assert (_last_routing(ctx) or {}).get("classification") == DEFECT


def test_a_reviewer_objection_local_test_failure_or_dry_run_never_reruns(tmp_path: Path) -> None:
    ctx, ci = _ctx(tmp_path), _Ci(GREEN)
    for flags in ({"reviewer_objected": True}, {"test_gate": _TestGate(True)}, {"effective_dry_run": True}):
        inputs = _inputs(ci, **flags)
        first = inspect_ci(inputs, ctx)
        assert rerun_noise_once(ctx, inputs, first, HEAD) is first
    assert rerun_noise_once(ctx, _inputs(ci), inspect_ci(_inputs(ci), ctx), None).ci_green is False
    assert ci.reruns == [] and _last_routing(ctx) is None


def test_a_failed_dispatch_falls_back_to_the_resolver(tmp_path: Path) -> None:
    ctx, ci = _ctx(tmp_path), _Ci(GREEN, ok=False)
    inputs = _inputs(ci)
    first = inspect_ci(inputs, ctx)
    assert rerun_noise_once(ctx, inputs, first, HEAD) is first
    assert (_last_routing(ctx) or {}).get("decision") == RESOLVE
    assert any("could not be dispatched" in line for line in ctx.logs)


def test_a_partial_dispatch_failure_records_no_inspected_rerun(tmp_path: Path) -> None:
    ctx, ci = _ctx(tmp_path), _Ci(GREEN)
    second = {
        "name": "lint",
        "bucket": "fail",
        "state": "FAILURE",
        "link": "https://github.com/o/r/actions/runs/12/job/6",
    }
    two_red = {**RED, "failed": 2, "total": 2, "checks": [*RED["checks"], second]}
    dispatched: list[str] = []

    def rerun(run_id: str) -> bool:
        dispatched.append(run_id)
        return run_id == "11"

    inputs = _inputs(ci, probe_ci=lambda: dict(two_red), rerun_failed=rerun)
    first = inspect_ci(inputs, ctx)
    assert rerun_noise_once(ctx, inputs, first, HEAD) is first
    assert dispatched == ["11", "12"] and _reruns_spent(ctx, HEAD, "11") == 1
    last = _last_routing(ctx) or {}
    assert last.get("decision") == RESOLVE and last.get("reruns") == 0
    assert "still red after" not in describe_ci_outcome(two_red, None, False, routing=last)


def test_the_rerun_start_wait_is_credited_to_the_iteration_timer(tmp_path: Path) -> None:
    ctx, ci, timer = _ctx(tmp_path), _Ci(GREEN), MagicMock()
    probes = iter([RED, RED])  # Two polls still red: two 5 s sleeps before the rerun starts.
    inputs = _inputs(ci, iteration_timer=timer)
    first = inspect_ci(inputs, ctx)
    late = _inputs(ci, probe_ci=lambda: dict(next(probes, GREEN)), iteration_timer=timer, rerun_failed=ci.rerun)
    assert rerun_noise_once(ctx, late, first, HEAD).ci_green is True
    timer.credit.assert_any_call(10.0)
