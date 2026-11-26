"""Classes of a post-approval CI failure, and the rerun budget shared by Stage 4 and Stage 5.

A red pipeline after the reviewers approved is either noise (``noise_timing``: only the CI timing gate failed;
``noise_infra``: the runner failed to start, the job timed out or only setup and teardown steps failed;
``noise_cancelled``: every failed run was cancelled or stale) or a ``defect``. Noise is rerun once per run id and head
(``CI_NOISE_RERUNS``) while the diff approval stands; a defect, or noise still red after its rerun, goes to the
resolver. The rule fails safe: no run, a run without an Actions run id, more than ``MAX_JOBS_DESCRIBED`` failed jobs, an
unreadable job detail, a failed test step or any mix of classes is a ``defect``. A repeated timing-gate failure keeps
the slowest-tests finding (:mod:`sandbox_executor.flow.review.ci_timing_finding`).

The budget and the last decision live in ``context.review_state["ci_routing"]``, checkpointed with the context, so a
resume never reruns twice.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.ci_failure_detail import DetailLookup, parse_run_id
from sandbox_executor.flow.github.ci_head_checks import CANCELLED_CONCLUSIONS, PASSING_CONCLUSIONS, PENDING_STATUSES
from sandbox_executor.flow.review.approval_gates import RED, pipeline_gate_of, record_pipeline_gate
from sandbox_executor.flow.review.ci_timing_finding import MAX_JOBS_DESCRIBED
from sandbox_executor.ledger import generate_action_id
from sandbox_executor.review_accounting import count_accounting

NOISE_TIMING, NOISE_INFRA, NOISE_CANCELLED, DEFECT = "noise_timing", "noise_infra", "noise_cancelled", "defect"
NOISE_CLASSES = frozenset({NOISE_TIMING, NOISE_INFRA, NOISE_CANCELLED})
RERUN, RESOLVE = "rerun", "resolve"
INFRA_CONCLUSIONS = frozenset({"startup_failure", "timed_out"})
INFRA_STEP_PREFIXES = ("Set up job", "Checkout", "Install uv", "Set up Python", "Post ", "Complete job")
CI_NOISE_RERUNS = 1  # Reruns of one run id on one head before the failure goes to the resolver.
CLASS_LABELS = {NOISE_TIMING: "timing gate", NOISE_INFRA: "runner failure", NOISE_CANCELLED: "cancelled run"}


@dataclass(frozen=True)
class FailedRun:
    """One failed check: its name, Actions run id (None when unknown) and lower-case conclusion."""

    name: str
    run_id: str | None
    conclusion: str


def _failed_runs_from_status(ci_status: Mapping[str, Any]) -> list[FailedRun]:
    """The failed or cancelled checks of a Stage 4 ``gh pr checks`` status."""
    runs = []
    for check in ci_status.get("checks") or []:
        bucket = str(check.get("bucket") or "").lower() if isinstance(check, Mapping) else ""
        if bucket in ("fail", "cancel"):
            conclusion = "cancelled" if bucket == "cancel" else str(check.get("state") or "failure").lower()
            runs.append(FailedRun(str(check.get("name") or ""), parse_run_id(check.get("link")), conclusion))
    return runs


def failed_runs_from_record(record: Mapping[str, Any]) -> list[FailedRun]:
    """The runs of a Stage 5 merged-head record that did not pass and are not pending."""
    settled = PASSING_CONCLUSIONS | PENDING_STATUSES
    runs = [run for run in record.get("runs") or [] if str(run.get("conclusion")) not in settled]
    return [
        FailedRun(
            str(run.get("name") or ""), str(run["run_id"]) if run.get("run_id") else None, str(run.get("conclusion"))
        )
        for run in runs
    ]


def _infra_step(step: str) -> bool:
    return step.startswith(INFRA_STEP_PREFIXES)


def _run_class(run: FailedRun, lookup: DetailLookup) -> str:
    """The class of one failed (not cancelled) run from its conclusion and failed steps."""
    if run.conclusion in INFRA_CONCLUSIONS:
        return NOISE_INFRA
    detail = lookup({"name": run.name, "link": f"/actions/runs/{run.run_id}"})
    if not detail:
        return DEFECT
    steps = [str(step) for step in detail.get("failed_steps") or []]
    if detail.get("timing_gate"):
        return NOISE_TIMING
    return NOISE_INFRA if steps and all(_infra_step(step) for step in steps) else DEFECT


def classify_ci_failure(runs: Sequence[FailedRun], lookup: DetailLookup) -> str:
    """The class of a red pipeline; anything unknown, unreadable or mixed is ``defect``."""
    if not runs or len(runs) > MAX_JOBS_DESCRIBED or any(run.run_id is None for run in runs):
        return DEFECT
    if all(run.conclusion in CANCELLED_CONCLUSIONS for run in runs):
        return NOISE_CANCELLED
    classes = {_run_class(run, lookup) for run in runs}
    return classes.pop() if len(classes) == 1 else DEFECT


def _routing_state(context: FlowContext) -> dict[str, Any]:
    state = context.review_state.get("ci_routing")
    if not isinstance(state, dict):
        state = context.review_state["ci_routing"] = {}
    return state


def _run_ids(runs: Iterable[FailedRun]) -> list[str]:
    return list(dict.fromkeys(run.run_id for run in runs if run.run_id))


def _reruns_spent(context: FlowContext, head: str, run_id: str) -> int:
    """How many noise reruns of ``run_id`` were dispatched on ``head``."""
    spent = (_routing_state(context).get("reruns") or {}).get(head) or {}
    count = spent.get(run_id) if isinstance(spent, Mapping) else None
    return count if isinstance(count, int) else 0


def note_rerun(context: FlowContext, head: str, run_ids: Iterable[str]) -> None:
    """Count one dispatched rerun of each of ``run_ids`` on ``head``."""
    reruns = _routing_state(context).setdefault("reruns", {})
    spent = reruns.setdefault(head, {})
    for run_id in run_ids:
        spent[run_id] = _reruns_spent(context, head, run_id) + 1
        count_accounting(context, "pipeline_reruns", event=f"rerun:{head}:{run_id}")


def route_failure(context: FlowContext, head: str, cls: str, runs: Sequence[FailedRun]) -> str:
    """``rerun`` for noise whose every run id is under the budget on ``head``, else ``resolve``."""
    run_ids = _run_ids(runs)
    if cls not in NOISE_CLASSES or not run_ids or not head:
        return RESOLVE
    return RERUN if all(_reruns_spent(context, head, run_id) < CI_NOISE_RERUNS for run_id in run_ids) else RESOLVE


def _last_routing(context: FlowContext) -> dict[str, Any] | None:
    """The last routing decision, or None when no failure was routed."""
    last = _routing_state(context).get("last")
    return last if isinstance(last, dict) else None


def apply_last_classification(context: FlowContext, head: str | None) -> None:
    """Set the red pipeline gate's classification from the last routing decision on the same ``head``."""
    last, gate = _last_routing(context), pipeline_gate_of(context)
    if last and gate is not None and gate.state == RED and head and gate.head_sha == head == last.get("head_sha"):
        record_pipeline_gate(context, replace(gate, classification=str(last.get("classification"))))


def _routing_row(context: FlowContext, last: Mapping[str, Any]) -> dict[str, Any]:
    head, cls, decision = last["head_sha"], last["classification"], last["decision"]
    return {
        "action_id": generate_action_id("ACT-CIROUTE"),
        "execution_id": f"REV-CIROUTE-{int(datetime.now(UTC).timestamp())}",
        "action": f"ci_failure_routing for {context.execution_branch}: {cls} on {head} -> {decision}",
        "category": "code",
        "target": str(context.execution_branch or ""),
        "outcome": "failure",
        "entry_type": "action",
        "action_origin": "pr_review",
        "labels": ["review", "ci-failure-routing", cls],
        "ci_failure_routing": dict(last),
        "created_at": datetime.now(UTC).isoformat(),
    }


def record_routing(
    context: FlowContext, head: str, cls: str, decision: str, runs: Sequence[FailedRun]
) -> dict[str, Any]:
    """Store the decision as the last one, classify the pipeline gate on ``head`` and append one ACT row."""
    from sandbox_executor.flow.review.records import record_review_action  # Lazy: records imports review_report.

    run_ids = _run_ids(runs)
    spent = max((_reruns_spent(context, head, run_id) for run_id in run_ids), default=0)
    last = {"head_sha": head, "classification": cls, "decision": decision, "run_ids": run_ids, "reruns": spent}
    _routing_state(context)["last"] = last
    if decision == RERUN and cls in NOISE_CLASSES:
        count_accounting(context, "non_defect_churn", "ci_noise", event=f"noise:{head}:{','.join(sorted(run_ids))}")
    apply_last_classification(context, head)
    ledger_path = os.path.join(context.target_worktree, "holon-knowledge", "ledger", "executions.jsonl")
    row = record_review_action(context, ledger_path, _routing_row(context, last))
    last["action_id"] = (row or {}).get("action_id")
    return last
