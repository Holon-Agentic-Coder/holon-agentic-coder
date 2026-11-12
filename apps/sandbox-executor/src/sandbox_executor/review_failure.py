"""Name why a reviewer turn failed, with a redacted, bounded excerpt of what the runner said.

A reviewer that exits non-zero without a usable report used to be logged as ``stop reason: unknown``. This module
classifies the failure as authentication, quota, timeout or other, recognises a quota reset time when the message
gives one, and decides whether that reset is near enough to wait out. Everything here is pure: the clock is passed in.
"""

from __future__ import annotations

import enum
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
_ANTHROPIC_TOKEN_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]*")
_QUOTA_STATUSES = {PreflightStatus.QUOTA_EXHAUSTED}
_AUTH_STATUSES = {PreflightStatus.NOT_LOGGED_IN, PreflightStatus.EXPIRED_KEY}
_RETRY_AFTER_RE = re.compile(r"retry-after\W+(\d{1,6})\b", re.IGNORECASE)
_RELATIVE_RE = re.compile(
    r"(?:in|after)\s+(?:(\d{1,3})\s*(?:h|hours?|hrs?))?\s*(?:(\d{1,3})\s*(?:m|mins?|minutes?))?\s*"
    r"(?:(\d{1,3})\s*(?:s|secs?|seconds?))?\b",
    re.IGNORECASE,
)
_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?")
_CLOCK_RE = re.compile(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?(?:\s*\(([A-Za-z_/]+)\))?", re.IGNORECASE)


class FailureCause(enum.StrEnum):
    """Why a reviewer turn failed."""

    AUTHENTICATION = "authentication"
    QUOTA = "quota"
    TIMEOUT = "timeout"
    OTHER = "other"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ReviewerFailure:
    """A classified reviewer failure; ``excerpt`` is redacted and bounded, ``reset_at`` is timezone-aware."""

    cause: FailureCause
    excerpt: str = ""
    reset_at: datetime | None = None
    wait_seconds: float | None = None


def redacted_excerpt(stderr: str, stdout: str, limit: int = EXCERPT_CHARS) -> str:
    """The tail of stderr plus the last lines of stdout, with credentials redacted, within ``limit`` characters."""
    tail = (stdout or "").splitlines()[-_STDOUT_TAIL_LINES:]
    text = "\n".join([(stderr or "").strip(), *tail]).strip()[-_SCAN_CHARS:]
    text = redact_agent_secrets(_ANTHROPIC_TOKEN_RE.sub("[REDACTED]", text)) or ""
    lines = text.splitlines()[-EXCERPT_LINES:]
    return "\n".join(lines)[-limit:]


def classify_failure(result: TurnResult, now: datetime) -> ReviewerFailure:
    """Classify a failed turn from its breach, stderr and the tail of stdout."""
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
        wait = (reset_at - now).total_seconds() if reset_at else None
        return ReviewerFailure(FailureCause.QUOTA, excerpt, reset_at, wait)
    return ReviewerFailure(FailureCause.OTHER, excerpt)


def _status(text: str) -> PreflightStatus | None:
    """The preflight status whose pattern matches ``text``, authentication before quota."""
    matched = matching_statuses(text)
    for status in (PreflightStatus.NOT_LOGGED_IN, PreflightStatus.EXPIRED_KEY, PreflightStatus.QUOTA_EXHAUSTED):
        if status in matched:
            return status
    return None


def parse_reset_time(text: str, now: datetime) -> datetime | None:
    """A future, aware reset time named in ``text`` (clock time, relative, retry-after or ISO), else None."""
    found = _relative_reset(text, now) or _iso_reset(text) or _clock_reset(text, now)
    return found if found is not None and found > now else None


def _relative_reset(text: str, now: datetime) -> datetime | None:
    match = _RETRY_AFTER_RE.search(text)
    if match:
        return now + timedelta(seconds=int(match.group(1)))
    for candidate in _RELATIVE_RE.finditer(text):
        hours, minutes, seconds = (int(part or 0) for part in candidate.groups())
        if hours or minutes or seconds:
            return now + timedelta(hours=hours, minutes=minutes, seconds=seconds)
    return None


def _iso_reset(text: str) -> datetime | None:
    match = _ISO_RE.search(text)
    if not match:
        return None
    try:
        parsed = datetime.fromisoformat(match.group(0).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


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
        tz = ZoneInfo(zone) if zone else UTC
    except (KeyError, ValueError, OSError):
        tz = UTC
    local = now.astimezone(tz)
    reset = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return (reset if reset > local else reset + timedelta(days=1)).astimezone(UTC)


def describe(failure: ReviewerFailure) -> str:
    """The stop-reason text, such as ``authentication`` or ``quota (resets 2026-11-13T05:00Z)``."""
    if failure.cause is FailureCause.QUOTA and failure.reset_at is not None:
        stamp = failure.reset_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ")
        return f"{failure.cause} (resets {stamp})"
    return str(failure.cause)


def quota_wait_seconds(failure: ReviewerFailure, ceiling: float) -> float | None:
    """Seconds to wait before retrying, or None unless a quota reset time is named within ``ceiling``."""
    if failure.cause is not FailureCause.QUOTA or failure.wait_seconds is None:
        return None
    wait = failure.wait_seconds + _WAIT_MARGIN_SECONDS
    return wait if wait <= ceiling else None


def wait_ceiling(env: Mapping[str, str]) -> float:
    """The wait ceiling in seconds from ``HOLON_REVIEW_QUOTA_WAIT_SECONDS``, else the default."""
    try:
        value = float(env.get(WAIT_CEILING_ENV_VAR, ""))
    except ValueError:
        return DEFAULT_WAIT_CEILING_SECONDS
    return max(0.0, value)


class OnceSleeper:
    """Sleep callable that records whether it has been used, so an ensemble waits for at most one quota reset."""

    def __init__(self, sleep: Callable[[float], None]) -> None:
        self._sleep = sleep
        self.used = False

    def __call__(self, seconds: float) -> None:
        self.used = True
        self._sleep(seconds)
