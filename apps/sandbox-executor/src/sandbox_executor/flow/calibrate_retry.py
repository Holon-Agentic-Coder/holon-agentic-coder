from __future__ import annotations

import enum
import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.agent_api_error import MESSAGE_MAX_CHARS, detect_api_error
from sandbox_executor.budgets import BudgetBreach
from sandbox_executor.calibration import CalibrationFormatError, CalibrationSelfCheckError
from sandbox_executor.ledger import LedgerRewriteError
from sandbox_executor.preflight import PreflightStatus, matching_statuses
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.review_failure import (
    FailureCause,
    ReviewerFailure,
    parse_reset_time,
    quota_wait_seconds,
    wait_ceiling,
)

CALIBRATE_MAX_ATTEMPTS = 3
# why: retry n (1-based) waits entry n - 1 and reuses the last entry once attempts outnumber the entries
CALIBRATE_RETRY_BACKOFF_SECONDS = (10.0, 30.0)

# why: the stage fails closed on these errors itself, so they are re-raised unchanged, never classified or retried
_NEVER_RETRIED = (LedgerRewriteError, CalibrationFormatError, CalibrationSelfCheckError)
_TRANSIENT_TYPES = (subprocess.TimeoutExpired, TimeoutError, ConnectionError)
# why: a fired stage budget leaves no time for another attempt; only turn and stall breaches are retried
_TRANSIENT_BUDGETS = frozenset({"turn", "stall"})
_AUTH_STATUSES = {PreflightStatus.NOT_LOGGED_IN, PreflightStatus.EXPIRED_KEY}
_OVERLOADED_RE = re.compile(
    r"\boverloaded\b|(?:HTTP|status|code|error)[\s:=]*5\d\d\b|\b5\d\d\s+(?:server\s+error|service\s+unavailable)",
    re.IGNORECASE,
)


class RetryKind(enum.StrEnum):
    TRANSIENT = "transient"
    QUOTA = "quota"
    PERMANENT = "permanent"


@dataclass(frozen=True)
class CalibrateFailure:
    kind: RetryKind
    cause: str
    wait_seconds: float | None = None


# why: Stage 5 fails closed rather than writing a stub fallback report that reports success (incident PR #249)
class CalibrationRetriesExhausted(Exception):
    def __init__(self, attempts: int, causes: list[str]) -> None:
        self.attempts = attempts
        self.causes = list(causes)
        last = self.causes[-1] if self.causes else "unknown"
        super().__init__(f"{attempts} attempt(s) failed; last: {last}")


def redacted_cause(exc: BaseException) -> str:
    text = redact_agent_secrets(str(exc)) or type(exc).__name__
    return text[:MESSAGE_MAX_CHARS]


def _kind_by_type(exc: Exception) -> RetryKind | None:
    if isinstance(exc, BudgetBreach):
        return RetryKind.TRANSIENT if exc.budget_name in _TRANSIENT_BUDGETS else RetryKind.PERMANENT
    return RetryKind.TRANSIENT if isinstance(exc, _TRANSIENT_TYPES) else None


def classify_calibrate_error(exc: Exception, now: datetime, ceiling: float) -> CalibrateFailure:
    cause = redacted_cause(exc)
    kind = _kind_by_type(exc)
    if kind is not None:
        return CalibrateFailure(kind, cause)
    text = str(exc)
    statuses = matching_statuses(text)
    if statuses & _AUTH_STATUSES:
        return CalibrateFailure(RetryKind.PERMANENT, cause)
    if PreflightStatus.QUOTA_EXHAUSTED in statuses:
        reset_at = parse_reset_time(text, now)
        wait = (reset_at - now).total_seconds() if reset_at else None
        seconds = quota_wait_seconds(ReviewerFailure(FailureCause.QUOTA, cause, reset_at, wait), ceiling)
        if seconds is None:
            return CalibrateFailure(RetryKind.PERMANENT, cause)
        return CalibrateFailure(RetryKind.QUOTA, cause, seconds)
    if detect_api_error(text) or _OVERLOADED_RE.search(text):
        return CalibrateFailure(RetryKind.TRANSIENT, cause)
    return CalibrateFailure(RetryKind.PERMANENT, cause)


@dataclass
class _RetryState:
    log_fn: Callable[[str], None]
    retry_safe: Callable[[], bool]
    ceiling: float
    causes: list[str] = field(default_factory=list)
    quota_waited: bool = False

    def delay_after(self, failure: CalibrateFailure) -> float | None:
        attempt = len(self.causes)
        if failure.kind is RetryKind.PERMANENT or attempt >= CALIBRATE_MAX_ATTEMPTS:
            return None
        if failure.kind is RetryKind.QUOTA and self.quota_waited:
            return None
        # why: a retry after a report change or a new /calibrated commit would re-append a ledger row or commit twice
        if not self.retry_safe():
            self.log_fn("Calibration attempt left a report change or a new /calibrated commit; not safe to retry.")
            return None
        if failure.kind is RetryKind.QUOTA:
            self.quota_waited = True
            return failure.wait_seconds
        return CALIBRATE_RETRY_BACKOFF_SECONDS[min(attempt, len(CALIBRATE_RETRY_BACKOFF_SECONDS)) - 1]

    def record(self, exc: Exception) -> float:
        failure = classify_calibrate_error(exc, datetime.now(UTC), self.ceiling)
        self.causes.append(failure.cause)
        attempt = len(self.causes)
        delay = self.delay_after(failure)
        prefix = f"Calibration attempt {attempt}/{CALIBRATE_MAX_ATTEMPTS} failed ({failure.kind}): {failure.cause}"
        if delay is not None:
            self.log_fn(f"{prefix}; retrying in {delay:.0f}s.")
            return delay
        self.log_fn(f"{prefix}; not retrying.")
        if attempt == 1:
            raise exc
        raise CalibrationRetriesExhausted(attempt, self.causes) from exc


def call_with_retries(
    calibrate: Callable[..., Any],
    *,
    log_fn: Callable[[str], None],
    retry_safe: Callable[[], bool],
    env: Mapping[str, str] | None = None,
    before_retry: Callable[[int], None] | None = None,
) -> Callable[..., Any]:

    def run(**kwargs: Any) -> Any:
        state = _RetryState(log_fn, retry_safe, wait_ceiling(os.environ if env is None else env))
        while True:
            try:
                return calibrate(**kwargs)
            except _NEVER_RETRIED:
                raise
            except Exception as exc:
                time.sleep(state.record(exc))
                if before_retry is not None:
                    before_retry(len(state.causes))

    return run
