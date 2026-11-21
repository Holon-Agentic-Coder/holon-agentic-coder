"""Head test evidence selection for the review loop test gate.

Pure module with no subprocess calls: decides which test evidence to use for the current git head commit.
1. Resolver evidence (passing run confirmed locally on this head)
2. Green CI on this PR head SHA
3. Stage 3 on an unchanged head
4. Local test re-run (review_local)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sandbox_executor.entrypoint.test_measurement import TestMeasurement

TEST_SOURCE_STAGE_3 = "stage_3"
TEST_SOURCE_CI_HEAD = "ci_head"
TEST_SOURCE_REVIEW_LOCAL = "review_local"
TEST_SOURCE_RESOLVER_EVIDENCE = "resolver_evidence"
TEST_SOURCE_CI = "ci"

# Public aliases
STAGE_3 = TEST_SOURCE_STAGE_3
CI_HEAD = TEST_SOURCE_CI_HEAD
REVIEW_LOCAL = TEST_SOURCE_REVIEW_LOCAL
RESOLVER_EVIDENCE = TEST_SOURCE_RESOLVER_EVIDENCE


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


def _is_stage_3_passing(stage_3_evidence: tuple[float | None, bool], stage_3_head: str, current_head: str) -> bool:
    rate, failed = stage_3_evidence
    if failed or rate is None or rate < 1.0:
        return False
    return current_head == stage_3_head


def choose_evidence(*args: Any, **kwargs: Any) -> str:
    """Decide which test evidence source to use for the current head commit.

    Applying rules in priority order:
    1. Resolver evidence confirmed on current head -> resolver_evidence
    2. Green CI on PR head matching current head -> ci_head
    3. Stage 3 passed on unchanged head -> stage_3
    4. Otherwise (Stage 3 failed, unmeasured, or head moved) -> review_local
    """
    if args and isinstance(args[0], HeadEvidenceDecisionInput):
        inp = args[0]
        ci_status = inp.ci_status
        stage_3_evidence = inp.stage_3_evidence
        stage_3_head = inp.stage_3_head
        current_head = inp.current_head
        pr_head_sha = inp.pr_head_sha
        resolver_heads = inp.resolver_heads
    else:
        ci_status = args[0] if len(args) > 0 else kwargs.get("ci_status", {})
        stage_3_evidence = args[1] if len(args) > 1 else kwargs.get("stage_3_evidence", (None, False))
        stage_3_head = args[2] if len(args) > 2 else kwargs.get("stage_3_head", "")
        current_head = args[3] if len(args) > 3 else kwargs.get("current_head", "")
        pr_head_sha = args[4] if len(args) > 4 else kwargs.get("pr_head_sha")
        resolver_heads = args[5] if len(args) > 5 else kwargs.get("resolver_heads", frozenset())

    if current_head and current_head in resolver_heads:
        return TEST_SOURCE_RESOLVER_EVIDENCE

    if _is_ci_head_passing(ci_status, current_head, pr_head_sha):
        return TEST_SOURCE_CI_HEAD

    if _is_stage_3_passing(stage_3_evidence, stage_3_head, current_head):
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


def _applies(ci_status: dict[str, Any], stage_3_rate: float | None, stage_3_failed: bool) -> bool:
    """True when the probe fell back to ``local_tests`` and Stage 3 left the tests unmeasured."""
    return ci_status.get("source") == "local_tests" and stage_3_rate is None and not stage_3_failed


def has_pass_marker(text: str) -> bool:
    clean = text.lower()
    return "passed" in clean or "0 failed" in clean
