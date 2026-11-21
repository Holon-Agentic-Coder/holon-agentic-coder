"""Head test evidence selection for the review loop test gate.

Pure module with no subprocess calls: decides which test evidence to use for the current git head commit.
1. Resolver evidence (passing run confirmed locally on this head)
2. Green CI on this PR head SHA
3. Stage 3 on an unchanged head
4. Local test re-run (review_local)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sandbox_executor.entrypoint.test_measurement import TestMeasurement

TEST_SOURCE_STAGE_3 = "stage_3"
TEST_SOURCE_CI_HEAD = "ci_head"
TEST_SOURCE_REVIEW_LOCAL = "review_local"
TEST_SOURCE_RESOLVER_EVIDENCE = "resolver_evidence"
TEST_SOURCE_CI = "ci"


@dataclass(frozen=True)
class _LocalTestOutcome:
    """The review-local measurement as the loop consumes it: a rate (None when unmeasured) and whether it failed."""

    rate: float | None
    failed: bool
    measurement: TestMeasurement


def _failed(measurement: TestMeasurement) -> bool:
    """A measured rate below 1.0 or a non-zero exit of a completed run; an unrunnable run is unmeasured, not failed."""
    if measurement.run_error:
        return False
    return measurement.exit_code != 0 or (measurement.rate is not None and measurement.rate < 1.0)


@dataclass(frozen=True)
class HeadTestEvidence:
    """The evaluated test evidence for a specific git head commit."""

    rate: float | None
    failed: bool
    source: str
    head_sha: str
    detail: str = ""


@dataclass(frozen=True)
class HeadEvidenceDecisionInput:
    """Inputs to choose_evidence."""

    ci_status: dict[str, Any]
    stage_3_evidence: tuple[float | None, bool]
    stage_3_head: str
    current_head: str
    pr_head_sha: str | None = None
    resolver_heads: frozenset[str] = frozenset()


def _is_ci_head_passing(ci_status: dict[str, Any], current_head: str, pr_head_sha: str | None) -> bool:
    if ci_status.get("source") != "github_cli":
        return False
    if not current_head or not pr_head_sha or current_head != pr_head_sha:
        return False
    if not ci_status.get("all_passed"):
        return False
    return ci_status.get("pending", 0) == 0


def _is_stage_3_measured(stage_3_evidence: tuple[float | None, bool], stage_3_head: str, current_head: str) -> bool:
    rate, failed = stage_3_evidence
    if rate is None and not failed:
        return False
    return bool(stage_3_head) and current_head == stage_3_head


def choose_evidence(inp: HeadEvidenceDecisionInput) -> str:
    """Decide which test evidence source to use for the current head commit.

    Applying rules in priority order:
    1. Resolver evidence confirmed on current head -> resolver_evidence
    2. Green CI on PR head matching current head -> ci_head
    3. Stage 3 on an unchanged head when measured -> stage_3
    4. Otherwise (unmeasured, or head moved) -> review_local
    """
    current_head = inp.current_head
    if current_head and current_head in inp.resolver_heads:
        return TEST_SOURCE_RESOLVER_EVIDENCE

    if _is_ci_head_passing(inp.ci_status, current_head, inp.pr_head_sha):
        return TEST_SOURCE_CI_HEAD

    if _is_stage_3_measured(inp.stage_3_evidence, inp.stage_3_head, current_head):
        return TEST_SOURCE_STAGE_3

    return TEST_SOURCE_REVIEW_LOCAL


def _attribute_test_source(
    ci_status: dict[str, Any], stage_3: dict[str, Any] | None, review_local: _LocalTestOutcome | None
) -> None:
    """Record on the CI probe where its test evidence came from (``stage_3``, ``ci`` or ``review_local``)."""
    if ci_status.get("source") == "github_cli":
        ci_status["test_source"] = TEST_SOURCE_CI
        return
    if review_local is not None:
        m = review_local.measurement
        ci_status["test_source"] = TEST_SOURCE_REVIEW_LOCAL
        ci_status["test_counts"] = {"passed": m.passed, "failed": m.failed, "errored": m.errored}
        if m.run_error:
            ci_status["test_run_error"] = m.run_error
    elif stage_3 and (stage_3.get("test_pass_rate") is not None or stage_3.get("test_run_error")):
        ci_status["test_source"] = TEST_SOURCE_STAGE_3
        ci_status["test_counts"] = {
            "passed": stage_3.get("tests_passed"),
            "failed": stage_3.get("tests_failed"),
            "errored": stage_3.get("tests_errored"),
        }


def has_pass_marker(text: str) -> bool:
    """True when the text reports a pass: the word ``passed`` or a zero failure count (not ``10 failed``)."""
    clean = text.lower()
    return bool(re.search(r"\bpassed\b", clean) or re.search(r"(?<!\d)0 failed\b", clean))
