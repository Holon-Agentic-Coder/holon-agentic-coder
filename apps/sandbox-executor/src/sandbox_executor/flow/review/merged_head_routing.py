from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.github.ci_failure_detail import gh_detail_lookup, short_sha
from sandbox_executor.flow.github.ci_head_checks import FAILED, PASSED
from sandbox_executor.flow.github.ci_head_reruns import gh_run_rerunner
from sandbox_executor.flow.github.ci_reverify import approved_head_of
from sandbox_executor.flow.github.ci_slowest_tests import gh_slowest_lookup
from sandbox_executor.flow.review import delta_review
from sandbox_executor.flow.review.approval_gates import (
    GREEN,
    PipelineGate,
    diff_gate_holds,
    mark_pipeline_red,
    record_pipeline_gate,
)
from sandbox_executor.flow.review.carried_findings import serialize_open
from sandbox_executor.flow.review.ci_failure_routing import (
    BLOCK,
    CLASS_LABELS,
    NOISE_TIMING,
    RERUN,
    RESOLVE,
    FailedRun,
    classify_ci_failure,
    failed_runs_from_record,
    note_rerun,
    record_routing,
    route_failure,
)
from sandbox_executor.flow.review.ci_timing_finding import timing_gate_finding
from sandbox_executor.flow.review.consensus_withdrawal import failed_runs_of, withdraw_consensus
from sandbox_executor.flow.review.main_baseline_guard import (
    MAIN_BASELINE_FAILING,
    BaselineGuardSeams,
    classify_baseline_block,
)
from sandbox_executor.flow.review.merged_head_gate import (
    MergedHeadSeams,
    _wait_for_rerun_start,
    format_merged_head_ci,
    gate_merged_head_ci,
)
from sandbox_executor.review_accounting import restart_review_passes
from sandbox_executor.review_report import ci_failure_finding

Decision = tuple[StageResult | None, dict[str, Any]]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _skipped(start: str, payload: dict[str, Any]) -> StageResult:
    return StageResult(FlowStage.CALIBRATE, StageStatus.SKIPPED, start, _now(), payload=payload)


def refuse_calibration(context: FlowContext, start: str, record: dict[str, Any]) -> StageResult:
    reason = (
        f"Calibrate stage refused: CI on merged head {record['head_sha']} is {record['state']} ({record['reason']}); "
        "nothing was calibrated or pushed."
    )
    extra: dict[str, Any] = {"merged_head_ci": record}
    if record.get("blocked_on"):
        extra["blocked_on"] = record["blocked_on"]
    return _stage_failure(context, FlowStage.CALIBRATE, start, reason, **extra)


def _withdraw(context: FlowContext, start: str, record: dict[str, Any], seams: MergedHeadSeams, withdraw: Any) -> Any:
    withdrawal = withdraw(context, record, seams.withdrawal, context.merged_head_restart_allowed)
    return _skipped(start, {"review_restart": True, "merged_head_ci": record, "consensus_withdrawal": withdrawal})


def _rerun(
    context: FlowContext, head: str, cls: str, runs: Sequence[FailedRun], seams: MergedHeadSeams
) -> tuple[dict[str, Any], list[str]]:
    repo_dir = context.target_worktree
    dispatch = seams.rerun_dispatcher or gh_run_rerunner(repo_dir, seams.proxy_env)
    dispatched: list[str] = []
    for run_id in dict.fromkeys(run.run_id for run in runs if run.run_id):
        if not dispatch(run_id):
            reason = f"the rerun of run {run_id} could not be dispatched"
            return {"state": FAILED, "head_sha": head, "reason": reason}, dispatched
        dispatched.append(run_id)
    # why: counted before the routing is recorded, so ``reruns`` means the same in Stage 4 and Stage 5.
    note_rerun(context, head, dispatched)
    record_routing(context, head, cls, RERUN, runs)
    _wait_for_rerun_start(context, head, {run.name for run in runs}, seams)
    retried = gate_merged_head_ci(context, repo_dir, head, seams)
    context.log(f"After the noise rerun: {format_merged_head_ci(retried)}")
    return retried, []


def _ci_finding(cls: str, runs: Sequence[FailedRun], seams: MergedHeadSeams, context: FlowContext) -> Any:
    names = list(dict.fromkeys(run.name for run in runs))
    status = {"pending": 0, "checks": [{"name": r.name, "link": f"/actions/runs/{r.run_id}"} for r in runs]}
    if cls == NOISE_TIMING:
        repo_dir, proxy_env = context.target_worktree, seams.proxy_env
        lookups = (
            seams.detail_lookup or gh_detail_lookup(repo_dir, proxy_env),
            seams.slowest_lookup or gh_slowest_lookup(repo_dir, proxy_env),
        )
        return timing_gate_finding(0, status, names, lookups) or ci_failure_finding(0, names)
    return ci_failure_finding(0, names)


def _scoped_restart(context: FlowContext, start: str, record: dict[str, Any], cls: str, seams: MergedHeadSeams) -> Any:
    head, runs, approved = str(record["head_sha"]), failed_runs_from_record(record), approved_head_of(context)
    mark_pipeline_red(context, head, failed_runs_of(record))
    routing = record_routing(context, head, cls, RESOLVE, runs)
    restart_review_passes(context)
    context.carried_review_findings = serialize_open([_ci_finding(cls, runs, seams, context)], {})
    defect = {"head_sha": head, "classification": cls, "action_id": routing.get("action_id"), "merged_head_ci": record}
    # why: the consensus stops naming the approved head below; a rejected fix review withdraws it from here.
    defect["approved_head"] = approved
    review = context.stage_results.get(FlowStage.REVIEW.value)
    if review is not None:
        consensus = {**(review.payload.get("consensus") or {}), "approved": False, "pipeline_defect": defect}
        review.payload = {**review.payload, "consensus": consensus}
    delta_review.adopt_reviewed_head(context, head)
    payload = {"review_restart": True, "scoped_restart": True, "merged_head_ci": record, "ci_routing": routing}
    return _skipped(start, {**payload, "approved_head": approved})


def route_merged_head_failure(
    context: FlowContext,
    start: str,
    record: dict[str, Any],
    seams: MergedHeadSeams,
    # why: the caller passes ``withdraw`` so its module-level binding stays patchable.
    withdraw: Callable[..., dict[str, Any]] = withdraw_consensus,
) -> Decision:
    head = str(record.get("head_sha") or "")
    runs = failed_runs_from_record(record)
    target_branch = str(context.intent_data.get("target_branch") or "main")
    guard_seams = seams.baseline_guard or BaselineGuardSeams(proxy_env=seams.proxy_env)
    if block := classify_baseline_block(context, target_branch, head, [r.name for r in runs], guard_seams):
        record_routing(context, head, MAIN_BASELINE_FAILING, BLOCK, runs)
        context.log(
            f"Merged-head CI on {short_sha(head)} blocked on {MAIN_BASELINE_FAILING}: "
            "preserving consensus and refusing resolver dispatch."
        )
        blocked_record = {**record, "blocked_on": MAIN_BASELINE_FAILING, "reason": block["reason"]}
        return refuse_calibration(context, start, blocked_record), blocked_record
    if record.get("withdrawal_reason") or not diff_gate_holds(context, context.target_worktree, head):
        return _withdraw(context, start, record, seams, withdraw), record

    def restart_or_withdraw(failed: dict[str, Any]) -> Any:
        # why: at the restart cap Stage 5 must not stop on a standing approval, so the consensus is withdrawn.
        if not context.merged_head_restart_allowed:
            return _withdraw(context, start, failed, seams, withdraw)
        return _scoped_restart(context, start, failed, cls, seams)

    cls = classify_ci_failure(runs, seams.detail_lookup or gh_detail_lookup(context.target_worktree, seams.proxy_env))
    if route_failure(context, head, cls, runs) != RERUN:
        return restart_or_withdraw(record), record
    context.log(
        f"CI noise ({CLASS_LABELS[cls]}) on merged head {short_sha(head)}: rerunning once; the diff approval stands."
    )
    rerun, partial = _rerun(context, head, cls, runs, seams)
    retried = {**record, **rerun}
    if retried["state"] == PASSED:
        record_pipeline_gate(context, PipelineGate(head, GREEN, source="merged_head"))
        return None, retried
    if retried["state"] == FAILED:
        restart = restart_or_withdraw(retried)
        # why: partial reruns were never inspected, so they are counted after the resolve routing, as Stage 4 does.
        note_rerun(context, head, partial)
        return restart, retried
    return refuse_calibration(context, start, retried), retried
