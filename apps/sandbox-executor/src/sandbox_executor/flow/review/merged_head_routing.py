"""Stage 5's routing of a red merged head by failure class, instead of withdrawing the consensus on any failure.

The consensus is withdrawn (today's comment, marker and ledger row) only when the reviewable diff changed (the record
carries ``withdrawal_reason``) or the diff gate does not hold on the merged head. Otherwise the failure is classified
(:mod:`.ci_failure_routing`): noise under its rerun budget is rerun once and the head polled again, and only a fresh
``passed`` lets Stage 5 calibrate. A defect, or noise still red, restarts Stage 4 scoped: the CI finding is carried to
the resolver, the diff approval stands, and the delta-review anchor moves to the merged head so the fix delta (no merge
commit) is reviewed scoped. A scoped restart whose Stage 4 does not approve withdraws the consensus then. Anything
unsettled still fails Stage 5 closed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.dispatch import _stage_failure
from sandbox_executor.flow.github.ci_failure_detail import gh_detail_lookup, short_sha
from sandbox_executor.flow.github.ci_head_checks import FAILED, PASSED
from sandbox_executor.flow.github.ci_head_reruns import gh_run_rerunner
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
    CLASS_LABELS,
    NOISE_TIMING,
    RERUN,
    RESOLVE,
    _classify_failure,
    _failed_runs_from_record,
    _FailedRun,
    _note_rerun,
    _record_routing,
    _route_failure,
)
from sandbox_executor.flow.review.ci_timing_finding import timing_gate_finding
from sandbox_executor.flow.review.consensus_withdrawal import (
    WithdrawalSeams,
    failed_runs_of,
    withdraw_consensus,
    withdrawn_consensus_of,
)
from sandbox_executor.flow.review.merged_head_gate import (
    MergedHeadSeams,
    _wait_for_rerun_start,
    format_merged_head_ci,
    gate_merged_head_ci,
    skipped_record,
)
from sandbox_executor.review_report import ci_failure_finding

Decision = tuple[StageResult | None, dict[str, Any]]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _skipped(start: str, payload: dict[str, Any]) -> StageResult:
    return StageResult(FlowStage.CALIBRATE, StageStatus.SKIPPED, start, _now(), payload=payload)


def _refuse_calibration(context: FlowContext, start: str, record: dict[str, Any]) -> StageResult:
    """Fail Stage 5 closed on an unsettled merged head: nothing is calibrated or pushed."""
    reason = (
        f"Calibrate stage refused: CI on merged head {record['head_sha']} is {record['state']} ({record['reason']}); "
        "nothing was calibrated or pushed."
    )
    return _stage_failure(context, FlowStage.CALIBRATE, start, reason, merged_head_ci=record)


def _withdraw(context: FlowContext, start: str, record: dict[str, Any], seams: MergedHeadSeams, withdraw: Any) -> Any:
    withdrawal = withdraw(context, record, seams.withdrawal, context.merged_head_restart_allowed)
    return _skipped(start, {"review_restart": True, "merged_head_ci": record, "consensus_withdrawal": withdrawal})


def _rerun(
    context: FlowContext, head: str, cls: str, runs: Sequence[_FailedRun], seams: MergedHeadSeams
) -> dict[str, Any]:
    """Rerun each failed run once, record the routing as Stage 4 does and poll the head again.

    The routing is recorded after the rerun is counted, so ``reruns`` means the same in both stages;
    a failed dispatch keeps the head failed, and its scoped restart records the resolve routing.
    """
    repo_dir = context.target_worktree
    dispatch = seams.rerun_dispatcher or gh_run_rerunner(repo_dir, seams.proxy_env)
    dispatched: list[str] = []
    for run_id in dict.fromkeys(run.run_id for run in runs if run.run_id):
        if not dispatch(run_id):
            _note_rerun(context, head, dispatched)
            return {"state": FAILED, "head_sha": head, "reason": f"the rerun of run {run_id} could not be dispatched"}
        dispatched.append(run_id)
    _note_rerun(context, head, dispatched)
    _record_routing(context, head, cls, RERUN, runs)
    _wait_for_rerun_start(context, head, {run.name for run in runs}, seams)
    retried = gate_merged_head_ci(context, repo_dir, head, seams)
    context.log(f"After the noise rerun: {format_merged_head_ci(retried)}")
    return retried


def _ci_finding(cls: str, runs: Sequence[_FailedRun], seams: MergedHeadSeams, context: FlowContext) -> Any:
    """The carried ``FIND-CI-000``: the slowest-tests finding for a repeated timing failure, else the generic one."""
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
    """Restart Stage 4 with the resolver on the CI finding; the diff approval stands and the fix is reviewed scoped."""
    head, runs = str(record["head_sha"]), _failed_runs_from_record(record)
    mark_pipeline_red(context, head, failed_runs_of(record))
    routing = _record_routing(context, head, cls, RESOLVE, runs)
    context.carried_review_findings = serialize_open([_ci_finding(cls, runs, seams, context)], {})
    context.review_iterations_done = 0
    defect = {"head_sha": head, "classification": cls, "action_id": routing.get("action_id"), "merged_head_ci": record}
    review = context.stage_results.get(FlowStage.REVIEW.value)
    if review is not None:
        consensus = {**(review.payload.get("consensus") or {}), "approved": False, "pipeline_defect": defect}
        review.payload = {**review.payload, "consensus": consensus}
    delta_review._adopt_reviewed_head(context, head)
    payload = {"review_restart": True, "scoped_restart": True, "merged_head_ci": record, "ci_routing": routing}
    return _skipped(start, payload)


def _route_merged_head_failure(
    context: FlowContext,
    start: str,
    record: dict[str, Any],
    seams: MergedHeadSeams,
    withdraw: Callable[..., dict[str, Any]] = withdraw_consensus,
) -> Decision:
    """The Stage 5 outcome of a failed merged head and the record it rests on: None (calibrate) or a stop.

    ``withdraw`` is :func:`withdraw_consensus`, passed by the caller so its module-level binding stays patchable.
    """
    head = str(record.get("head_sha") or "")
    if record.get("withdrawal_reason") or not diff_gate_holds(context, context.target_worktree, head):
        return _withdraw(context, start, record, seams, withdraw), record
    runs = _failed_runs_from_record(record)
    cls = _classify_failure(runs, seams.detail_lookup or gh_detail_lookup(context.target_worktree, seams.proxy_env))
    if _route_failure(context, head, cls, runs) != RERUN:
        return _scoped_restart(context, start, record, cls, seams), record
    context.log(
        f"CI noise ({CLASS_LABELS[cls]}) on merged head {short_sha(head)}: rerunning once; the diff approval stands."
    )
    retried = {**record, **_rerun(context, head, cls, runs, seams)}
    if retried["state"] == PASSED:
        record_pipeline_gate(context, PipelineGate(head, GREEN, source="merged_head"))
        return None, retried
    if retried["state"] == FAILED:
        return _scoped_restart(context, start, retried, cls, seams), retried
    return _refuse_calibration(context, start, retried), retried


def _pending_scoped_restart(context: FlowContext) -> dict[str, Any] | None:
    """The merged-head defect a scoped restart was asked for while Stage 4 has not run again, else None."""
    review = context.stage_results.get(FlowStage.REVIEW.value)
    defect = ((review.payload.get("consensus") if review else None) or {}).get("pipeline_defect")
    return defect if isinstance(defect, dict) else None


def _withdraw_after_rejected_restart(
    context: FlowContext, record: dict[str, Any], seams: WithdrawalSeams | None = None
) -> dict[str, Any]:
    """Withdraw the consensus when the scoped review of a CI-defect fix did not approve."""
    context.log(f"The scoped review of the CI fix on {short_sha(record.get('head_sha'))} did not approve.")
    return withdraw_consensus(context, record, seams, context.merged_head_restart_allowed)


def _resumed_restart(context: FlowContext, start: str) -> Decision | None:
    """On resume, the Stage 5 restart a withdrawn consensus or a pending scoped restart asks for, else None."""
    withdrawn, defect = withdrawn_consensus_of(context), _pending_scoped_restart(context)
    if context.dry_run or context.skip_push or not (withdrawn or defect):
        return None
    if withdrawn:
        record = {**skipped_record(withdrawn.get("merged_head"), "consensus withdrawn"), "state": FAILED}
        context.log("Calibrate stage: the Stage 4 approval was withdrawn; Stage 4 must approve the new head first.")
        payload = {"review_restart": True, "merged_head_ci": record, "consensus_withdrawal": withdrawn}
    else:
        record = defect.get("merged_head_ci") or skipped_record(defect.get("head_sha"), "CI defect")
        context.log("Calibrate stage: CI found a defect on the merged head; Stage 4 must approve the fix first.")
        payload = {"review_restart": True, "scoped_restart": True, "merged_head_ci": record, "ci_routing": defect}
    return StageResult(FlowStage.CALIBRATE, StageStatus.SKIPPED, start, start, payload=payload), record
