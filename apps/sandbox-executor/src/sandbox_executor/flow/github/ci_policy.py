from __future__ import annotations

from typing import Any

# why: this verdict stays I/O-free (no subprocess, no environment); ci.py owns the gh call
_FAILED_STATES = ("FAILURE", "TIMED_OUT", "ACTION_REQUIRED")
_PENDING_STATES = ("IN_PROGRESS", "QUEUED", "PENDING")


def _bucket(check: dict[str, Any]) -> str:
    return str(check.get("bucket", "")).lower()


def _state(check: dict[str, Any]) -> str:
    return str(check.get("state", "")).upper()


def _is_passed(check: dict[str, Any]) -> bool:
    return _bucket(check) == "pass" or (_state(check) in ("SUCCESS", "COMPLETED") and _bucket(check) != "fail")


def _is_failed(check: dict[str, Any]) -> bool:
    return _bucket(check) == "fail" or _state(check) in _FAILED_STATES


def _is_pending(check: dict[str, Any]) -> bool:
    return _bucket(check) == "pending" or _state(check) in _PENDING_STATES


def _is_skipped(check: dict[str, Any]) -> bool:
    return _bucket(check) in ("skipping", "skip") or _state(check) == "SKIPPED"


def summarise_ci_checks(raw_checks: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(raw_checks)
    passed = sum(1 for c in raw_checks if _is_passed(c))
    failed = sum(1 for c in raw_checks if _is_failed(c))
    pending = sum(1 for c in raw_checks if _is_pending(c))
    skipped = sum(1 for c in raw_checks if _is_skipped(c))
    return {
        "source": "github_cli",
        "total": total,
        "passed": passed,
        "failed": failed,
        "pending": pending,
        "skipped": skipped,
        "all_passed": (total > 0) and (failed == 0) and (pending == 0),
        "checks": raw_checks,
    }


def local_tests_ci_status() -> dict[str, Any]:
    return {
        "source": "local_tests",
        "total": 1,
        "passed": 1,
        "failed": 0,
        "pending": 0,
        "skipped": 0,
        "all_passed": True,
        "checks": [{"name": "local_tests", "bucket": "pass", "state": "SUCCESS"}],
    }


def _ci_green(ci_status: dict[str, Any], tests_failed: bool) -> bool | None:
    if ci_status.get("measured") is False or "all_passed" not in ci_status:
        # why: an unmeasured CI status is unknown, and unknown is never green
        return None
    return ci_status.get("all_passed") is True and not tests_failed


def _extract_failed_ci_check_names(ci_status: dict[str, Any] | None, tests_failed: bool = False) -> list[str]:
    failing: list[str] = []
    if ci_status and isinstance(ci_status.get("checks"), list):
        for check in ci_status["checks"]:
            if not isinstance(check, dict):
                continue
            bucket = str(check.get("bucket") or "").lower()
            state = str(check.get("state") or "").upper()
            if bucket == "fail" or state in _FAILED_STATES:
                name = str(check.get("name") or "").strip()
                if name and name not in failing:
                    failing.append(name)
    if tests_failed and "local_tests" not in failing:
        failing.append("local_tests")
    return failing
