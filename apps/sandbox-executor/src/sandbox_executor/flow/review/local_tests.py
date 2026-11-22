"""Stage 4 local test measurement: when no CI registers and Stage 3 recorded no rate, run the test task here.

The result is cached per head commit, so the review loop measures once per head and re-measures only after the resolver
pushes a fix.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sandbox_executor.entrypoint.test_measurement import TestMeasurement, run_test_task
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.dispatch import _test_evidence
from sandbox_executor.flow.review.head_test_evidence import (
    TEST_SOURCE_CI_HEAD,
    TEST_SOURCE_RESOLVER_EVIDENCE,
    TEST_SOURCE_STAGE_3,
    HeadEvidenceDecisionInput,
    _attach_local_counts,
    _attach_stage_3_counts,
    _attribute_test_source,
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

# The test run; replaced in tests so no real ``uv run task test`` starts.
run_measurement: Callable[[str], TestMeasurement] = run_test_task


class _ReviewLocalTests:
    """Measures the project's tests in the review workspace, once per head commit."""

    def __init__(self, repo_dir: str, head_sha: str = "") -> None:
        self._repo_dir = repo_dir
        self._head_sha = head_sha
        self._cache: dict[str, _LocalTestOutcome] = {}

    def _head(self) -> str:
        res = run_git(["rev-parse", "HEAD"], cwd=self._repo_dir, check=False)
        return res.stdout.strip() if res.returncode == 0 and res.stdout.strip() else self._head_sha

    def measure(self) -> _LocalTestOutcome:
        """The measurement for the current head, running the test task only when this head is not yet cached."""
        head = self._head() or "unknown"
        if head not in self._cache:
            measurement = run_measurement(self._repo_dir)
            self._cache[head] = _LocalTestOutcome(measurement.rate, _failed(measurement), measurement)
        return self._cache[head]


def _default_pr_head(repo_dir: str, target: str, proxy_env: Mapping[str, str] | None = None) -> str | None:
    """The PR head commit for ``target`` via ``gh``, or None when it cannot be read."""
    if not target or not shutil.which("gh"):
        return None
    env = {**os.environ, **(proxy_env or {}), "DOCKER_CLI_HINTS": "false"}
    try:
        proc = subprocess.run(
            ["gh", "pr", "view", target, "--json", "headRefOid"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            env=env,
        )
        if proc.returncode == 0 and proc.stdout:
            data = json.loads(proc.stdout.strip())
            return str(data.get("headRefOid") or "") or None
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, AttributeError):
        return None
    return None


class _ReviewTestGate:
    """Stage 3's test evidence for the review loop, topped up by a local measurement when CI cannot supply it."""

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
        # The commit Stage 3 tested; without one its evidence applies to no head and the gate measures locally.
        self.stage_3_head = str(self._stage_3.get("head_sha") or "").strip()

        self._tester = _ReviewLocalTests(repo_dir)
        self._resolver_heads: set[str] = set()
        self._logged_evidence: set[tuple[str, str]] = set()
        self._pr_heads: dict[str, str | None] = {}

    def _get_pr_head(self, local_head: str) -> str | None:
        """The PR head commit, looked up once per local head (it only moves when the loop pushes)."""
        if local_head not in self._pr_heads:
            if self.pr_head is not None:
                self._pr_heads[local_head] = self.pr_head(self._target)
            else:
                self._pr_heads[local_head] = _default_pr_head(self._repo_dir, self._target, self._proxy_env)
        return self._pr_heads[local_head]

    def accept_resolver_evidence(self, head_sha: str, dispositions: Sequence[Any]) -> None:
        """Confirm a rejected FIND-CI with passing test evidence on this head via local re-measurement."""
        if not head_sha or not _has_passing_ci_rejection(dispositions):
            return
        if self._tester._head() != head_sha:
            # The resolver pushed: the checkout is no longer the head its evidence describes.
            return
        outcome = self._tester.measure()
        if not outcome.failed and outcome.rate is not None and not outcome.measurement.run_error:
            self._resolver_heads.add(head_sha)

    def needs_ci_registration(self) -> bool:
        """True when the current head has neither confirmed resolver evidence nor a Stage 3 measurement."""
        return _needs_ci_registration(
            self.stage_3_evidence, self.stage_3_head, self._tester._head(), self._resolver_heads
        )

    def advance_ledger_head(self, old_head: str, new_head: str) -> None:
        """Carry head-scoped evidence across a flow-authored ledger commit (no code changed)."""
        if not old_head or not new_head or old_head == new_head:
            return
        if old_head in self._resolver_heads:
            self._resolver_heads.add(new_head)
        if old_head in self._tester._cache:
            self._tester._cache[new_head] = self._tester._cache[old_head]
        if self.stage_3_head == old_head:
            self.stage_3_head = new_head

    def evidence(self, ci_status: dict[str, Any]) -> tuple[float | None, bool]:
        """The ``(test_pass_rate, tests_failed)`` for the CI gate judging the current head commit."""
        if self._context.dry_run:
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

        ci_status["test_source"] = source
        ci_status["test_head_sha"] = current_head

        sha7 = current_head[:7] if current_head else "unknown"
        if (current_head, source) not in self._logged_evidence:
            self._context.log(f"[Review Loop] Test evidence for {sha7}: {source} ({detail}).")
            self._logged_evidence.add((current_head, source))

        return rate, failed
