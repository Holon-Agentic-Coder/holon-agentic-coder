"""Bounded, classified retries of ``run_calibrate`` for Stage 5.

Stage 5 used to write a stub "fallback" report whenever ``run_calibrate`` raised and still report success (seen on PR
#249, where checking out the /calibrated branch failed with ``fatal: gitfile does not point to a valid repository``).
Stage 5 now fails closed instead, after retrying only failures that a second attempt can fix:

- transient: ``git``/subprocess timeouts, connection errors, a turn or stall budget breach, and provider API errors
  (``API Error:``, HTTP 5xx, ``overloaded``) surfacing through the exception message; retried after a short backoff;
- quota: a usage-limit or rate-limit message naming a reset within the wait ceiling
  (``HOLON_REVIEW_QUOTA_WAIT_SECONDS``, see :mod:`sandbox_executor.review_failure`); waited out at most once;
- permanent: everything else (a stage budget breach, ``git`` ``fatal:`` messages, ``CalibrationSyncError``,
  ``ValueError`` ...); not retried.

A retry happens only while ``retry_safe()`` holds (the caller checks that the failed attempt left no report change and
no new /calibrated commit), so a retry never re-appends a ledger row or commits twice. Each failed attempt is logged
with its number, classification and redacted cause.
"""

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
# Backoff before retry n (1-based) is entry n - 1; the last entry is reused.
CALIBRATE_RETRY_BACKOFF_SECONDS = (10.0, 30.0)

# Fail-closed errors the stage handles itself; they are re-raised unchanged, never classified or retried.
_NEVER_RETRIED = (LedgerRewriteError, CalibrationFormatError, CalibrationSelfCheckError)
_TRANSIENT_TYPES = (subprocess.TimeoutExpired, TimeoutError, ConnectionError)
# A fired stage budget leaves no time for another attempt; only turn and stall breaches are retried.
_TRANSIENT_BUDGETS = frozenset({"turn", "stall"})
_AUTH_STATUSES = {PreflightStatus.NOT_LOGGED_IN, PreflightStatus.EXPIRED_KEY}
_OVERLOADED_RE = re.compile(
    r"\boverloaded\b|(?:HTTP|status|code|error)[\s:=]*5\d\d\b|\b5\d\d\s+(?:server\s+error|service\s+unavailable)",
    re.IGNORECASE,
)


class RetryKind(enum.StrEnum):
    """How a failed calibration attempt is treated."""

    TRANSIENT = "transient"
    QUOTA = "quota"
    PERMANENT = "permanent"


@dataclass(frozen=True)
class CalibrateFailure:
    """A classified calibration failure; ``cause`` is redacted and bounded, ``wait_seconds`` is set for quota."""

    kind: RetryKind
    cause: str
    wait_seconds: float | None = None


class CalibrationRetriesExhausted(Exception):
    """``run_calibrate`` failed on more than one attempt; ``causes`` are the redacted causes, oldest first."""

    def __init__(self, attempts: int, causes: list[str]) -> None:
        self.attempts = attempts
        self.causes = list(causes)
        last = self.causes[-1] if self.causes else "unknown"
        super().__init__(f"{attempts} attempt(s) failed; last: {last}")


def redacted_cause(exc: BaseException) -> str:
    """``exc``'s message with credentials redacted, bounded to ``MESSAGE_MAX_CHARS``."""
    text = redact_agent_secrets(str(exc)) or type(exc).__name__
    return text[:MESSAGE_MAX_CHARS]


def _kind_by_type(exc: Exception) -> RetryKind | None:
    """The retry kind ``exc``'s type alone decides, or None when its message must be inspected."""
    if isinstance(exc, BudgetBreach):
        return RetryKind.TRANSIENT if exc.budget_name in _TRANSIENT_BUDGETS else RetryKind.PERMANENT
    return RetryKind.TRANSIENT if isinstance(exc, _TRANSIENT_TYPES) else None


def classify_calibrate_error(exc: Exception, now: datetime, ceiling: float) -> CalibrateFailure:
    """Classify a ``run_calibrate`` exception (never one of the fail-closed errors, which callers re-raise first)."""
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
    """The attempts of one wrapped call: the causes so far and whether a quota reset was already waited out."""

    log_fn: Callable[[str], None]
    retry_safe: Callable[[], bool]
    ceiling: float
    causes: list[str] = field(default_factory=list)
    quota_waited: bool = False

    def delay_after(self, failure: CalibrateFailure) -> float | None:
        """Seconds to wait before the next attempt, or None when the call must give up."""
        attempt = len(self.causes)
        if failure.kind is RetryKind.PERMANENT or attempt >= CALIBRATE_MAX_ATTEMPTS:
            return None
        if failure.kind is RetryKind.QUOTA and self.quota_waited:
            return None
        if not self.retry_safe():
            self.log_fn("Calibration attempt left a report change or a new /calibrated commit; not safe to retry.")
            return None
        if failure.kind is RetryKind.QUOTA:
            self.quota_waited = True
            return failure.wait_seconds
        return CALIBRATE_RETRY_BACKOFF_SECONDS[min(attempt, len(CALIBRATE_RETRY_BACKOFF_SECONDS)) - 1]

    def record(self, exc: Exception) -> float:
        """Log the failed attempt and return the wait before the next one; raise when giving up."""
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
) -> Callable[..., Any]:
    """Wrap ``calibrate`` (``run_calibrate``'s keyword signature) in the bounded retry policy of this module.

    The wrapper re-raises ``LedgerRewriteError``, ``CalibrationFormatError`` and ``CalibrationSelfCheckError``
    unchanged, re-raises any other exception unchanged when the first attempt gives up, and raises
    ``CalibrationRetriesExhausted`` (chained to the last exception) when a later attempt gives up.
    """

    def run(**kwargs: Any) -> Any:
        state = _RetryState(log_fn, retry_safe, wait_ceiling(os.environ if env is None else env))
        while True:
            try:
                return calibrate(**kwargs)
            except _NEVER_RETRIED:
                raise
            except Exception as exc:
                time.sleep(state.record(exc))

    return run
