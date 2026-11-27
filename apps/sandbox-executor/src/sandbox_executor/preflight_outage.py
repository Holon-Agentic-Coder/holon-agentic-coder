import logging
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from sandbox_executor.preflight import PreflightResult, PreflightStatus, matching_statuses
from sandbox_executor.preflight_model_error import is_model_error

logger = logging.getLogger(__name__)

OUTAGE_INITIAL_WAIT_SECONDS = 30.0
OUTAGE_MAX_WAIT_SECONDS = 300.0
OUTAGE_GIVE_UP_SECONDS = 3600.0

_NO_OUTPUT_REASON = "probe finished with no output"
_NO_OUTPUT_TIMEOUT_HINT = "did not finish within"
_NEVER_WAIT_HINT_PREFIXES = ("'docker' CLI command not found", "Unsupported agent runner")

_CONNECTIVITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "DNS resolution failure",
        re.compile(
            r"ENOTFOUND|EAI_AGAIN|could\s+not\s+resolve\s+host|temporary\s+failure\s+in\s+name\s+resolution"
            r"|getaddrinfo\b[^\n]{0,80}\bfailed|name\s+or\s+service\s+not\s+known|no\s+such\s+host",
            re.IGNORECASE,
        ),
    ),
    ("connection refused", re.compile(r"ECONNREFUSED|connection\s+refused", re.IGNORECASE)),
    ("connection reset", re.compile(r"ECONNRESET|connection\s+reset|socket\s+hang\s+up", re.IGNORECASE)),
    (
        "network unreachable",
        re.compile(r"ENETUNREACH|EHOSTUNREACH|network\s+is\s+unreachable|no\s+route\s+to\s+host", re.IGNORECASE),
    ),
    ("connection timed out", re.compile(r"ETIMEDOUT|connection\s+timed\s+out|connect\s+timeout", re.IGNORECASE)),
    (
        "Docker daemon not reachable",
        re.compile(
            r"cannot\s+connect\s+to\s+the\s+docker\s+daemon|is\s+the\s+docker\s+daemon\s+running|error\s+during\s+connect",
            re.IGNORECASE,
        ),
    ),
)


@dataclass(frozen=True)
class OutageOutcome:
    result: PreflightResult
    waited_seconds: float
    outage_seconds: float
    gave_up: bool
    attempts: int


def _never_waits(result: PreflightResult) -> bool:
    if matching_statuses(f"{result.output_excerpt}\n{result.hint}"):
        return True
    if is_model_error(result.output_excerpt):
        return True
    return result.hint.startswith(_NEVER_WAIT_HINT_PREFIXES)


def _matching_connectivity_reason(text: str) -> str | None:
    for reason, pattern in _CONNECTIVITY_PATTERNS:
        if pattern.search(text):
            return reason
    return None


def _finished_with_no_output(result: PreflightResult) -> bool:
    if result.output_excerpt.strip():
        return False
    return result.exit_code is not None or _NO_OUTPUT_TIMEOUT_HINT in result.hint


def connectivity_reason(result: PreflightResult) -> str | None:
    if result.status is not PreflightStatus.UNKNOWN or _never_waits(result):
        return None
    reason = _matching_connectivity_reason(f"{result.output_excerpt}\n{result.hint}")
    if reason is None and _finished_with_no_output(result):
        return _NO_OUTPUT_REASON
    return reason


def backoff_schedule() -> Iterator[float]:
    waited = 0.0
    delay = OUTAGE_INITIAL_WAIT_SECONDS
    while waited < OUTAGE_GIVE_UP_SECONDS:
        step = min(delay, OUTAGE_MAX_WAIT_SECONDS, OUTAGE_GIVE_UP_SECONDS - waited)
        yield step
        waited += step
        delay *= 2


def wait_out_outage(
    probe: Callable[[], PreflightResult],
    first: PreflightResult,
    started_at: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> OutageOutcome:
    result, waited, attempts = first, 0.0, 1
    reason = connectivity_reason(first)
    if reason is None:
        return OutageOutcome(result, waited, 0.0, gave_up=False, attempts=attempts)
    for delay in backoff_schedule():
        attempts += 1
        logger.warning(
            "Pre-flight probe hit a connectivity outage (%s); waiting %gs before attempt %d (%gs waited of %gs).",
            reason,
            delay,
            attempts,
            waited,
            OUTAGE_GIVE_UP_SECONDS,
        )
        sleep(delay)
        waited += delay
        result = probe()
        reason = connectivity_reason(result)
        if reason is None:
            return OutageOutcome(result, waited, clock() - started_at, gave_up=False, attempts=attempts)
    return OutageOutcome(result, waited, clock() - started_at, gave_up=True, attempts=attempts)


def format_outage_duration(seconds: float) -> str:
    hours, remainder = divmod(round(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    give_up_minutes = round(OUTAGE_GIVE_UP_SECONDS / 60)
    return (
        f"Connectivity outage lasted {hours}h {minutes}m {secs}s; gave up after {give_up_minutes} minutes of waiting."
    )
