"""Head test evidence selection for the review loop test gate.

Pure module with no subprocess calls: decides which test evidence to use for the current git head commit.
1. Resolver evidence (passing run confirmed locally on this head)
2. Green CI on this PR head SHA
3. Stage 3 on an unchanged head
4. Local test re-run (review_local)
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sandbox_executor.entrypoint.test_measurement import TestMeasurement
from sandbox_executor.resolver_evidence import names_checked_code

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


def _needs_pr_head(ci_status: dict[str, Any], current_head: str, resolver_heads: frozenset[str] | set[str]) -> bool:
    """True only when green GitHub CI could decide the head and resolver evidence does not already cover it."""
    if not current_head or current_head in resolver_heads:
        return False
    return (
        ci_status.get("source") == "github_cli"
        and bool(ci_status.get("all_passed"))
        and ci_status.get("pending", 0) == 0
    )


def _needs_ci_registration(
    stage_3_evidence: tuple[float | None, bool],
    stage_3_head: str,
    current_head: str,
    resolver_heads: frozenset[str] | set[str],
) -> bool:
    """True when the current head has neither confirmed resolver evidence nor a Stage 3 measurement."""
    if current_head and current_head in resolver_heads:
        return False
    return not _is_stage_3_measured(stage_3_evidence, stage_3_head, current_head)


def _attach_local_counts(ci_status: dict[str, Any], outcome: _LocalTestOutcome) -> None:
    """Populate ``test_counts`` (and any ``test_run_error``) on ``ci_status`` from a local measurement."""
    m = outcome.measurement
    ci_status["test_counts"] = {"passed": m.passed, "failed": m.failed, "errored": m.errored}
    if m.run_error:
        ci_status["test_run_error"] = m.run_error


def _attach_stage_3_counts(ci_status: dict[str, Any], stage_3: dict[str, Any] | None) -> None:
    """Populate ``test_counts`` on ``ci_status`` when Stage 3 recorded counts or a run error."""
    if stage_3 and (stage_3.get("test_pass_rate") is not None or stage_3.get("test_run_error")):
        ci_status["test_counts"] = {
            "passed": stage_3.get("tests_passed"),
            "failed": stage_3.get("tests_failed"),
            "errored": stage_3.get("tests_errored"),
        }


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
        ci_status["test_source"] = TEST_SOURCE_REVIEW_LOCAL
        _attach_local_counts(ci_status, review_local)
    elif stage_3 and (stage_3.get("test_pass_rate") is not None or stage_3.get("test_run_error")):
        ci_status["test_source"] = TEST_SOURCE_STAGE_3
        _attach_stage_3_counts(ci_status, stage_3)


def has_pass_marker(text: str) -> bool:
    """True when the text reports a pass: the word ``passed`` or a zero failure count (not ``10 failed``)."""
    clean = text.lower()
    return bool(re.search(r"\bpassed\b", clean) or re.search(r"(?<!\d)0 failed\b", clean))


def _disp_field(disp: Any, name: str) -> str:
    return str(getattr(disp, name, None) or (disp.get(name) if isinstance(disp, dict) else "") or "")


def _has_passing_ci_rejection(dispositions: Sequence[Any]) -> bool:
    """True when a disposition rejects a ``FIND-CI`` finding with code/test evidence containing a pass marker."""
    for disp in dispositions:
        ev = _disp_field(disp, "evidence")
        if (
            _disp_field(disp, "id").startswith("FIND-CI")
            and _disp_field(disp, "disposition") == "rejected"
            and names_checked_code(ev)
            and has_pass_marker(ev)
        ):
            return True
    return False
