"""Stage 4 CI evidence: fold the Stage 3 local test result into a ``local_tests`` CI probe."""

from __future__ import annotations

from typing import Any


def apply_local_test_evidence(
    ci_status: dict[str, Any],
    tests_failed: bool,
    test_pass_rate: float | None,
    dry_run: bool,
    unmeasured_blocks: bool,
) -> bool:
    """Rewrite a ``local_tests`` probe in place from the Stage 3 evidence; True when tests were not measured.

    Failed local tests always fail the probe. With no recorded pass rate, a dry run marks the probe unmeasured, and
    otherwise, when ``unmeasured_blocks``, the probe becomes a skipped ``NOT_MEASURED`` check that blocks approval.
    """
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
