from __future__ import annotations

from typing import Any


def apply_local_test_evidence(
    ci_status: dict[str, Any],
    tests_failed: bool,
    test_pass_rate: float | None,
    dry_run: bool,
    unmeasured_blocks: bool,
) -> bool:
    if ci_status.get("source") != "local_tests":
        return False
    if tests_failed:
        ci_status.update(all_passed=False, passed=0, failed=1)
        ci_status["checks"] = [{"name": "local_tests", "bucket": "fail", "state": "FAILURE"}]
    elif test_pass_rate is None and dry_run:
        ci_status["measured"] = False
    elif test_pass_rate is None and unmeasured_blocks:
        ci_status.update(all_passed=False, passed=0, failed=0, skipped=1, measured=False)
        ci_status["checks"] = [{"name": "local_tests", "bucket": "skipping", "state": "NOT_MEASURED"}]
        return True
    return False


def _apply_guarded_test_evidence(
    test_gate: Any,
    ci_status: dict[str, Any],
    dry_run: bool,
    blocked: bool,
    unmeasured_blocks: bool,
) -> tuple[bool, bool]:
    if not dry_run and blocked:
        return False, False
    gate_rate, tests_failed = test_gate.evidence(ci_status, dry_run=dry_run)
    not_measured = apply_local_test_evidence(ci_status, tests_failed, gate_rate, dry_run, unmeasured_blocks)
    return tests_failed, not_measured
