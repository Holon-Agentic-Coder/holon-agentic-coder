from __future__ import annotations

import functools
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sandbox_executor.entrypoint.test_failure_classify import BaseSnapshot, run_failing_ids
from sandbox_executor.entrypoint.test_failure_loop import (
    LoopOutcome,
    LoopPorts,
    TestFailureLoop,
    measure_selection,
)
from sandbox_executor.entrypoint.test_failure_record import (
    BLOCKED_CLASSIFICATIONS,
    LEDGER_RELPATH,
    STAGE_4,
    record_test_failure,
)
from sandbox_executor.entrypoint.test_measurement import TestMeasurement
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.dispatch import _test_evidence
from sandbox_executor.flow.github.pr_head import pr_head_sha
from sandbox_executor.flow.review.head_test_evidence import (
    TEST_SOURCE_CI_HEAD,
    TEST_SOURCE_RESOLVER_EVIDENCE,
    TEST_SOURCE_STAGE_3,
    HeadEvidenceDecisionInput,
    _attach_local_counts,
    _attach_stage_3_counts,
    _attribute_test_source,
    _discard_stale_github_pass,
    _failed,
    _has_passing_ci_rejection,
    _LocalTestOutcome,
    _needs_ci_registration,
    _needs_pr_head,
    choose_evidence,
)
from sandbox_executor.flow.review.head_test_evidence import (
    has_pass_marker as has_pass_marker,
)
from sandbox_executor.git import run_git

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _MeasurementWithOutput(TestMeasurement):
    full_output: str = ""


def _measure_with_short_summary(repo_dir: str) -> TestMeasurement:
    measurement, output = measure_selection(repo_dir)
    return _MeasurementWithOutput(**vars(measurement), full_output=output)


# why: a seam tests replace, so no real ``uv run task test`` starts.
run_measurement: Callable[[str], TestMeasurement] = _measure_with_short_summary

FailureLoopFactory = Callable[[], "TestFailureLoop | None"]


def _output_of(measurement: TestMeasurement) -> str:
    return getattr(measurement, "full_output", "") or measurement.output_excerpt


def _measured_with_output(repo_dir: str) -> tuple[TestMeasurement, str]:
    measurement = run_measurement(repo_dir)
    return measurement, _output_of(measurement)


def _git_head(repo_dir: str) -> str:
    res = run_git(["rev-parse", "HEAD"], cwd=repo_dir, check=False)
    return res.stdout.strip() if res.returncode == 0 else ""


def _default_review_failure_loop(repo_dir: str, context: FlowContext) -> TestFailureLoop:
    branch = context.execution_branch or context.intent_branch or ""
    ports = LoopPorts(
        run_selection=functools.partial(_measured_with_output, repo_dir),
        rerun_ids=functools.partial(run_failing_ids, cwd=repo_dir),
        base_rerun=BaseSnapshot(repo_dir, str(context.intent_data.get("target_branch") or "main")),
        record=functools.partial(
            record_test_failure,
            os.path.join(context.target_worktree, *LEDGER_RELPATH),
            branch=branch,
            execution_id=next((part for part in branch.split("/") if part.startswith("E-")), ""),
        ),
        stage=STAGE_4,
        head_sha=functools.partial(_git_head, repo_dir),
        # why: the Stage 4 resolver is the fix turn; the gate only feeds it the evidence.
        fix_turn=None,
    )
    return TestFailureLoop(ports)


# why: a seam tests replace, so a scripted failing head starts no diagnosis re-run.
_review_failure_loop: Callable[[str, FlowContext], TestFailureLoop | None] = _default_review_failure_loop


class _ReviewLocalTests:
    def __init__(self, repo_dir: str, head_sha: str = "", *, failure_loop: FailureLoopFactory | None = None) -> None:
        self._repo_dir = repo_dir
        self._head_sha = head_sha
        self._cache: dict[str, _LocalTestOutcome] = {}
        self._failure_loop = failure_loop
        self.loop_outcomes: dict[str, LoopOutcome] = {}

    def _head(self) -> str:
        res = run_git(["rev-parse", "HEAD"], cwd=self._repo_dir, check=False)
        return res.stdout.strip() if res.returncode == 0 and res.stdout.strip() else self._head_sha

    def measure(self) -> _LocalTestOutcome:
        head = self._head() or "unknown"
        if head not in self._cache:
            measurement = run_measurement(self._repo_dir)
            if self._failure_loop is not None and _failed(measurement) and (loop := self._failure_loop()) is not None:
                measurement = self._diagnosed(head, loop, measurement)
            self._cache[head] = _LocalTestOutcome(measurement.rate, _failed(measurement), measurement)
        return self._cache[head]

    def _diagnosed(self, head: str, loop: TestFailureLoop, measurement: TestMeasurement) -> TestMeasurement:
        try:
            self.loop_outcomes[head] = loop.run(measurement, _output_of(measurement))
        except Exception:
            # why: a broken diagnosis must not end the review; the failed measurement still feeds the CI finding.
            logger.exception("Stage 4 test failure loop failed for %s", head[:7])
            return measurement
        return self.loop_outcomes[head].measurement


def _attach_test_failure(ci_status: dict[str, Any], outcome: LoopOutcome | None) -> None:
    if outcome is None:
        return
    ci_status["test_failure"] = {
        "classification": outcome.classification,
        "status": outcome.status,
        "failing_ids": list(outcome.failing_ids),
        "excerpts": dict(outcome.excerpts),
        "signature": outcome.signature,
        "rows": [row.get("action_id") for row in outcome.rows],
    }
    if outcome.status == "blocked" and outcome.classification in BLOCKED_CLASSIFICATIONS:
        ci_status["test_failure"]["blocked_evidence"] = outcome.evidence_text


def _default_pr_head(repo_dir: str, target: str, proxy_env: Mapping[str, str] | None = None) -> str | None:
    return pr_head_sha(repo_dir, target, proxy_env)


class _ReviewTestGate:
    def __init__(
        self,
        context: FlowContext,
        repo_dir: str,
        pr_head: Callable[[str], str | None] | None = None,
        target: str = "",
        proxy_env: Mapping[str, str] | None = None,
    ) -> None:
        exec_result = context.stage_results.get("execute")
        self._context = context
        self._repo_dir = repo_dir
        self.pr_head = pr_head
        self._target = target or context.execution_branch or context.intent_branch or ""
        self._proxy_env = dict(proxy_env or {})
        self._stage_3 = (exec_result.payload if exec_result else None) or {}
        rate, exit_code = _test_evidence(exec_result)
        self.stage_3_evidence = (rate, exit_code != 0 or (rate is not None and rate < 1.0))
        # why: without a tested commit, Stage 3 evidence applies to no head and the gate measures locally.
        self.stage_3_head = str(self._stage_3.get("head_sha") or "").strip()

        self._tester = _ReviewLocalTests(repo_dir, failure_loop=lambda: _review_failure_loop(repo_dir, context))
        self._resolver_heads: set[str] = set()
        self._logged_evidence: set[tuple[str, str]] = set()
        self._pr_heads: dict[str, str | None] = {}

    def _get_pr_head(self, local_head: str) -> str | None:
        if local_head and self._pr_heads.get(local_head) == local_head:
            return local_head
        reader = self.pr_head or (lambda t: _default_pr_head(self._repo_dir, t, self._proxy_env))
        # why: only a lookup matched to the local head is cached, so stale or failed lookups retry.
        if (sha := reader(self._target)) == local_head and local_head:
            self._pr_heads[local_head] = sha
        return sha

    def accept_resolver_evidence(self, head_sha: str, dispositions: Sequence[Any]) -> None:
        if not head_sha or not _has_passing_ci_rejection(dispositions):
            return
        if self._tester._head() != head_sha:
            # why: the resolver pushed, so the checkout is no longer the head its evidence describes.
            return
        outcome = self._tester.measure()
        if not outcome.failed and outcome.rate is not None and not outcome.measurement.run_error:
            self._resolver_heads.add(head_sha)

    def needs_ci_registration(self) -> bool:
        return _needs_ci_registration(
            self.stage_3_evidence, self.stage_3_head, self._tester._head(), self._resolver_heads
        )

    def advance_ledger_head(self, old_head: str, new_head: str) -> None:
        if not old_head or not new_head or old_head == new_head:
            return
        if old_head in self._resolver_heads:
            self._resolver_heads.add(new_head)
        if old_head in self._tester._cache:
            self._tester._cache[new_head] = self._tester._cache[old_head]
        if self.stage_3_head == old_head:
            self.stage_3_head = new_head

    def evidence(self, ci_status: dict[str, Any], dry_run: bool | None = None) -> tuple[float | None, bool]:
        if bool(dry_run or self._context.dry_run):
            _attribute_test_source(ci_status, self._stage_3, None)
            return self.stage_3_evidence

        current_head = self._tester._head()
        need_pr = _needs_pr_head(ci_status, current_head, self._resolver_heads)
        pr_head = self._get_pr_head(current_head) if need_pr else ""
        source = choose_evidence(
            HeadEvidenceDecisionInput(
                ci_status=ci_status,
                stage_3_evidence=self.stage_3_evidence,
                stage_3_head=self.stage_3_head,
                current_head=current_head,
                pr_head_sha=pr_head,
                resolver_heads=frozenset(self._resolver_heads),
            )
        )
        _discard_stale_github_pass(ci_status, source)

        if source == TEST_SOURCE_RESOLVER_EVIDENCE:
            rate, failed, detail = 1.0, False, "passing run confirmed locally"
            if (cached := self._tester._cache.get(current_head)) is not None:
                _attach_local_counts(ci_status, cached)
        elif source == TEST_SOURCE_CI_HEAD:
            rate, failed, detail = 1.0, False, "all_passed=True"
        elif source == TEST_SOURCE_STAGE_3:
            rate, failed = self.stage_3_evidence
            detail = f"rate {rate}, failed={failed}"
            _attach_stage_3_counts(ci_status, self._stage_3)
        else:
            outcome = self._tester.measure()
            rate, failed = outcome.rate, outcome.failed
            detail = outcome.measurement.run_error or f"rate {rate}, failed={failed}"
            _attach_local_counts(ci_status, outcome)
            _attach_test_failure(ci_status, self._tester.loop_outcomes.get(current_head))

        ci_status["test_source"] = source
        ci_status["test_head_sha"] = current_head

        sha7 = current_head[:7] if current_head else "unknown"
        if (current_head, source) not in self._logged_evidence:
            self._context.log(f"[Review Loop] Test evidence for {sha7}: {source} ({detail}).")
            self._logged_evidence.add((current_head, source))

        return rate, failed
