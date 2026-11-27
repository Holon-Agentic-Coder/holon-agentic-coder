from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, NamedTuple

from sandbox_executor.budgets import StageTimer
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.ci import _ci_green, resolve_ci_registration_timeout
from sandbox_executor.flow.github.ci_failure_detail import DetailLookup, parse_run_id
from sandbox_executor.flow.github.ci_wait import CiWaitPolicy, CiWaitResult, settle_pending_ci
from sandbox_executor.flow.review.ci_evidence import _apply_guarded_test_evidence
from sandbox_executor.flow.review.ci_outcome import gate_ci_flags
from sandbox_executor.flow.review.delta_review import current_decision
from sandbox_executor.review_accounting import count_accounting


@dataclass(frozen=True)
class CiInspectionInputs:
    repo_dir: str
    review_target: str
    proxy_env: dict[str, str]
    github: Any
    test_gate: Any
    ci_policy: CiWaitPolicy
    # why: passed in from the loop module so tests patching loop.probe_ci_checks still intercept them
    probe_ci: Callable[[], dict[str, Any]]
    wait_registration: Callable[..., None]
    pr_allowed: bool
    ci_registration_timeout: float | None
    ci_poll_interval: float
    clock: Callable[[], float]
    sleep: Callable[[float], None]
    iteration_timer: StageTimer | None = None
    reviewer_objected: bool = False
    is_dirty: bool = False
    missing_ref: bool = False
    effective_dry_run: bool = False
    infra_failed: bool = False
    ci_wait: CiWaitResult | None = None
    detail_lookup: DetailLookup | None = None
    rerun_failed: Callable[[str], bool] | None = None


class CiInspection(NamedTuple):
    ci_status: dict[str, Any]
    ci_wait: CiWaitResult | None
    tests_failed: bool
    tests_not_measured: bool
    ci_green: bool | None
    ci_finding: bool
    decides: bool


def inspect_ci(inputs: CiInspectionInputs, context: FlowContext) -> CiInspection:
    if inputs.reviewer_objected:
        ci = _fast_probe(inputs)
    elif not inputs.is_dirty and not inputs.missing_ref and not inputs.effective_dry_run and not inputs.infra_failed:
        ci = _post_review_gate(inputs, context)
    else:
        ci = _fallback_probe(inputs)
    _count_settled_pipeline(context, inputs.review_target, ci.ci_status)
    return ci


def _count_settled_pipeline(context: FlowContext, review_target: str, ci_status: dict[str, Any]) -> None:
    if ci_status.get("source") != "github_cli" or not ci_status.get("total") or ci_status.get("pending", 0):
        return
    links = (check.get("link") for check in ci_status.get("checks") or [] if isinstance(check, dict))
    run_ids = sorted({run_id for run_id in map(parse_run_id, links) if run_id})
    runs = ",".join(run_ids) or "no-run-id"
    count_accounting(context, "pipeline_runs", event=f"run:{current_decision(context).head or review_target}:{runs}")


def _kept_wait(ci_wait: CiWaitResult | None, ci_status: dict[str, Any]) -> CiWaitResult | None:
    return ci_wait if ci_status.get("pending", 0) else None


def _fast_probe(inputs: CiInspectionInputs) -> CiInspection:
    ci_status = inputs.probe_ci()
    tests_failed, _ = _apply_guarded_test_evidence(
        inputs.test_gate, ci_status, inputs.effective_dry_run, inputs.infra_failed or inputs.missing_ref, False
    )
    ci_failed = ci_status.get("failed", 0) > 0 or tests_failed
    return CiInspection(
        ci_status,
        _kept_wait(inputs.ci_wait, ci_status),
        tests_failed,
        False,
        False if ci_failed else None,
        ci_failed,
        True,
    )


def _post_review_gate(inputs: CiInspectionInputs, context: FlowContext) -> CiInspection:
    if inputs.pr_allowed and inputs.test_gate.needs_ci_registration() and inputs.github.available:
        timer = inputs.iteration_timer
        timeout = resolve_ci_registration_timeout(inputs.ci_registration_timeout)
        inputs.wait_registration(
            context,
            inputs.repo_dir,
            inputs.review_target,
            inputs.proxy_env,
            timer.remaining_capped(timeout) if timer is not None else timeout,
            inputs.ci_poll_interval,
            clock=inputs.clock,
            sleep=inputs.sleep,
            probe=inputs.probe_ci,
        )

    ci_status = inputs.probe_ci()
    ci_status, waited = settle_pending_ci(context, inputs.probe_ci, ci_status, inputs.ci_policy, bool(inputs.ci_wait))
    if inputs.iteration_timer is not None:
        # why: the CI wait has its own budget, so it is not charged to the iteration
        inputs.iteration_timer.credit(waited.waited_seconds if waited else 0.0)
    ci_wait = waited if waited and waited.exhausted else _kept_wait(inputs.ci_wait, ci_status)
    tests_failed, unmeasured = _apply_guarded_test_evidence(inputs.test_gate, ci_status, False, False, True)
    ci_green, ci_finding = gate_ci_flags(ci_status, tests_failed)
    return CiInspection(ci_status, ci_wait, tests_failed, unmeasured, ci_green, ci_finding, True)


def _fallback_probe(inputs: CiInspectionInputs) -> CiInspection:
    ci_status = inputs.probe_ci()
    tests_failed, unmeasured = _apply_guarded_test_evidence(
        inputs.test_gate, ci_status, inputs.effective_dry_run, inputs.infra_failed or inputs.missing_ref, True
    )
    ci_finding = not ci_status.get("all_passed", False) or tests_failed
    return CiInspection(
        ci_status,
        _kept_wait(inputs.ci_wait, ci_status),
        tests_failed,
        unmeasured,
        _ci_green(ci_status, tests_failed),
        ci_finding,
        not inputs.effective_dry_run,
    )
