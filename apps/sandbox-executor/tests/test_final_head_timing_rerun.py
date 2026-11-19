"""The one automatic rerun of a timing-gate-only failure on a bookkeeping-only head, from FinalHeadSeams fakes only."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from sandbox_executor.flow import FlowContext
from sandbox_executor.flow.context import FlowStage, StageResult, StageStatus
from sandbox_executor.flow.github import ci_reverify
from sandbox_executor.flow.github.ci_final_head import FinalHeadSeams, check_final_head_ci
from sandbox_executor.flow.github.ci_reverify import reverify_and_report, reverify_final_head
from sandbox_executor.flow.github.ci_wait import CiWaitPolicy

BRANCH = "I-1-x/P-2-y/E-3-z/_"
GATE = "CI Timing Gate (compare with main)"
APPROVED = "a" * 40
FINAL = "b" * 40
BOOKKEEPING = ["plans/P-1/plan.md", "executions/E-1.md", "holon-knowledge/notes/noise.md"]


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


def _check(name: str, bucket: str, link: str | None = None) -> dict:
    link = link if link is not None else f"https://github.com/o/r/actions/runs/42/job/{name}"
    return {"name": name, "bucket": bucket, "link": link}


def _detail(name: str, steps: list[str] | None, seconds: float | None = 360.0) -> dict | None:
    if steps is None:
        return None
    gate = bool(steps) and all(s.startswith("CI Timing Gate") for s in steps)
    return {"name": name, "failed_steps": steps, "duration_seconds": seconds, "timing_gate": gate}


class RerunFakes:
    """Probe, detail lookup, clock, rerun dispatcher and diff lister for check_final_head_ci, all hermetic."""

    def __init__(
        self,
        *statuses: dict,
        steps: dict[str, list[str] | None] | None = None,
        paths: list[str] | None = BOOKKEEPING,
        dispatch_ok: bool = True,
        approved: str | None = APPROVED,
        final: str | None = FINAL,
    ) -> None:
        self.statuses = list(statuses)
        self.probes = 0
        self.now = 0.0
        self.sleeps: list[float] = []
        self.steps = steps or {}
        self.paths = paths
        self.dispatch_ok = dispatch_ok
        self.approved = approved
        self.final = final
        self.dispatched: list[str] = []
        self.diff_calls: list[tuple[str, str]] = []

    def probe(self) -> dict:
        self.probes += 1
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def rerun(self, run_id: str) -> bool:
        self.dispatched.append(run_id)
        return self.dispatch_ok

    def changed(self, approved: str, head: str) -> list[str] | None:
        self.diff_calls.append((approved, head))
        return self.paths

    def seams(self, registration_timeout: float = 0.0) -> FinalHeadSeams:
        return FinalHeadSeams(
            policy=CiWaitPolicy(600.0, clock=lambda: self.now, sleep=self.sleep),
            probe=self.probe,
            detail_lookup=lambda check: _detail(check["name"], self.steps.get(check["name"])),
            registration_timeout=registration_timeout,
            pushed_head=lambda: None,
            pr_head=lambda: self.final,
            rerun_dispatcher=self.rerun,
            changed_paths=self.changed,
            approved_head=lambda: self.approved,
        )


def _ctx(tmp_path: Path) -> FlowContext:
    context = FlowContext(repo_dir=str(tmp_path), execution_branch=BRANCH, agent="a", model="m")
    context.stage_results["review"] = StageResult(
        stage=FlowStage.REVIEW,
        status=StageStatus.HALTED_FOR_HUMAN,
        payload={
            "consensus": {"approved": True, "target_commit": APPROVED, "pr_number": 7},
            "posting": {"dual_posted": True},
        },
    )
    return context


def _run(tmp_path: Path, fakes: RerunFakes, registration_timeout: float = 0.0) -> dict:
    return check_final_head_ci(_ctx(tmp_path), str(tmp_path), BRANCH, fakes.seams(registration_timeout))


def _timing_failure(**kwargs: object) -> RerunFakes:
    """A timing-gate-only failed probe that every later probe repeats (the rerun fails again)."""
    return RerunFakes(_status([_check("macos-unit", "fail")]), steps={"macos-unit": [GATE]}, **kwargs)


def test_passing_rerun_turns_the_record_green_and_keeps_the_approval(tmp_path: Path) -> None:
    fakes = RerunFakes(
        _status([_check("macos-unit", "fail")]),
        _status([_check("macos-unit", "pass")]),
        steps={"macos-unit": [GATE]},
    )
    record = _run(tmp_path, fakes)
    assert record["state"] == "success"
    assert record["timing_rerun"] == {"attempted": True, "run_ids": ["42"], "outcome": "success"}
    assert fakes.dispatched == ["42"]
    assert fakes.diff_calls == [(APPROVED, FINAL)]
    context = _ctx(tmp_path)
    with (
        patch.object(ci_reverify, "published_head_of", return_value=FINAL),
        patch.object(ci_reverify, "invalidate_approval") as invalidate,
    ):
        reverified = reverify_and_report(context, str(tmp_path), record, pushed=True)
    assert reverified["verdict"] == "green"
    assert "after one automatic rerun of the timing gate" in reverified["report"]
    invalidate.assert_not_called()


def test_second_timing_failure_applies_the_existing_withdrawal(tmp_path: Path) -> None:
    fakes = _timing_failure()
    record = _run(tmp_path, fakes)
    assert record["state"] == "failed"
    assert record["failed_checks"] == ["macos-unit"]
    assert record["timing_rerun"] == {"attempted": True, "run_ids": ["42"], "outcome": "failed"}
    assert fakes.dispatched == ["42"]
    context = _ctx(tmp_path)
    with (
        patch.object(ci_reverify, "published_head_of", return_value=FINAL),
        patch.object(ci_reverify, "invalidate_approval", return_value={"posted": True}) as invalidate,
    ):
        reverified = reverify_and_report(context, str(tmp_path), record, pushed=True)
    assert reverified["verdict"] == "red"
    assert invalidate.assert_called_once() is None


def test_code_changing_head_gets_no_rerun(tmp_path: Path) -> None:
    fakes = _timing_failure(paths=[*BOOKKEEPING, "apps/sandbox-executor/src/sandbox_executor/flow/pipeline.py"])
    record = _run(tmp_path, fakes)
    assert record["state"] == "failed"
    assert "timing_rerun" not in record
    assert fakes.dispatched == []


def test_non_timing_and_mixed_failures_get_no_rerun(tmp_path: Path) -> None:
    for steps in ({"macos-unit": ["Run unit tests"]}, {"macos-unit": [GATE, "Run unit tests"]}, {}):
        fakes = RerunFakes(_status([_check("macos-unit", "fail")]), steps=steps)
        record = _run(tmp_path, fakes)
        assert record["state"] == "failed"
        assert "timing_rerun" not in record
        assert fakes.dispatched == []


def test_mixed_timing_and_test_check_failures_get_no_rerun(tmp_path: Path) -> None:
    fakes = RerunFakes(
        _status([_check("macos-unit", "fail"), _check("unit", "fail")]),
        steps={"macos-unit": [GATE], "unit": ["Run unit tests"]},
    )
    record = _run(tmp_path, fakes)
    assert record["state"] == "failed"
    assert "timing_rerun" not in record
    assert fakes.dispatched == []


def test_undecidable_inputs_are_fail_closed(tmp_path: Path) -> None:
    unknown_head = _timing_failure(approved=None)
    assert "timing_rerun" not in _run(tmp_path, unknown_head)
    assert unknown_head.dispatched == []
    assert unknown_head.diff_calls == []

    unresolvable_head = _timing_failure(final=None)
    assert "timing_rerun" not in _run(tmp_path, unresolvable_head)
    assert unresolvable_head.dispatched == []
    assert unresolvable_head.diff_calls == []

    unreadable_diff = _timing_failure(paths=None)
    assert "timing_rerun" not in _run(tmp_path, unreadable_diff)
    assert unreadable_diff.dispatched == []

    empty_diff = _timing_failure(paths=[])
    assert "timing_rerun" not in _run(tmp_path, empty_diff)
    assert empty_diff.dispatched == []

    no_run_id = RerunFakes(
        _status([_check("macos-unit", "fail", link="https://example.com/not-a-run")]),
        steps={"macos-unit": [GATE]},
    )
    assert "timing_rerun" not in _run(tmp_path, no_run_id)
    assert no_run_id.dispatched == []


def test_failed_dispatch_keeps_the_original_record_without_a_reprobe(tmp_path: Path) -> None:
    fakes = _timing_failure(dispatch_ok=False)
    record = _run(tmp_path, fakes)
    assert record["state"] == "failed"
    assert record["timing_rerun"] == {"attempted": False, "reason": "the rerun could not be dispatched"}
    assert fakes.dispatched == ["42"]
    assert fakes.probes == 1
    assert reverify_final_head(record, APPROVED, FINAL)["verdict"] == "red"


def test_checks_that_never_reregister_get_no_second_dispatch(tmp_path: Path) -> None:
    fakes = _timing_failure()
    record = _run(tmp_path, fakes, registration_timeout=60.0)
    assert record["state"] == "failed"
    assert record["timing_rerun"]["outcome"] == "failed"
    assert fakes.dispatched == ["42"]
    assert fakes.sleeps
    assert fakes.now >= 60.0
