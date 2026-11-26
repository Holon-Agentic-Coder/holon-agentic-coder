"""The bounded automatic rerun of a timing-gate-only failure on unchanged code, from FinalHeadSeams fakes only."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor.flow import FlowContext
from sandbox_executor.flow.context import FlowStage, StageResult, StageStatus
from sandbox_executor.flow.github import ci_reverify
from sandbox_executor.flow.github.ci_failure_detail import STEP_LOOKUP_TIMEOUT_SECONDS, failure_detail
from sandbox_executor.flow.github.ci_final_head import FinalHeadSeams, check_final_head_ci
from sandbox_executor.flow.github.ci_reverify import reverify_and_report, reverify_final_head
from sandbox_executor.flow.github.ci_wait import CiWaitPolicy

from tests.flow.github.fake_github import FakeGitHub, ci_checks_payload

BRANCH = "I-1-x/P-2-y/E-3-z/_"
GATE = "CI Timing Gate (compare with main)"
APPROVED = "a" * 40
FINAL = "b" * 40
BOOKKEEPING = ["plans/P-1/plan.md", "executions/E-1.md", "holon-knowledge/notes/noise.md"]
CODE_PATHS = [
    "apps/sandbox-executor/src/sandbox_executor/flow/pipeline.py",
    "apps/sandbox-executor/tests/test_pipeline.py",
    ".github/workflows/test-unit.yml",
    "scripts/ci_timing_gate.py",
]


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
    gate = failure_detail(name, {"steps": [{"name": s, "conclusion": "failure"} for s in steps]})["timing_gate"]
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
    assert record["timing_rerun"] == {
        "attempted": True,
        "run_ids": ["42"],
        "outcome": "success",
        "attempts": 1,
        "outcomes": ["success"],
    }
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


def test_the_existing_withdrawal_applies_after_the_third_failed_rerun(tmp_path: Path) -> None:
    fakes = _timing_failure()
    record = _run(tmp_path, fakes)
    unretried = _run(tmp_path, _timing_failure(paths=[*BOOKKEEPING, CODE_PATHS[0]]))
    assert record["state"] == "failed"
    assert record["failed_checks"] == ["macos-unit"]
    for key in ("state", "failed_checks", "failed_details"):
        assert record[key] == unretried[key]
    assert record["timing_rerun"] == {
        "attempted": True,
        "run_ids": ["42"],
        "outcome": "failed",
        "attempts": 3,
        "outcomes": ["failed", "failed", "failed"],
    }
    assert fakes.dispatched == ["42", "42", "42"]  # No fourth dispatch.
    context = _ctx(tmp_path)
    with (
        patch.object(ci_reverify, "published_head_of", return_value=FINAL),
        patch.object(ci_reverify, "invalidate_approval", return_value={"posted": True}) as invalidate,
    ):
        reverified = reverify_and_report(context, str(tmp_path), record, pushed=True)
    assert reverified["verdict"] == "red"
    assert invalidate.assert_called_once() is None


@pytest.mark.parametrize("paths", [*([path] for path in CODE_PATHS), [*BOOKKEEPING, CODE_PATHS[0]]])
def test_a_head_with_a_changed_code_test_or_workflow_path_gets_no_rerun(tmp_path: Path, paths: list[str]) -> None:
    fakes = _timing_failure(paths=paths)
    record = _run(tmp_path, fakes)
    assert record["state"] == "failed"
    assert "timing_rerun" not in record
    assert fakes.dispatched == []


@pytest.mark.parametrize("paths", [BOOKKEEPING, []])
def test_a_failed_then_passing_rerun_on_unchanged_code_is_green_after_two_attempts(
    tmp_path: Path, paths: list[str]
) -> None:
    failed, passed = _status([_check("macos-unit", "fail")]), _status([_check("macos-unit", "pass")])
    # Initial probe, then per attempt one rerun-start probe and one settled probe.
    fakes = RerunFakes(failed, failed, failed, passed, steps={"macos-unit": [GATE]}, paths=paths)
    record = _run(tmp_path, fakes)
    assert record["state"] == "success"
    assert record["timing_rerun"]["attempts"] == 2
    assert record["timing_rerun"]["outcomes"] == ["failed", "success"]
    assert record["timing_rerun"]["outcome"] == "success"
    assert fakes.dispatched == ["42", "42"]
    assert fakes.probes == 5


def test_a_rerun_that_turns_into_a_mixed_failure_is_not_retried_again(tmp_path: Path) -> None:
    timing = _status([_check("macos-unit", "fail")])
    mixed = _status([_check("macos-unit", "fail"), _check("unit", "fail")])
    fakes = RerunFakes(timing, timing, mixed, steps={"macos-unit": [GATE], "unit": ["Run unit tests"]})
    record = _run(tmp_path, fakes)
    assert record["state"] == "failed"
    assert record["failed_checks"] == ["macos-unit", "unit"]
    assert record["timing_rerun"]["attempts"] == 1
    assert record["timing_rerun"]["outcomes"] == ["failed"]
    assert fakes.dispatched == ["42"]


def test_a_rerun_still_pending_at_the_budget_is_not_retried_again(tmp_path: Path) -> None:
    failed = _status([_check("macos-unit", "fail")])
    fakes = RerunFakes(failed, failed, _status([_check("macos-unit", "pending")]), steps={"macos-unit": [GATE]})
    record = _run(tmp_path, fakes)
    assert record["state"] == "unknown"
    assert record["timing_rerun"]["outcome"] == "unknown"
    assert record["timing_rerun"]["attempts"] == 1
    assert fakes.dispatched == ["42"]


def test_a_code_push_between_attempts_stops_the_loop(tmp_path: Path) -> None:
    class MovingHead(RerunFakes):
        def changed(self, approved: str, head: str) -> list[str] | None:
            self.diff_calls.append((approved, head))
            return BOOKKEEPING if len(self.diff_calls) == 1 else [CODE_PATHS[0]]

    fakes = MovingHead(_status([_check("macos-unit", "fail")]), steps={"macos-unit": [GATE]})
    record = _run(tmp_path, fakes)
    assert record["state"] == "failed"
    assert record["timing_rerun"]["attempts"] == 1
    assert fakes.dispatched == ["42"]
    assert len(fakes.diff_calls) == 2


def test_a_failed_second_dispatch_keeps_the_first_attempt_record(tmp_path: Path) -> None:
    class SecondDispatchFails(RerunFakes):
        def rerun(self, run_id: str) -> bool:
            self.dispatched.append(run_id)
            return len(self.dispatched) == 1

    fakes = SecondDispatchFails(_status([_check("macos-unit", "fail")]), steps={"macos-unit": [GATE]})
    record = _run(tmp_path, fakes)
    assert record["state"] == "failed"
    assert record["timing_rerun"] == {
        "attempted": True,
        "run_ids": ["42"],
        "outcome": "failed",
        "attempts": 1,
        "outcomes": ["failed"],
        "reason": "the rerun could not be dispatched",
    }
    assert fakes.dispatched == ["42", "42"]
    assert fakes.probes == 3  # No probe after the failed dispatch.


def test_a_workspace_survival_timing_gate_failure_is_rerun(tmp_path: Path) -> None:
    fakes = RerunFakes(
        _status([_check("Workspace survival", "fail")]),
        _status([_check("Workspace survival", "pass")]),
        steps={"Workspace survival": ["Timing gate"]},
    )
    record = _run(tmp_path, fakes)
    assert record["state"] == "success"
    assert record["timing_rerun"]["outcomes"] == ["success"]
    assert fakes.dispatched == ["42"]


@pytest.mark.parametrize(
    ("steps", "gate"),
    [
        (["Timing gate"], True),
        (["CI Timing Gate (Check <= 5 min)"], True),
        (["Timing gate", "Run stress tests"], False),
        (["Timing gate check"], False),
        ([], False),
    ],
)
def test_failure_detail_recognises_both_timing_gate_step_names(steps: list[str], gate: bool) -> None:
    job = {"steps": [{"name": s, "conclusion": "failure"} for s in steps]}
    assert failure_detail("Workspace survival", job)["timing_gate"] is gate


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


def test_checks_that_never_reregister_stop_after_the_bounded_dispatches(tmp_path: Path) -> None:
    fakes = _timing_failure()
    record = _run(tmp_path, fakes, registration_timeout=60.0)
    assert record["state"] == "failed"
    assert record["timing_rerun"]["outcome"] == "failed"
    assert fakes.dispatched == ["42", "42", "42"]
    assert fakes.sleeps
    assert fakes.now >= 180.0


def test_an_injected_adapter_reads_the_pr_head_probes_ci_and_dispatches_the_rerun(tmp_path: Path) -> None:
    """With ``FinalHeadSeams(github=...)``, the PR-head read, the CI probes and the rerun go through the adapter."""
    fakes = RerunFakes(_status([]), steps={"macos-unit": [GATE]})
    github = FakeGitHub(
        checks_sequence=[ci_checks_payload(failed=1, names=["macos-unit"]), ci_checks_payload(passed=1)],
        head_sha=FINAL,
    )
    seams = dataclasses.replace(fakes.seams(), probe=None, pr_head=None, rerun_dispatcher=None, github=github)
    record = check_final_head_ci(_ctx(tmp_path), str(tmp_path), BRANCH, seams)
    assert record["state"] == "success"
    assert record["timing_rerun"] == {
        "attempted": True,
        "run_ids": ["100"],
        "outcome": "success",
        "attempts": 1,
        "outcomes": ["success"],
    }
    assert ("pr_head_sha", (BRANCH, STEP_LOOKUP_TIMEOUT_SECONDS)) in github.calls
    assert ("rerun_failed", ("100", STEP_LOOKUP_TIMEOUT_SECONDS)) in github.calls
    assert fakes.diff_calls == [(APPROVED, FINAL)]
    assert set(github.methods()) == {"ci_checks", "pr_head_sha", "rerun_failed"}


def test_an_adapter_whose_rerun_raises_is_a_failed_dispatch_not_an_unknown_record(tmp_path: Path) -> None:
    class RaisingRerun(FakeGitHub):
        def rerun_failed(self, run_id: str, timeout: float = 60) -> bool:
            self.calls.append(("rerun_failed", (run_id, timeout)))
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    fakes = _timing_failure()
    github = RaisingRerun(head_sha=FINAL)
    seams = dataclasses.replace(fakes.seams(), rerun_dispatcher=None, github=github)
    record = check_final_head_ci(_ctx(tmp_path), str(tmp_path), BRANCH, seams)
    assert record["state"] == "failed"
    assert record["timing_rerun"] == {"attempted": False, "reason": "the rerun could not be dispatched"}
    assert github.methods() == ["rerun_failed"]
    assert fakes.probes == 1


def test_the_default_adapter_is_built_once_over_the_checkout_with_the_seam_proxy_environment(tmp_path: Path) -> None:
    fakes = RerunFakes(_status([_check("unit", "pass")]))
    seams = dataclasses.replace(fakes.seams(), proxy_env={"HTTPS_PROXY": "p"})
    with patch("sandbox_executor.flow.github.ci_final_head.GitHubAdapter") as adapter:
        record = check_final_head_ci(_ctx(tmp_path), str(tmp_path), BRANCH, seams)
    assert record["state"] == "success"
    adapter.assert_called_once_with(str(tmp_path), {"HTTPS_PROXY": "p"})


def test_the_registration_wait_polls_the_injected_adapter(tmp_path: Path) -> None:
    """A first probe without checks enters the registration wait, whose poll must land on the injected adapter."""
    fakes = RerunFakes(_status([]))
    github = FakeGitHub(checks_sequence=[None, ci_checks_payload(passed=1)], head_sha=FINAL)
    seams = dataclasses.replace(fakes.seams(registration_timeout=60.0), probe=None, pr_head=None, github=github)
    record = check_final_head_ci(_ctx(tmp_path), str(tmp_path), BRANCH, seams)
    assert record["state"] == "success"
    # The first probe (no checks), the registration poll (checks registered) and the settled probe.
    assert github.methods() == ["ci_checks"] * 3
    assert fakes.sleeps == []
