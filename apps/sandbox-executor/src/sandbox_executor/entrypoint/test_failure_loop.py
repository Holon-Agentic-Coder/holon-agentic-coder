from __future__ import annotations

import math
import os
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sandbox_executor.entrypoint.test_failure_classify import (
    BaseRerun,
    Diagnosis,
    FailureClass,
    Rerun,
    ShortSummaryRunner,
    diagnose,
)
from sandbox_executor.entrypoint.test_failure_record import (
    TestFailureEvidence,
    environment_summary,
    excerpts_by_test,
    failing_ids_from_output,
    failure_signature,
)
from sandbox_executor.entrypoint.test_measurement import TestMeasurement, run_test_task
from sandbox_executor.model_tiers import next_tier

# why: each full selection can take up to the 900 s test timeout, so the loop must stay inside the stage budget.
MAX_FLAKY_RERUNS = 2
MAX_FIX_TURNS = 4
MAX_DIAGNOSES = MAX_FIX_TURNS + 2
EVIDENCE_IDS_SHOWN = 5
EVIDENCE_EXCERPT_CHARS = 400
BLOCKED_ON_NONE = "none"
PARKED_STATUSES = frozenset({"blocked", "exhausted"})
_BEAN_ID_RE = re.compile(r"\b(?:BEAN-[A-Za-z0-9_-]+|bean[-_][A-Za-z0-9_-]+)\b")
_PR_REF_RE = re.compile(r"\bPR\s*(?:#|-)(\d+)\b", re.IGNORECASE)

FIX_PROMPT_TEMPLATE = """The project's test suite fails after your change. Classification: {classification}.

Failing tests:
{ids}

Redacted failure excerpts:
{excerpts}

Fix the code so the listed tests pass. Do not weaken, skip or delete the tests unless the plan requires it.
Before you finish, run `uv run task lint` and `uv run pytest {id_args}` in the foreground; both must pass.
Never background a command.
"""


@dataclass(frozen=True)
class FixRequest:
    classification: str
    failing_ids: tuple[str, ...]
    excerpts: Mapping[str, str]
    signature: str
    tier: str | None
    iteration: int


@dataclass
class LoopOutcome:
    status: str
    measurement: TestMeasurement
    classification: str | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    blocked_reason: str | None = None
    final_tier: str | None = None
    failing_ids: tuple[str, ...] = ()
    excerpts: Mapping[str, str] = field(default_factory=dict)
    signature: str = ""
    blocked_on: str | None = None
    parked_record: dict[str, Any] | None = None

    @property
    def parked(self) -> bool:
        return self.parked_record is not None

    @property
    def evidence_text(self) -> str:
        shown = ", ".join(self.failing_ids[:EVIDENCE_IDS_SHOWN]) or "none parsed"
        more = len(self.failing_ids) - EVIDENCE_IDS_SHOWN
        excerpt = next(iter(self.excerpts.values()), "")[:EVIDENCE_EXCERPT_CHARS]
        row_ids = ", ".join(str(row.get("action_id")) for row in self.rows) or "none"
        reason = self.blocked_reason or "no reason"
        return (
            f"test failure {self.classification or 'unclassified'} ({self.status}): {reason}"
            f"; failing: {shown}{f' (+{more} more)' if more > 0 else ''}; ledger rows: {row_ids}"
            + (f"; excerpt: {excerpt}" if excerpt else "")
        )


SelectionRun = Callable[[], "tuple[TestMeasurement, str]"]
FixTurn = Callable[[FixRequest], object]
RecordFn = Callable[[TestFailureEvidence], "dict[str, Any] | None"]


@dataclass(frozen=True)
class LoopPorts:
    run_selection: SelectionRun
    rerun_ids: Rerun
    base_rerun: BaseRerun
    record: RecordFn
    stage: str
    head_sha: Callable[[], str] = lambda: ""
    fix_turn: FixTurn | None = None
    ladder: tuple[str, ...] = ()
    start_tier: str | None = None
    remaining_seconds: Callable[[], float] = lambda: math.inf
    environment: Callable[[], Mapping[str, str]] = lambda: dict(os.environ)
    blocker_map: Mapping[str, str] = field(default_factory=dict)
    checkpoint_path: str | None = None


@dataclass
class _Case:
    measurement: TestMeasurement
    failing_ids: tuple[str, ...]
    excerpts: Mapping[str, str]
    signature: str
    diagnosis: Diagnosis | None = None


def measurement_passed(measurement: TestMeasurement) -> bool:
    return measurement.run_error is None and measurement.exit_code == 0 and (measurement.rate or 0.0) >= 1.0


def measure_selection(workspace: str, runner: ShortSummaryRunner | None = None) -> tuple[TestMeasurement, str]:
    summary_runner = runner or ShortSummaryRunner()
    measurement = run_test_task(workspace, runner=summary_runner)
    return measurement, summary_runner.last_output or measurement.output_excerpt


def fix_prompt(request: FixRequest) -> str:
    return FIX_PROMPT_TEMPLATE.format(
        classification=request.classification,
        ids="\n".join(f"- {test_id}" for test_id in request.failing_ids) or "- (no test ids parsed)",
        excerpts="\n\n".join(request.excerpts.values()) or "(none)",
        id_args=" ".join(request.failing_ids) or "<the failing tests>",
    )


def _case_of(measurement: TestMeasurement, output: str) -> _Case:
    ids = failing_ids_from_output(output)
    excerpts = excerpts_by_test(output, ids)
    return _Case(measurement, ids, excerpts, failure_signature(ids, excerpts))


def _extract_blocker_token(text: str) -> str | None:
    if (bean := _BEAN_ID_RE.search(text)) is not None:
        return bean.group(0)
    if (pr := _PR_REF_RE.search(text)) is not None:
        return f"PR#{pr.group(1)}"
    return None


def resolve_blocked_on(
    case: _Case, classification: str | None, reason: str | None, blocker_map: Mapping[str, str]
) -> str:
    suspects = case.diagnosis.environment_suspects if case.diagnosis else ()
    keys = (case.signature, *case.failing_ids, classification or "", *suspects)
    for key in keys:
        if key and (mapped := str(blocker_map.get(key) or "").strip()):
            return mapped
    for text in (reason or "", *suspects, *case.excerpts.values()):
        if text and (token := _extract_blocker_token(str(text))) is not None:
            return token
    return BLOCKED_ON_NONE


def build_parked_record(outcome: LoopOutcome, checkpoint_path: str | None = None) -> dict[str, Any]:
    return {
        "status": "parked",
        "outcome_status": outcome.status,
        "blocked_on": outcome.blocked_on or BLOCKED_ON_NONE,
        "classification": outcome.classification,
        "failing_ids": list(outcome.failing_ids),
        "signature": outcome.signature,
        "evidence": outcome.evidence_text,
        "reason": outcome.blocked_reason,
        "final_tier": outcome.final_tier,
        "rows": [dict(row) for row in outcome.rows],
        "checkpoint_path": checkpoint_path,
    }


class TestFailureLoop:
    __test__ = False

    def __init__(self, ports: LoopPorts) -> None:
        self.ports = ports
        self.run_id = uuid.uuid4().hex[:12]
        self.rows: list[dict[str, Any]] = []
        self.tier = ports.start_tier
        self.fix_turns = 0
        self.flaky_seen = False

    def run(self, first: TestMeasurement, first_output: str) -> LoopOutcome:
        if measurement_passed(first):
            return LoopOutcome("passed", first)
        if first.rate is None:
            return self._unmeasured(first)
        case = _case_of(first, first_output)
        for _ in range(MAX_DIAGNOSES):
            step = self._act(self._diagnose(case))
            if isinstance(step, LoopOutcome):
                return step
            case = step
        return self._finish("exhausted", case, "diagnosis bound reached")

    def _record(self, case: _Case, classification: str | None, outcome: str = "failure", base_check: str = "") -> None:
        evidence = TestFailureEvidence(
            run_id=self.run_id,
            stage=self.ports.stage,
            head_sha=self.ports.head_sha(),
            failing_ids=case.failing_ids,
            excerpts=case.excerpts,
            environment=environment_summary(self.ports.environment()),
            classification=classification,
            iteration=self.fix_turns,
            model_tier=self.tier,
            base_check=base_check or (case.diagnosis.base_check if case.diagnosis else "not_run"),
            outcome=outcome,
        )
        if (row := self.ports.record(evidence)) is not None:
            self.rows.append(row)

    def _finish(
        self, status: str, case: _Case, reason: str | None = None, *, classification: str | None = None
    ) -> LoopOutcome:
        resolved = classification or (case.diagnosis.classification.value if case.diagnosis else None)
        outcome = LoopOutcome(
            status,
            case.measurement,
            resolved,
            list(self.rows),
            reason,
            self.tier,
            case.failing_ids,
            case.excerpts,
            case.signature,
        )
        if status in PARKED_STATUSES:
            outcome.blocked_on = resolve_blocked_on(case, resolved, reason, self.ports.blocker_map)
            outcome.parked_record = build_parked_record(outcome, self.ports.checkpoint_path)
        return outcome

    def _unmeasured(self, measurement: TestMeasurement) -> LoopOutcome:
        case = _Case(measurement, (), {"run_error": measurement.run_error or ""}, "")
        self._record(case, FailureClass.ENVIRONMENT.value)
        return self._finish(
            "blocked",
            case,
            f"tests could not be measured: {measurement.run_error}",
            classification=FailureClass.ENVIRONMENT.value,
        )

    def _diagnose(self, case: _Case) -> _Case:
        case.diagnosis = diagnose(
            case.failing_ids, self.ports.rerun_ids, self.ports.base_rerun, self.ports.environment()
        )
        self._record(case, case.diagnosis.classification.value)
        return case

    def _act(self, case: _Case) -> LoopOutcome | _Case:
        classification = case.diagnosis.classification if case.diagnosis else FailureClass.INTRODUCED
        if classification == FailureClass.FLAKY:
            return self._retry_flaky(case)
        if classification == FailureClass.INTRODUCED:
            return self._fix(case)
        suspects = ", ".join(case.diagnosis.environment_suspects) if case.diagnosis else ""
        detail = (
            f"; environment suspects: {suspects}" if suspects and classification == FailureClass.ENVIRONMENT else ""
        )
        return self._finish("blocked", case, f"{classification.value} failure, not caused by this change{detail}")

    def _retry_flaky(self, case: _Case) -> LoopOutcome | _Case:
        if self.flaky_seen:
            return self._finish("blocked", case, "persistent flakiness")
        self.flaky_seen = True
        for _ in range(MAX_FLAKY_RERUNS):
            rerun = self.ports.rerun_ids(case.failing_ids, self.ports.environment())
            self._record(case, FailureClass.FLAKY.value, "success" if rerun.passed else "failure")
            if not rerun.passed:
                return self._finish("blocked", case, "flaky test failed again on re-run")
        measurement, output = self.ports.run_selection()
        if measurement_passed(measurement):
            case.measurement = measurement
            return self._finish("flaky_passed", case)
        return _case_of(measurement, output)

    def _fix(self, case: _Case) -> LoopOutcome | _Case:
        if self.ports.fix_turn is None:
            return self._finish("blocked", case, "no fix runner available")
        while self.fix_turns < MAX_FIX_TURNS and self.ports.remaining_seconds() > 0:
            after = self._fix_once(case, self.ports.fix_turn)
            if isinstance(after, LoopOutcome) or after.signature != case.signature:
                return after
            if (tier := next_tier(self.ports.ladder, self.tier)) is None:
                return self._finish("exhausted", after, "the failure repeats and the model ladder is exhausted")
            self.tier, case = tier, after
        return self._finish("exhausted", case, "fix turn or time bound reached")

    def _fix_once(self, case: _Case, fix_turn: FixTurn) -> LoopOutcome | _Case:
        self.fix_turns += 1
        request = FixRequest(
            FailureClass.INTRODUCED.value, case.failing_ids, case.excerpts, case.signature, self.tier, self.fix_turns
        )
        try:
            fix_turn(request)
        except Exception as exc:
            return self._finish("blocked", case, f"fix turn failed: {exc}")
        measurement, output = self.ports.run_selection()
        if measurement_passed(measurement):
            case.measurement = measurement
            self._record(case, FailureClass.INTRODUCED.value, "success")
            return self._finish("passed", case)
        if measurement.rate is None:
            return self._unmeasured(measurement)
        after = _case_of(measurement, output)
        after.diagnosis = case.diagnosis
        self._record(after, FailureClass.INTRODUCED.value)
        return after
