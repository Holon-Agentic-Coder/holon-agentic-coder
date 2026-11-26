"""Stage 4 approval gates: consensus on the diff and a green pipeline on the exact head, recorded independently.

"Approved" is only their conjunction (:func:`approved`). A red pipeline marks only the pipeline gate, so the diff
approval survives it; the diff gate holds on a moved head only while the PR-own change fingerprint is unchanged.
Both records live in ``context.review_state["gates"]`` and are checkpointed with the context.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.ci_failure_detail import parse_run_id
from sandbox_executor.flow.review.approval_coverage import approval_covers
from sandbox_executor.flow.review.approval_fingerprint import approval_fingerprint_of, fingerprints_match
from sandbox_executor.flow.review.verdict import decide_returned_consensus
from sandbox_executor.review_ensemble import ConsensusVerdict, ReviewerResult

GREEN, RED, PENDING, UNKNOWN = "green", "red", "pending", "unknown"
PIPELINE_STATES = frozenset({GREEN, RED, PENDING, UNKNOWN})
DIFF, PIPELINE = "diff", "pipeline"


@dataclass(frozen=True)
class DiffGate:
    """Whether the reviewers approved the PR-own diff of ``reviewed_head``, independently of CI."""

    approved: bool
    reviewed_head: str
    fingerprint: str | None = None
    base_branch: str | None = None
    iteration: int = 0
    mode: str = "full"
    verdicts: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "verdicts": list(self.verdicts)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DiffGate:
        return cls(
            approved=data.get("approved") is True,
            reviewed_head=str(data.get("reviewed_head") or ""),
            fingerprint=data.get("fingerprint") or None,
            base_branch=data.get("base_branch") or None,
            iteration=int(data.get("iteration") or 0),
            mode=str(data.get("mode") or "full"),
            verdicts=tuple(str(v) for v in data.get("verdicts") or ()),
        )


@dataclass(frozen=True)
class PipelineGate:
    """The pipeline state on exactly ``head_sha``: ``green``, ``red``, ``pending`` or ``unknown``."""

    head_sha: str
    state: str = UNKNOWN
    run_ids: tuple[str, ...] = ()
    classification: str | None = None
    source: str = "stage4"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "run_ids": list(self.run_ids)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PipelineGate:
        state = str(data.get("state") or UNKNOWN)
        return cls(
            head_sha=str(data.get("head_sha") or ""),
            state=state if state in PIPELINE_STATES else UNKNOWN,
            run_ids=tuple(str(r) for r in data.get("run_ids") or ()),
            classification=data.get("classification") or None,
            source=str(data.get("source") or "stage4"),
        )


@dataclass(frozen=True)
class IterationGates:
    """What one Stage 4 iteration knows about its gates; ``fingerprint_of`` maps a head to its PR-own fingerprint."""

    head: str
    iteration: int
    reviewers: Sequence[ReviewerResult]
    ensemble_size: int
    mergeable: bool | None
    blockers: Sequence[str]
    ci_green: bool | None
    ci_status: Mapping[str, Any] = field(default_factory=dict)
    base_branch: str | None = None
    fingerprint_of: Callable[[str], str | None] | None = None
    mode: str = "full"  # ``scoped`` for a delta pass or a re-review of only ``context.review_scope_paths``.


def pipeline_state_of(ci_green: bool | None, ci_status: Mapping[str, Any]) -> str:
    """``green``/``red`` from the CI verdict, else from ``ci_status`` alone (the fast probe leaves the verdict None
    unless CI failed), so the pipeline gate never depends on the reviewers: ``pending``, ``green`` or ``unknown``."""
    if ci_green is not None:
        return GREEN if ci_green else RED
    if ci_status.get("pending", 0):
        return PENDING
    return GREEN if ci_status.get("all_passed") is True else UNKNOWN


def _gates(context: FlowContext) -> dict[str, Any]:
    gates = context.review_state.get("gates")
    if not isinstance(gates, dict):
        gates = context.review_state["gates"] = {}
    return gates


def record_diff_gate(context: FlowContext, gate: DiffGate) -> None:
    """Record the diff gate; the pipeline gate is left as it is."""
    _gates(context)[DIFF] = gate.to_dict()


def record_pipeline_gate(context: FlowContext, gate: PipelineGate) -> None:
    """Record the pipeline gate; the diff gate is left as it is."""
    _gates(context)[PIPELINE] = gate.to_dict()


def _gate_record(context: FlowContext, name: str) -> Mapping[str, Any] | None:
    gates = context.review_state.get("gates")
    record = gates.get(name) if isinstance(gates, dict) else None
    return record if isinstance(record, Mapping) else None


def diff_gate_of(context: FlowContext) -> DiffGate | None:
    """The recorded diff gate, or None when absent or malformed."""
    record = _gate_record(context, DIFF)
    return DiffGate.from_dict(record) if record is not None and record.get("reviewed_head") else None


def pipeline_gate_of(context: FlowContext) -> PipelineGate | None:
    """The recorded pipeline gate, or None when absent or malformed."""
    record = _gate_record(context, PIPELINE)
    return PipelineGate.from_dict(record) if record is not None and record.get("head_sha") else None


def diff_gate_holds(context: FlowContext, repo_dir: str, head: str | None) -> bool:
    """The diff gate is approved for ``head``: the reviewed head itself, or the same PR-own change fingerprint."""
    gate = diff_gate_of(context)
    if gate is None or not gate.approved or not head:
        return False
    if gate.reviewed_head == head or approval_covers(context, repo_dir, head).covers:
        return True
    current = approval_fingerprint_of(repo_dir, head, gate.base_branch) if gate.fingerprint else None
    return fingerprints_match(gate.fingerprint, current)


def pipeline_gate_holds(context: FlowContext, head: str | None) -> bool:
    """The pipeline is green on exactly ``head``: a full SHA comparison, never ancestry or a prefix."""
    gate = pipeline_gate_of(context)
    return gate is not None and bool(head) and gate.state == GREEN and gate.head_sha == head


def approved(context: FlowContext, repo_dir: str, head: str | None) -> bool:
    """The only definition of approval: the diff gate and the pipeline gate both hold on ``head``."""
    return diff_gate_holds(context, repo_dir, head) and pipeline_gate_holds(context, head)


def mark_pipeline_red(context: FlowContext, head_sha: str, failed_runs: Sequence[Mapping[str, Any]]) -> None:
    """Record a merged-head CI failure as a red pipeline gate on ``head_sha``; the diff gate survives it."""
    run_ids = tuple(str(run.get("run_id")) for run in failed_runs if run.get("run_id"))
    record_pipeline_gate(context, PipelineGate(head_sha, RED, run_ids, source="merged_head"))


def gates_summary(context: FlowContext) -> dict[str, Any]:
    """The JSON-safe gate records for the Stage 4 payload."""
    diff, pipeline = diff_gate_of(context), pipeline_gate_of(context)
    return {DIFF: diff.to_dict() if diff else None, PIPELINE: pipeline.to_dict() if pipeline else None}


def _run_ids(ci_status: Mapping[str, Any], state: str) -> tuple[str, ...]:
    checks = [c for c in ci_status.get("checks") or [] if isinstance(c, Mapping)]
    ids = (parse_run_id(c.get("link")) for c in checks if state != RED or c.get("bucket") == "fail")
    return tuple(dict.fromkeys(i for i in ids if i))


def record_iteration_gates(context: FlowContext, facts: IterationGates, verdict: ConsensusVerdict | None) -> None:
    """Record both gates for one iteration; ``verdict`` (their conjunction) is only checked for drift, never changed.

    The diff verdict takes ``ci_green=True`` on purpose: ``decide_consensus`` treats anything else as not approved, so
    it judges the reviewers, the blockers and mergeability alone.
    """
    diff_verdict = decide_returned_consensus(
        facts.reviewers, facts.ensemble_size, facts.mergeable, True, facts.blockers
    )
    diff_ok = diff_verdict is ConsensusVerdict.APPROVED
    fingerprint = facts.fingerprint_of(facts.head) if diff_ok and facts.fingerprint_of else None
    verdicts = tuple(str(r.verdict) for r in facts.reviewers)
    gate = DiffGate(diff_ok, facts.head, fingerprint, facts.base_branch, facts.iteration, facts.mode, verdicts)
    record_diff_gate(context, gate)
    state = pipeline_state_of(facts.ci_green, facts.ci_status)
    record_pipeline_gate(context, PipelineGate(facts.head, state, _run_ids(facts.ci_status, state)))
    if diff_ok and state == GREEN and verdict is not ConsensusVerdict.APPROVED:
        context.log(
            f"[Review Loop] Gate drift at iteration {facts.iteration}: both gates hold but verdict is {verdict}."
        )
