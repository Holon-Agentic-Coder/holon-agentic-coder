from __future__ import annotations

from collections.abc import Sequence

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.ci import resolve_ci_registration_timeout
from sandbox_executor.flow.github.ci_failure_detail import gh_detail_lookup, short_sha
from sandbox_executor.flow.github.ci_head_checks import CANCELLED_CONCLUSIONS
from sandbox_executor.flow.review.ci_failure_routing import (
    CLASS_LABELS,
    RERUN,
    RESOLVE,
    FailedRun,
    _failed_runs_from_status,
    classify_ci_failure,
    note_rerun,
    record_routing,
    route_failure,
)
from sandbox_executor.flow.review.ci_inspection import CiInspection, CiInspectionInputs, inspect_ci

RERUN_START_POLL_SECONDS = 5.0


def _eligible(inputs: CiInspectionInputs, ci: CiInspection, head: str | None) -> bool:
    flags = (inputs.reviewer_objected, inputs.is_dirty, inputs.missing_ref, inputs.effective_dry_run)
    if any(flags) or inputs.infra_failed or not head:
        return False
    return ci.decides and ci.ci_green is False and not ci.tests_failed and not ci.ci_status.get("pending")


def _dispatch(inputs: CiInspectionInputs, run_id: str, whole_run: bool) -> bool:
    try:
        if inputs.rerun_failed is not None:
            return bool(inputs.rerun_failed(run_id))
        return bool(inputs.github.rerun_run(run_id) if whole_run else inputs.github.rerun_failed(run_id))
    except Exception:
        return False


def _dispatch_all(inputs: CiInspectionInputs, runs: Sequence[FailedRun]) -> tuple[bool, list[str]]:
    dispatched = []
    for run_id in dict.fromkeys(run.run_id for run in runs if run.run_id):
        whole = all(run.conclusion in CANCELLED_CONCLUSIONS for run in runs if run.run_id == run_id)
        if not _dispatch(inputs, run_id, whole):
            return False, dispatched
        dispatched.append(run_id)
    return True, dispatched


def _wait_for_rerun_start(inputs: CiInspectionInputs, names: set[str]) -> None:
    start = inputs.clock()
    deadline = start + resolve_ci_registration_timeout(inputs.ci_registration_timeout)
    while {run.name for run in _failed_runs_from_status(inputs.probe_ci())} & names:
        remaining = deadline - inputs.clock()
        if remaining <= 0:
            break
        inputs.sleep(max(0.0, min(RERUN_START_POLL_SECONDS, remaining)))
    if inputs.iteration_timer is not None:
        # why: the CI wait has its own budget, so it is not charged to the iteration
        inputs.iteration_timer.credit(max(0.0, inputs.clock() - start))


def rerun_noise_once(
    context: FlowContext, inputs: CiInspectionInputs, ci: CiInspection, head: str | None
) -> CiInspection:
    runs = _failed_runs_from_status(ci.ci_status) if head and _eligible(inputs, ci, head) else []
    if not head or not runs:
        return ci
    cls = classify_ci_failure(runs, inputs.detail_lookup or gh_detail_lookup(inputs.repo_dir, inputs.proxy_env))
    decision, dispatched = route_failure(context, head, cls, runs), []
    if decision == RERUN:
        ok, dispatched = _dispatch_all(inputs, runs)
        if ok:
            note_rerun(context, head, dispatched)
        else:
            context.log(f"[Review Loop] CI noise rerun on {short_sha(head)} could not be dispatched; resolving.")
            decision = RESOLVE
    last = record_routing(context, head, cls, decision, runs)
    if decision != RERUN:
        # why: a partial dispatch is counted only after recording, so the record names only inspected reruns
        note_rerun(context, head, dispatched)
        context.log(f"[Review Loop] CI failure on {short_sha(head)} classified {cls}: sent to the resolver.")
        return ci
    context.log(
        f"[Review Loop] CI noise ({CLASS_LABELS[cls]}) on {short_sha(head)}: rerunning failed jobs of run "
        f"{', '.join(last['run_ids'])} once; the diff approval stands."
    )
    _wait_for_rerun_start(inputs, {run.name for run in runs})
    after = inspect_ci(inputs, context)
    still_red = after.ci_green is False
    if still_red:
        # why: the ledger records the hand-off to the resolver, as Stage 5 does
        record_routing(context, head, cls, RESOLVE, _failed_runs_from_status(after.ci_status) or runs)
        context.log(f"[Review Loop] CI noise on {short_sha(head)} still red after its rerun: sent to the resolver.")
    return after
