from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sandbox_executor.agent_defaults import AGENT_IMAGE_MAPPING, normalise_agent_id

if TYPE_CHECKING:
    from sandbox_executor.preflight import PreflightResult

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_SECONDS = 300.0
MIN_COOLDOWN_SECONDS = 15.0
MAX_COOLDOWN_SECONDS = 3600.0
COOLDOWN_HINT_PREFIX = "Provider cooldown active"
_COOLDOWN_DIR_PARTS = (".holon", "preflight-cooldown")
_EXCERPT_CHARS = 800
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_RETRY_AFTER_RE = re.compile(
    r"retry[-_\s]?after\s*[:=]?\s*(\d+(?:\.\d+)?)\s*"
    r"(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours)?\b",
    re.IGNORECASE,
)
_RELATIVE_DURATION_RE = re.compile(
    r"(?:retry|resets?|try\s+again|available|wait|cooldown|limit\s+resets?)[^\n]{0,40}?\bin\s+"
    r"(\d+(?:\.\d+)?)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours)\b",
    re.IGNORECASE,
)
_ISO_TIMESTAMP_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))\b",
    re.IGNORECASE,
)
_UNIT_MULTIPLIERS: dict[str, float] = {
    "s": 1.0,
    "sec": 1.0,
    "secs": 1.0,
    "second": 1.0,
    "seconds": 1.0,
    "m": 60.0,
    "min": 60.0,
    "mins": 60.0,
    "minute": 60.0,
    "minutes": 60.0,
    "h": 3600.0,
    "hr": 3600.0,
    "hrs": 3600.0,
    "hour": 3600.0,
    "hours": 3600.0,
}


@dataclass(frozen=True)
class CooldownMarker:
    provider: str
    model: str
    recorded_at: float
    expires_at: float
    reason: str
    output_excerpt: str


def cooldown_dir() -> str:
    # why: resolve HOME per call so tests isolating HOME never touch the host user's ~/.holon.
    home = os.environ.get("HOME") or os.path.expanduser("~")
    return os.path.join(home, *_COOLDOWN_DIR_PARTS)


def marker_path(agent: str) -> str:
    provider = normalise_agent_id(agent)
    safe_name = _SAFE_FILENAME_RE.sub("_", provider).strip("._") or "unknown"
    return os.path.join(cooldown_dir(), f"{safe_name}.json")


def _clamp_ttl(seconds: float) -> float:
    if not math.isfinite(seconds):
        return DEFAULT_COOLDOWN_SECONDS
    return max(MIN_COOLDOWN_SECONDS, min(MAX_COOLDOWN_SECONDS, float(seconds)))


def _duration_from_match(amount_text: str, unit_text: str | None) -> float | None:
    try:
        amount = float(amount_text)
    except ValueError:
        return None
    if not math.isfinite(amount) or amount < 0:
        return None
    multiplier = _UNIT_MULTIPLIERS.get((unit_text or "s").lower(), 1.0)
    return _clamp_ttl(amount * multiplier)


def _parse_iso_reset_ttl(text: str, current_now: float) -> float | None:
    for match in _ISO_TIMESTAMP_RE.finditer(text):
        raw = match.group(1)
        try:
            parsed = datetime.fromisoformat(raw.replace("z", "Z"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            continue
        delta = parsed.timestamp() - current_now
        if delta > 0:
            return _clamp_ttl(delta)
    return None


def parse_cooldown_ttl(output: str, now: float | None = None) -> float:
    text = output or ""
    current_now = time.time() if now is None else now
    retry_after = _RETRY_AFTER_RE.search(text)
    if retry_after is not None:
        parsed = _duration_from_match(retry_after.group(1), retry_after.group(2))
        if parsed is not None:
            return parsed
    relative = _RELATIVE_DURATION_RE.search(text)
    if relative is not None:
        parsed = _duration_from_match(relative.group(1), relative.group(2))
        if parsed is not None:
            return parsed
    iso_ttl = _parse_iso_reset_ttl(text, current_now)
    if iso_ttl is not None:
        return iso_ttl
    return DEFAULT_COOLDOWN_SECONDS


def _is_valid_timestamp(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _parse_marker_payload(payload: Any) -> CooldownMarker | None:
    if not isinstance(payload, dict):
        return None
    provider = payload.get("provider")
    model = payload.get("model")
    recorded_at = payload.get("recorded_at")
    expires_at = payload.get("expires_at")
    reason = payload.get("reason")
    output_excerpt = payload.get("output_excerpt")
    has_strings = (
        isinstance(provider, str)
        and bool(provider)
        and isinstance(model, str)
        and isinstance(reason, str)
        and isinstance(output_excerpt, str)
    )
    if not (has_strings and _is_valid_timestamp(recorded_at) and _is_valid_timestamp(expires_at)):
        return None
    rec = float(recorded_at)
    exp = float(expires_at)
    if exp <= rec:
        return None
    return CooldownMarker(
        provider=provider,
        model=model,
        recorded_at=rec,
        expires_at=exp,
        reason=reason,
        output_excerpt=output_excerpt,
    )


def _unlink_best_effort(path: str) -> None:
    with contextlib.suppress(OSError):
        os.unlink(path)


def read_active_cooldown(agent: str, *, now: float | None = None) -> CooldownMarker | None:
    path = marker_path(agent)
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        _unlink_best_effort(path)
        return None
    marker = _parse_marker_payload(payload)
    current_now = time.time() if now is None else now
    if marker is None or current_now >= marker.expires_at:
        _unlink_best_effort(path)
        return None
    return marker


def _write_marker_atomic(path: str, marker: CooldownMarker) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    fd, tmp_path = tempfile.mkstemp(prefix=".cooldown-", suffix=".tmp", dir=directory)
    try:
        os.chmod(tmp_path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(asdict(marker), fh, sort_keys=True)
        os.replace(tmp_path, path)
    except BaseException:
        _unlink_best_effort(tmp_path)
        raise


def record_cooldown(
    agent: str,
    model: str,
    output: str,
    *,
    now: float | None = None,
    ttl_seconds: float | None = None,
) -> CooldownMarker | None:
    from sandbox_executor.preflight import redact_output

    current_now = time.time() if now is None else now
    ttl = _clamp_ttl(ttl_seconds) if ttl_seconds is not None else parse_cooldown_ttl(output, current_now)
    excerpt = redact_output(output)[-_EXCERPT_CHARS:].strip()
    marker = CooldownMarker(
        provider=normalise_agent_id(agent),
        model=model,
        recorded_at=current_now,
        expires_at=current_now + ttl,
        reason="quota_exhausted",
        output_excerpt=excerpt,
    )
    path = marker_path(agent)
    try:
        _write_marker_atomic(path, marker)
    except OSError as exc:
        logger.warning("Could not write pre-flight cooldown marker %s: %s", path, exc)
        return None
    return marker


def clear_cooldown(agent: str) -> None:
    _unlink_best_effort(marker_path(agent))


def build_cooldown_result(
    marker: CooldownMarker,
    agent: str,
    model: str,
    image: str,
    *,
    now: float | None = None,
) -> PreflightResult:
    from sandbox_executor.preflight import PreflightResult, PreflightStatus

    current_now = time.time() if now is None else now
    remaining = max(1, math.ceil(marker.expires_at - current_now))
    reset_utc = datetime.fromtimestamp(marker.expires_at, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    hint = (
        f"{COOLDOWN_HINT_PREFIX} for '{marker.provider}' ({remaining}s remaining; resets at {reset_utc}). "
        "Skipping container probe. Use --skip-preflight or HOLON_SKIP_PREFLIGHT=1 to bypass."
    )
    return PreflightResult(
        status=PreflightStatus.QUOTA_EXHAUSTED,
        agent=agent,
        model=model,
        image=image,
        exit_code=None,
        output_excerpt=marker.output_excerpt,
        hint=hint,
    )


def active_cooldown_result(
    agent: str,
    model: str,
    image: str | None = None,
    *,
    now: float | None = None,
) -> PreflightResult | None:
    marker = read_active_cooldown(agent, now=now)
    if marker is None:
        return None
    agent_id = normalise_agent_id(agent)
    resolved_image = image or AGENT_IMAGE_MAPPING.get(agent_id, f"holon/agent-{agent_id}")
    return build_cooldown_result(marker, agent, model, resolved_image, now=now)


def sync_cooldown_for_result(result: PreflightResult, *, now: float | None = None) -> None:
    from sandbox_executor.preflight import PreflightStatus

    if result.status is PreflightStatus.QUOTA_EXHAUSTED:
        if result.hint.startswith(COOLDOWN_HINT_PREFIX):
            return
        record_cooldown(result.agent, result.model, result.output_excerpt, now=now)
    elif result.ok and not result.skipped:
        clear_cooldown(result.agent)
