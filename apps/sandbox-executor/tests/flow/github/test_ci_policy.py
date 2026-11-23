"""Literal-only tests for the I/O-free CI verdict in ``flow/github/ci_policy.py``."""

from __future__ import annotations

import pytest
from sandbox_executor.flow.github.ci_policy import (
    _ci_green,
    _extract_failed_ci_check_names,
    local_tests_ci_status,
    summarise_ci_checks,
)


def counts(checks: list[dict[str, str]]) -> tuple[int, int, int, int, int, bool]:
    s = summarise_ci_checks(checks)
    return s["total"], s["passed"], s["failed"], s["pending"], s["skipped"], s["all_passed"]


@pytest.mark.parametrize(
    ("check", "expected"),
    [
        ({"bucket": "pass"}, (1, 1, 0, 0, 0, True)),
        ({"bucket": "PASS"}, (1, 1, 0, 0, 0, True)),
        ({"bucket": "fail"}, (1, 0, 1, 0, 0, False)),
        ({"bucket": "pending"}, (1, 0, 0, 1, 0, False)),
        ({"bucket": "skipping"}, (1, 0, 0, 0, 1, True)),
        ({"bucket": "skip"}, (1, 0, 0, 0, 1, True)),
        ({"state": "SUCCESS"}, (1, 1, 0, 0, 0, True)),
        ({"state": "completed"}, (1, 1, 0, 0, 0, True)),
        ({"state": "FAILURE"}, (1, 0, 1, 0, 0, False)),
        ({"state": "TIMED_OUT"}, (1, 0, 1, 0, 0, False)),
        ({"state": "ACTION_REQUIRED"}, (1, 0, 1, 0, 0, False)),
        ({"state": "IN_PROGRESS"}, (1, 0, 0, 1, 0, False)),
        ({"state": "QUEUED"}, (1, 0, 0, 1, 0, False)),
        ({"state": "PENDING"}, (1, 0, 0, 1, 0, False)),
        ({"state": "SKIPPED"}, (1, 0, 0, 0, 1, True)),
        ({"state": "SUCCESS", "bucket": "fail"}, (1, 0, 1, 0, 0, False)),
        ({"state": "NEUTRAL", "bucket": "cancel"}, (1, 0, 0, 0, 0, True)),
    ],
)
def test_summarise_ci_checks_classifies_each_bucket_and_state(check, expected):
    assert counts([check]) == expected


def test_summarise_ci_checks_of_an_empty_list_is_not_green():
    assert summarise_ci_checks([]) == {
        "source": "github_cli",
        "total": 0,
        "passed": 0,
        "failed": 0,
        "pending": 0,
        "skipped": 0,
        "all_passed": False,
        "checks": [],
    }


def test_summarise_ci_checks_keeps_the_raw_checks():
    checks = [{"name": "unit", "bucket": "pass"}, {"name": "lint", "bucket": "pending"}]
    summary = summarise_ci_checks(checks)
    assert summary["checks"] is checks
    assert (summary["source"], summary["all_passed"]) == ("github_cli", False)


def test_local_tests_ci_status_is_a_fresh_passing_dict_on_every_call():
    first = local_tests_ci_status()
    assert first == {
        "source": "local_tests",
        "total": 1,
        "passed": 1,
        "failed": 0,
        "pending": 0,
        "skipped": 0,
        "all_passed": True,
        "checks": [{"name": "local_tests", "bucket": "pass", "state": "SUCCESS"}],
    }
    first["checks"].append({"name": "leak"})
    first["all_passed"] = False
    assert local_tests_ci_status()["checks"] == [{"name": "local_tests", "bucket": "pass", "state": "SUCCESS"}]
    assert local_tests_ci_status()["all_passed"] is True


@pytest.mark.parametrize(
    ("ci_status", "tests_failed", "expected"),
    [
        ({"all_passed": True}, False, True),
        ({"all_passed": True}, True, False),
        ({"all_passed": False}, False, False),
        ({"all_passed": "yes"}, False, False),
        ({}, False, None),
        ({"measured": False, "all_passed": True}, False, None),
    ],
)
def test_ci_green(ci_status, tests_failed, expected):
    assert _ci_green(ci_status, tests_failed) is expected


def test_extract_failed_ci_check_names_dedups_and_skips_non_dicts():
    ci_status = {
        "checks": [
            {"name": "unit", "bucket": "fail"},
            "not-a-dict",
            {"name": " unit ", "state": "FAILURE"},
            {"name": "lint", "state": "timed_out"},
            {"name": "", "bucket": "fail"},
            {"name": "docs", "bucket": "pass"},
            {"name": "e2e", "state": "ACTION_REQUIRED"},
        ]
    }
    assert _extract_failed_ci_check_names(ci_status) == ["unit", "lint", "e2e"]


def test_extract_failed_ci_check_names_appends_local_tests_once():
    assert _extract_failed_ci_check_names(None, tests_failed=True) == ["local_tests"]
    ci_status = {"checks": [{"name": "local_tests", "bucket": "fail"}]}
    assert _extract_failed_ci_check_names(ci_status, tests_failed=True) == ["local_tests"]
    assert _extract_failed_ci_check_names({"checks": "x"}) == []
