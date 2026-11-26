"""Post-approval CI failures are classified fail-safe, and noise is rerun at most once per run id and head."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.review.approval_gates import RED, PipelineGate, pipeline_gate_of, record_pipeline_gate
from sandbox_executor.flow.review.ci_failure_routing import (
    DEFECT,
    NOISE_CANCELLED,
    NOISE_INFRA,
    NOISE_TIMING,
    RERUN,
    RESOLVE,
    FailedRun,
    _failed_runs_from_status,
    _last_routing,
    _reruns_spent,
    classify_ci_failure,
    failed_runs_from_record,
    note_rerun,
    record_routing,
    route_failure,
)
from sandbox_executor.ledger import read_merged_rows
from sandbox_executor.review_accounting import accounting_of

HEAD = "a" * 40


def _ctx(tmp_path: Path) -> FlowContext:
    (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True, exist_ok=True)
    return FlowContext(repo_dir=str(tmp_path), execution_branch="I-1-x/P-1-A/E-1-A/_", agent="a", model="m")


def _lookup(steps: dict[str, list[str]]) -> Any:
    def lookup(check: dict[str, Any]) -> dict[str, Any] | None:
        failed = steps.get(check["name"])
        if failed is None:
            return None
        gate = bool(failed) and all(s.startswith("CI Timing Gate") for s in failed)
        return {"name": check["name"], "failed_steps": failed, "duration_seconds": None, "timing_gate": gate}

    return lookup


def _run(name: str = "test", run_id: str | None = "11", conclusion: str = "failure") -> FailedRun:
    return FailedRun(name, run_id, conclusion)


@pytest.mark.parametrize(
    ("runs", "steps", "expected"),
    [
        ([_run()], {"test": ["CI Timing Gate (fast suite)"]}, NOISE_TIMING),
        ([_run(conclusion="startup_failure")], {}, NOISE_INFRA),
        ([_run()], {"test": ["Set up job", "Post Checkout"]}, NOISE_INFRA),
        ([_run(conclusion="cancelled"), _run("lint", "12", "stale")], {}, NOISE_CANCELLED),
        ([_run()], {"test": ["Run tests"]}, DEFECT),
        ([_run(), _run("lint", "12")], {"test": ["CI Timing Gate"], "lint": ["Run tests"]}, DEFECT),
        ([_run(), _run("lint", "12")], {"test": ["CI Timing Gate"], "lint": ["Checkout"]}, DEFECT),
        ([_run()], {}, DEFECT),
        ([_run()], {"test": []}, DEFECT),
        ([_run(run_id=None)], {"test": ["CI Timing Gate"]}, DEFECT),
        ([_run(f"j{i}", str(i)) for i in range(6)], {f"j{i}": ["CI Timing Gate"] for i in range(6)}, DEFECT),
        ([], {}, DEFECT),
    ],
    ids=[
        "timing",
        "infra-conclusion",
        "setup-steps",
        "cancelled",
        "test-step",
        "timing-and-test",
        "timing-and-infra",
        "unreadable",
        "no-failed-step",
        "no-run-id",
        "too-many-jobs",
        "no-run",
    ],
)
def test_classification_fails_safe(runs: list[FailedRun], steps: dict[str, list[str]], expected: str) -> None:
    assert classify_ci_failure(runs, _lookup(steps)) == expected


def test_failed_runs_from_the_stage4_and_stage5_shapes() -> None:
    status = {
        "checks": [
            {"name": "test", "bucket": "fail", "state": "FAILURE", "link": "https://x/actions/runs/11/job/1"},
            {"name": "lint", "bucket": "cancel", "link": "https://x/actions/runs/12/job/2"},
            {"name": "ok", "bucket": "pass", "link": "https://x/actions/runs/13/job/3"},
            {"name": "ext", "bucket": "fail", "link": "https://ci.example/build/4"},
        ]
    }
    assert _failed_runs_from_status(status) == [_run(), _run("lint", "12", "cancelled"), _run("ext", None, "failure")]
    record = {
        "runs": [
            {"name": "test", "run_id": 11, "conclusion": "failure"},
            {"name": "ok", "run_id": 13, "conclusion": "success"},
            {"name": "slow", "run_id": 14, "conclusion": "in_progress"},
        ]
    }
    assert failed_runs_from_record(record) == [_run()]


def test_one_rerun_per_run_id_and_head(tmp_path: Path) -> None:
    ctx, runs = _ctx(tmp_path), [_run()]
    assert route_failure(ctx, HEAD, NOISE_TIMING, runs) == RERUN
    note_rerun(ctx, HEAD, ["11"])
    assert _reruns_spent(ctx, HEAD, "11") == 1
    assert route_failure(ctx, HEAD, NOISE_TIMING, runs) == RESOLVE
    assert route_failure(ctx, "b" * 40, NOISE_TIMING, runs) == RERUN
    assert route_failure(ctx, HEAD, DEFECT, [_run(run_id="99")]) == RESOLVE
    assert route_failure(ctx, HEAD, NOISE_INFRA, [_run(run_id=None)]) == RESOLVE
    assert FlowContext.from_dict(ctx.to_dict()).review_state["ci_routing"]["reruns"][HEAD] == {"11": 1}


def test_record_routing_classifies_the_gate_and_appends_one_row(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    record_pipeline_gate(ctx, PipelineGate(HEAD, RED, ("11",)))
    last = record_routing(ctx, HEAD, NOISE_TIMING, RERUN, [_run()])
    gate = pipeline_gate_of(ctx)
    assert gate is not None and gate.classification == NOISE_TIMING
    rows = read_merged_rows(str(tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"))
    assert len(rows) == 1 and rows[0]["action_id"] == last["action_id"]
    assert rows[0]["ci_failure_routing"]["decision"] == RERUN
    assert _last_routing(ctx) == last


def test_record_routing_leaves_another_heads_gate_alone(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    record_pipeline_gate(ctx, PipelineGate("b" * 40, RED))
    record_routing(ctx, HEAD, DEFECT, RESOLVE, [_run()])
    gate = pipeline_gate_of(ctx)
    assert gate is not None and gate.classification is None


def test_a_noise_rerun_counts_one_rerun_and_one_ci_noise_churn(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    note_rerun(ctx, HEAD, ["11"])
    record_routing(ctx, HEAD, NOISE_INFRA, RERUN, [_run()])
    record_routing(ctx, HEAD, NOISE_INFRA, RESOLVE, [_run()])  # Still red after its rerun: no new churn.
    accounting = accounting_of(FlowContext.from_dict(ctx.to_dict()))
    assert accounting is not None
    assert (accounting["pipeline_reruns"], accounting["non_defect_churn"]["ci_noise"]) == (1, 1)


def test_a_defect_routing_counts_no_churn(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    record_routing(ctx, HEAD, DEFECT, RESOLVE, [_run()])
    assert accounting_of(ctx) is None
