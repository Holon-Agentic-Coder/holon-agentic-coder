"""Hermetic tests for reviewer failure classification, redaction and the single quota wait."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.budgets import BudgetBreach
from sandbox_executor.review_ensemble import ReviewerStatus, run_ensemble
from sandbox_executor.review_failure import (
    FailureCause,
    classify_failure,
    describe,
    parse_reset_time,
    quota_wait_seconds,
    redacted_excerpt,
    wait_ceiling,
)
from sandbox_executor.turns import TurnResult

from tests.test_review_ensemble import CLEAN, FakeTurnRunner, make_request, report, reviewer, seams

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
OAT = "sk-ant-" + "oat01-" + "Ab9_" * 6
SHORT = "sk-ant-" + "abc"
GHP = "ghp_" + "Q7" * 18


def turn(stderr: str = "", stdout: str = "", breach: BudgetBreach | None = None) -> TurnResult:
    return TurnResult(returncode=1, stdout=stdout, stderr=stderr, breach=breach)


@pytest.mark.parametrize(
    ("stderr", "cause"),
    [
        ("HTTP 401 invalid x-api-key", FailureCause.AUTHENTICATION),
        ("Not logged in. Please run /login", FailureCause.AUTHENTICATION),
        ("API Error: 429 rate limit reached", FailureCause.QUOTA),
        ("Claude usage limit reached. Your limit resets at 5pm (UTC)", FailureCause.QUOTA),
        ("segfault in agent", FailureCause.OTHER),
        ("", FailureCause.UNKNOWN),
    ],
)
def test_each_class_is_named(stderr: str, cause: FailureCause) -> None:
    assert classify_failure(turn(stderr), NOW).cause is cause


def test_breach_is_a_timeout_and_unknown_needs_empty_output() -> None:
    breach = BudgetBreach("review", "turn", 1.0, 2.0)
    assert classify_failure(turn(breach=breach), NOW).cause is FailureCause.TIMEOUT
    assert classify_failure(turn("boom"), NOW).cause is not FailureCause.UNKNOWN
    assert classify_failure(turn(stdout="boom"), NOW).cause is FailureCause.OTHER


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("resets at 5pm", 7 * 60),
        ("reset at 17:00 (UTC)", 7 * 60),
        ("resets 9am (UTC)", 23 * 60),
        ("try again in 2h 5m", 125),
        ("retry-after: 90", 1.5),
        ("available at 2026-11-12T12:30:00Z", 150),
        ("2026-11-12T01:00:00Z usage limit reached, resets at 5pm", 7 * 60),
    ],
)
def test_reset_time_forms(text: str, minutes: float) -> None:
    assert parse_reset_time(text, NOW) == NOW + timedelta(minutes=minutes)


def test_reset_time_absent_or_unknown_zone() -> None:
    assert parse_reset_time("no time here", NOW) is None
    assert parse_reset_time("resets at 5pm (Nowhere/Land)", NOW) == NOW + timedelta(hours=7)


def test_describe_names_reset() -> None:
    failure = classify_failure(turn("usage limit reached, resets at 5pm"), NOW)
    assert describe(failure) == "quota (resets 2026-11-12T17:00Z)"
    assert describe(classify_failure(turn("401 unauthorized"), NOW)) == "authentication"


def test_wait_is_bounded_by_ceiling() -> None:
    near = classify_failure(turn("rate limit, try again in 2m"), NOW)
    far = classify_failure(turn("rate limit, try again in 5h"), NOW)
    none = classify_failure(turn("rate limit"), NOW)
    assert quota_wait_seconds(near, 900) == 125.0
    assert quota_wait_seconds(far, 900) is None
    assert quota_wait_seconds(none, 900) is None
    assert wait_ceiling({"HOLON_REVIEW_QUOTA_WAIT_SECONDS": "60"}) == 60.0
    assert wait_ceiling({"HOLON_REVIEW_QUOTA_WAIT_SECONDS": "x"}) == 900.0


def test_excerpt_is_bounded_and_redacted() -> None:
    text = "\n".join(f"line {n} {OAT} {SHORT} {GHP}" for n in range(50))
    excerpt = redacted_excerpt(text, text)
    assert len(excerpt) <= 600 and len(excerpt.splitlines()) <= 8
    for secret in (OAT, SHORT, GHP, "sk-ant-"):
        assert secret not in excerpt


def test_authentication_failure_logs_cause_and_leaks_nothing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    stderr = f"401 invalid x-api-key {OAT} {SHORT} {GHP}"
    result, _ = reviewer(tmp_path, [("exit", 1, stderr)])
    assert result.status is ReviewerStatus.FAILED
    assert "authentication" in (result.reason or "")
    assert "stop reason: authentication" in caplog.text
    for secret in (OAT, SHORT, GHP, "sk-ant-"):
        assert secret not in caplog.text
        assert secret not in (result.failure_reason or "")


def test_empty_output_logs_unknown(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    reviewer(tmp_path, [("exit", 1)])
    assert "stop reason: unknown" in caplog.text


def _ensemble(
    tmp_path: Path, actions: list[tuple[Any, ...]], size: int = 1
) -> tuple[list, list[float], FakeTurnRunner]:
    sleeps: list[float] = []
    runner = FakeTurnRunner(tmp_path, actions)
    results = run_ensemble(
        make_request(tmp_path), agent="claude-agent", model="m", size=size, sleep=sleeps.append, **seams(runner)
    )
    return results, sleeps, runner


def test_quota_with_near_reset_waits_once_and_retries(tmp_path: Path) -> None:
    quota = ("exit", 1, "429 rate limit reached, try again in 2m")
    results, sleeps, runner = _ensemble(tmp_path, [quota, ("write", report("ok", CLEAN))])
    assert results[0].status is ReviewerStatus.COMPLETED
    assert len(sleeps) == 1 and 120 < sleeps[0] <= 131
    assert len(runner.calls) == 2


def test_only_one_retry_per_ensemble(tmp_path: Path) -> None:
    quota = ("exit", 1, "429 rate limit reached, try again in 2m")
    results, sleeps, _ = _ensemble(tmp_path, [quota, quota, quota, quota], size=2)
    assert len(sleeps) == 1
    assert all(r.status is ReviewerStatus.FAILED for r in results)
    assert "quota (resets" in (results[0].reason or "")


@pytest.mark.parametrize("stderr", ["429 rate limit reached", "429 rate limit, try again in 5h"])
def test_unwaitable_quota_fails_closed_without_sleeping(tmp_path: Path, stderr: str) -> None:
    results, sleeps, runner = _ensemble(tmp_path, [("exit", 1, stderr)])
    assert sleeps == [] and len(runner.calls) == 1
    assert results[0].status is ReviewerStatus.FAILED
    assert "quota" in (results[0].reason or "")
