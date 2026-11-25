from __future__ import annotations

import enum
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sandbox_executor.preflight import PreflightStatus, matching_statuses
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.turns import TurnResult

EXCERPT_CHARS = 600
EXCERPT_LINES = 8
_SCAN_CHARS = 4000
_STDOUT_TAIL_LINES = 20
DEFAULT_WAIT_CEILING_SECONDS = 900.0
WAIT_CEILING_ENV_VAR = "HOLON_REVIEW_QUOTA_WAIT_SECONDS"
_WAIT_MARGIN_SECONDS = 5.0
_REDACTED_PLACEHOLDER = "[REDACTED]"
_UTC_RESET_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%MZ"
_ANTHROPIC_TOKEN_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]*")
_QUOTA_STATUSES = {PreflightStatus.QUOTA_EXHAUSTED}
_AUTH_STATUSES = {PreflightStatus.NOT_LOGGED_IN, PreflightStatus.EXPIRED_KEY}
_STATUS_PRIORITY = (
    PreflightStatus.NOT_LOGGED_IN,
    PreflightStatus.EXPIRED_KEY,
    PreflightStatus.QUOTA_EXHAUSTED,
)
_RETRY_AFTER_RE = re.compile(r"retry-after\W+(\d{1,6})\b", re.IGNORECASE)
_RELATIVE_RE = re.compile(
    r"\b(?:in|after)\s+(?:(\d{1,3})\s*(?:h|hours?|hrs?))?\s*(?:(\d{1,3})\s*(?:m|mins?|minutes?))?\s*"
    r"(?:(\d{1,3})\s*(?:s|secs?|seconds?))?\b",
    re.IGNORECASE,
)
_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?")
_CLOCK_RE = re.compile(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?(?:\s*\(([A-Za-z_/]+)\))?", re.IGNORECASE)


class FailureCause(enum.StrEnum):
    AUTHENTICATION = "authentication"
    QUOTA = "quota"
    TIMEOUT = "timeout"
    OTHER = "other"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ReviewerFailure:
    cause: FailureCause
    excerpt: str = ""
    reset_at: datetime | None = None
    wait_seconds: float | None = None


def redacted_excerpt(stderr: str, stdout: str, limit: int = EXCERPT_CHARS) -> str:
    stdout_tail = (stdout or "").splitlines()[-_STDOUT_TAIL_LINES:]
    combined_output = "\n".join([(stderr or "").strip(), *stdout_tail]).strip()[-_SCAN_CHARS:]
    redacted_output = redact_agent_secrets(_ANTHROPIC_TOKEN_RE.sub(_REDACTED_PLACEHOLDER, combined_output)) or ""
    excerpt_lines = redacted_output.splitlines()[-EXCERPT_LINES:]
    return "\n".join(excerpt_lines)[-limit:]


def classify_failure(result: TurnResult, now: datetime) -> ReviewerFailure:
    excerpt = redacted_excerpt(result.stderr, result.stdout)
    if result.breach is not None:
        return ReviewerFailure(FailureCause.TIMEOUT, excerpt)
    if not excerpt:
        return ReviewerFailure(FailureCause.UNKNOWN)
    status = _status(excerpt)
    if status in _AUTH_STATUSES:
        return ReviewerFailure(FailureCause.AUTHENTICATION, excerpt)
    if status in _QUOTA_STATUSES:
        reset_at = parse_reset_time(excerpt, now)
        wait_seconds = (reset_at - now).total_seconds() if reset_at else None
        return ReviewerFailure(FailureCause.QUOTA, excerpt, reset_at, wait_seconds)
    return ReviewerFailure(FailureCause.OTHER, excerpt)


def _status(text: str) -> PreflightStatus | None:
    matched_statuses = matching_statuses(text)
    for status in _STATUS_PRIORITY:
        if status in matched_statuses:
            return status
    return None


def parse_reset_time(text: str, now: datetime) -> datetime | None:
    for candidate_reset in (_relative_reset(text, now), _iso_reset(text, now), _clock_reset(text, now)):
        if candidate_reset is not None and candidate_reset > now:
            return candidate_reset
    return None


def _relative_reset(text: str, now: datetime) -> datetime | None:
    retry_after_match = _RETRY_AFTER_RE.search(text)
    if retry_after_match:
        return now + timedelta(seconds=int(retry_after_match.group(1)))
    for candidate in _RELATIVE_RE.finditer(text):
        hours, minutes, seconds = (int(part or 0) for part in candidate.groups())
        if hours or minutes or seconds:
            return now + timedelta(hours=hours, minutes=minutes, seconds=seconds)
    return None


def _iso_reset(text: str, now: datetime) -> datetime | None:
    for match in _ISO_RE.finditer(text):
        try:
            parsed = datetime.fromisoformat(match.group(0).replace("Z", "+00:00"))
        except ValueError:
            continue
        parsed = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        if parsed > now:
            return parsed
    return None


def _clock_reset(text: str, now: datetime) -> datetime | None:
    match = _CLOCK_RE.search(text)
    if not match:
        return None
    hour, minute, meridiem, zone = int(match.group(1)), int(match.group(2) or 0), match.group(3), match.group(4)
    if meridiem:
        hour = hour % 12 + (12 if meridiem.lower() == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    try:
        target_tz = ZoneInfo(zone) if zone else UTC
    except (KeyError, ValueError, OSError):
        target_tz = UTC
    local_now = now.astimezone(target_tz)
    same_day_reset = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    next_reset = same_day_reset if same_day_reset > local_now else same_day_reset + timedelta(days=1)
    return next_reset.astimezone(UTC)


def describe(failure: ReviewerFailure) -> str:
    if failure.cause is FailureCause.QUOTA and failure.reset_at is not None:
        reset_timestamp = failure.reset_at.astimezone(UTC).strftime(_UTC_RESET_TIMESTAMP_FORMAT)
        return f"{failure.cause} (resets {reset_timestamp})"
    return str(failure.cause)


def quota_wait_seconds(failure: ReviewerFailure, ceiling: float) -> float | None:
    if failure.cause is not FailureCause.QUOTA or failure.wait_seconds is None:
        return None
    wait_with_margin = failure.wait_seconds + _WAIT_MARGIN_SECONDS
    return wait_with_margin if wait_with_margin <= ceiling else None


def wait_ceiling(env: Mapping[str, str]) -> float:
    try:
        raw_ceiling = float(env.get(WAIT_CEILING_ENV_VAR, ""))
    except ValueError:
        return DEFAULT_WAIT_CEILING_SECONDS
    return max(0.0, raw_ceiling)


def missing_report_reason(turn: TurnResult) -> str:
    failure = classify_failure(turn, datetime.now(UTC))
    if turn.returncode != 0:
        detail = f"{describe(failure)}: {failure.excerpt}" if failure.excerpt else describe(failure)
        return f"the reviewer exited with code {turn.returncode} ({detail})"
    if failure.excerpt:
        return f"the reviewer wrote no report (output: {failure.excerpt})"
    return "the reviewer wrote no report"


def log_failed_turn(logger: logging.Logger, turn: TurnResult) -> None:
    failure = classify_failure(turn, datetime.now(UTC))
    logger.warning("Reviewer agent command failed with code %d (stop reason: %s).", turn.returncode, describe(failure))
    if failure.excerpt:
        logger.warning("Runner output (redacted): %s", failure.excerpt)


def retry_wait(turn: TurnResult, env: Mapping[str, str]) -> float | None:
    if turn.returncode == 0:
        return None
    return quota_wait_seconds(classify_failure(turn, datetime.now(UTC)), wait_ceiling(env))


class OnceSleeper:
    def __init__(self, sleep: Callable[[float], None]) -> None:
        self._sleep = sleep
        self.used = False

    def try_wait(self, seconds: float) -> bool:
        if self.used:
            return False
        self.used = True
        self._sleep(seconds)
        return True
