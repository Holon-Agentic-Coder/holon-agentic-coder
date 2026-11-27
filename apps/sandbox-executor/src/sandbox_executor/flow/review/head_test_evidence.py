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
    rate: float | None
    failed: bool
    measurement: TestMeasurement


def _failed(measurement: TestMeasurement) -> bool:
    # why: an unrunnable run is unmeasured, not failed.
    if measurement.run_error:
        return False
    return measurement.exit_code != 0 or (measurement.rate is not None and measurement.rate < 1.0)


@dataclass(frozen=True)
class HeadEvidenceDecisionInput:
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
    if current_head and current_head in resolver_heads:
        return False
    return not _is_stage_3_measured(stage_3_evidence, stage_3_head, current_head)


def _attach_local_counts(ci_status: dict[str, Any], outcome: _LocalTestOutcome) -> None:
    m = outcome.measurement
    ci_status["test_counts"] = {"passed": m.passed, "failed": m.failed, "errored": m.errored}
    if m.run_error:
        ci_status["test_run_error"] = m.run_error


def _attach_stage_3_counts(ci_status: dict[str, Any], stage_3: dict[str, Any] | None) -> None:
    if stage_3 and (stage_3.get("test_pass_rate") is not None or stage_3.get("test_run_error")):
        ci_status["test_counts"] = {
            "passed": stage_3.get("tests_passed"),
            "failed": stage_3.get("tests_failed"),
            "errored": stage_3.get("tests_errored"),
        }


def choose_evidence(inp: HeadEvidenceDecisionInput) -> str:
    current_head = inp.current_head
    if current_head and current_head in inp.resolver_heads:
        return TEST_SOURCE_RESOLVER_EVIDENCE

    if _is_ci_head_passing(inp.ci_status, current_head, inp.pr_head_sha):
        return TEST_SOURCE_CI_HEAD

    if _is_stage_3_measured(inp.stage_3_evidence, inp.stage_3_head, current_head):
        return TEST_SOURCE_STAGE_3

    return TEST_SOURCE_REVIEW_LOCAL


def _discard_stale_github_pass(ci_status: dict[str, Any], source: str | None = None) -> None:
    if source is not None and source not in (TEST_SOURCE_STAGE_3, TEST_SOURCE_REVIEW_LOCAL):
        return
    if (
        ci_status.get("source") == "github_cli"
        and ci_status.get("all_passed")
        and not ci_status.get("pending", 0)
        and not ci_status.get("failed", 0)
    ):
        ci_status.update(
            source="local_tests",
            total=1,
            passed=1,
            failed=0,
            pending=0,
            skipped=0,
            all_passed=True,
            checks=[{"name": "local_tests", "bucket": "pass", "state": "SUCCESS"}],
        )


def _attribute_test_source(
    ci_status: dict[str, Any], stage_3: dict[str, Any] | None, review_local: _LocalTestOutcome | None
) -> None:
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
    clean = text.lower()
    if re.search(r"\b(?:not|0)\s+passed\b", clean) or re.search(r"\b[1-9]\d*\s+(?:failed|errors?|errored)\b", clean):
        return False
    return bool(re.search(r"\bpassed\b", clean) or re.search(r"(?<!\d)0 failed\b", clean))


def _disp_field(disp: Any, name: str) -> str:
    return str(getattr(disp, name, None) or (disp.get(name) if isinstance(disp, dict) else "") or "")


def _has_passing_ci_rejection(dispositions: Sequence[Any]) -> bool:
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
