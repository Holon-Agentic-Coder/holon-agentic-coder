"""Stage 4 local test measurement: when no CI registers and Stage 3 recorded no rate, run the test task here.

The result is cached per head commit, so the review loop measures once per head and re-measures only after the resolver
pushes a fix.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from typing import Any

from sandbox_executor.entrypoint.test_measurement import TestMeasurement, run_test_task
from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.dispatch import _test_evidence
from sandbox_executor.flow.review.head_test_evidence import (
    TEST_SOURCE_CI_HEAD,
    TEST_SOURCE_RESOLVER_EVIDENCE,
    TEST_SOURCE_STAGE_3,
    _attribute_test_source,
    _failed,
    _LocalTestOutcome,
    choose_evidence,
    has_pass_marker,
)
from sandbox_executor.git import run_git
from sandbox_executor.resolver_evidence import names_checked_code

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


def _default_pr_head(repo_dir: str, target: str) -> str | None:
    if not target or not shutil.which("gh"):
        return None
    try:
        proc = subprocess.run(
            ["gh", "pr", "view", target, "--json", "headRefOid"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if proc.returncode == 0 and proc.stdout:
            data = json.loads(proc.stdout.strip())
            return str(data.get("headRefOid") or "") or None
    except Exception:
        return None
    return None


class _ReviewTestGate:
    """Stage 3's test evidence for the review loop, topped up by a local measurement when CI cannot supply it."""

    def __init__(
        self,
        context: FlowContext,
        repo_dir: str,
        pr_head: Callable[..., str | None] | None = None,
    ) -> None:
        exec_result = context.stage_results.get("execute")
        self._context = context
        self._repo_dir = repo_dir
        self.pr_head = pr_head
        self._target = context.execution_branch or context.intent_branch or ""
        self._stage_3 = (exec_result.payload if exec_result else None) or {}
        rate, exit_code = _test_evidence(exec_result)
        self.stage_3_evidence = (rate, exit_code != 0 or (rate is not None and rate < 1.0))
        exec_payload = self._stage_3
        head_key = (
            exec_payload.get("head_sha")
            or exec_payload.get("commit_sha")
            or exec_payload.get("head")
            or exec_payload.get("commit")
            or exec_payload.get("head_oid")
        )
        if head_key:
            self.stage_3_head = str(head_key).strip()
        else:
            res = run_git(["rev-parse", "HEAD"], cwd=repo_dir, check=False)
            self.stage_3_head = res.stdout.strip() if res.returncode == 0 else ""

        self._tester = _ReviewLocalTests(repo_dir, head_sha=self.stage_3_head)
        self._resolver_heads: set[str] = set()
        self._logged_evidence: set[tuple[str, str]] = set()

    def _get_pr_head(self) -> str | None:
        if self.pr_head is not None:
            try:
                return self.pr_head(self._target)
            except TypeError:
                return self.pr_head()
        return _default_pr_head(self._repo_dir, self._target)

    def accept_resolver_evidence(self, head_sha: str, dispositions: Sequence[Any]) -> None:
        """Confirm a rejected FIND-CI with passing test evidence on this head via local re-measurement."""
        if not head_sha:
            return
        matches = False
        for d in dispositions:
            disp_id = str(getattr(d, "id", None) or (d.get("id") if isinstance(d, dict) else "") or "")
            disp_val = str(
                getattr(d, "disposition", None) or (d.get("disposition") if isinstance(d, dict) else "") or ""
            )
            ev = str(getattr(d, "evidence", None) or (d.get("evidence") if isinstance(d, dict) else "") or "")
            if (
                disp_id.startswith("FIND-CI")
                and disp_val == "rejected"
                and names_checked_code(ev)
                and has_pass_marker(ev)
            ):
                matches = True
                break
        if not matches:
            return
        outcome = self._tester.measure()
        if not outcome.failed and outcome.rate is not None and not outcome.measurement.run_error:
            self._resolver_heads.add(head_sha)

    def evidence(self, ci_status: dict[str, Any]) -> tuple[float | None, bool]:
        """The ``(test_pass_rate, tests_failed)`` for the CI gate judging the current head commit."""
        if self._context.dry_run:
            _attribute_test_source(ci_status, self._stage_3, None)
            return self.stage_3_evidence

        current_head = self._tester._head() or self.stage_3_head
        pr_head_sha = self._get_pr_head()
        source = choose_evidence(
            ci_status,
            self.stage_3_evidence,
            self.stage_3_head,
            current_head,
            pr_head_sha,
            frozenset(self._resolver_heads),
        )

        outcome: _LocalTestOutcome | None = None
        if source == TEST_SOURCE_RESOLVER_EVIDENCE:
            rate, failed = 1.0, False
            detail = "passing run confirmed locally"
        elif source == TEST_SOURCE_CI_HEAD:
            rate, failed = 1.0, False
            detail = "all_passed=True"
        elif source == TEST_SOURCE_STAGE_3:
            rate, failed = self.stage_3_evidence
            detail = f"rate {rate}, failed={failed}"
            if self._stage_3 and (
                self._stage_3.get("test_pass_rate") is not None or self._stage_3.get("test_run_error")
            ):
                ci_status["test_counts"] = {
                    "passed": self._stage_3.get("tests_passed"),
                    "failed": self._stage_3.get("tests_failed"),
                    "errored": self._stage_3.get("tests_errored"),
                }
        else:
            outcome = self._tester.measure()
            rate, failed = outcome.rate, outcome.failed
            detail = outcome.measurement.run_error or f"rate {rate}, failed={failed}"
            m = outcome.measurement
            ci_status["test_counts"] = {"passed": m.passed, "failed": m.failed, "errored": m.errored}
            if m.run_error:
                ci_status["test_run_error"] = m.run_error

        ci_status["test_source"] = source
        ci_status["test_head_sha"] = current_head

        sha7 = current_head[:7] if current_head else "unknown"
        if (current_head, source) not in self._logged_evidence:
            self._context.log(f"[Review Loop] Test evidence for {sha7}: {source} ({detail}).")
            self._logged_evidence.add((current_head, source))

        return rate, failed
