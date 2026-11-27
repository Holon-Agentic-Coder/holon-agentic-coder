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

from sandbox_executor.agent_defaults import normalise_agent_id

if TYPE_CHECKING:
    from sandbox_executor.preflight import PreflightResult

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_TTL_SECONDS = 300.0
MIN_COOLDOWN_TTL_SECONDS = 1.0
MAX_COOLDOWN_TTL_SECONDS = 3600.0

_OWNER_ONLY_DIR_MODE = 0o700
_GROUP_AND_OTHER_PERMS_MASK = 0o077
_OWNER_ONLY_FILE_MODE = 0o600
_SAFE_ID_RE = re.compile(r"[^a-z0-9_-]+")
_SPEND_LIMIT_RE = re.compile(
    r"(?:(?:org(?:anization)?|monthly)\s+)?spend(?:ing)?\s+limit"
    r"|credit\s+balance\s+is\s+too\s+low|insufficient_quota|billing\s+hard\s+limit",
    re.IGNORECASE,
)
_RETRY_AFTER_SECONDS_RE = re.compile(
    r"retry[-_\s]+after[\s:=]+(\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds?)?\b", re.IGNORECASE
)
_RETRY_IN_DURATION_RE = re.compile(
    r"(?:retry|try\s+again|resets?)\s+(?:in|after)\s+(\d+(?:\.\d+)?)\s*"
    r"(s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hrs|hours?)\b",
    re.IGNORECASE,
)
_ISO_RESET_TIMESTAMP_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))\b")
_UNIT_SECONDS = {"s": 1.0, "m": 60.0, "h": 3600.0}


@dataclass(frozen=True)
class CooldownMarker:
    agent_id: str
    agent: str
    model: str
    reason: str
    recorded_at: float
    expires_at: float
    output_excerpt: str

    def remaining_seconds(self, now: float | None = None) -> float:
        return max(0.0, self.expires_at - _current_time(now))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _current_time(now: float | None) -> float:
    return time.time() if now is None else float(now)


def _clamp_ttl(seconds: float) -> float:
    return max(MIN_COOLDOWN_TTL_SECONDS, min(MAX_COOLDOWN_TTL_SECONDS, float(seconds)))


def _format_utc(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _remove_quietly(path: str) -> None:
    with contextlib.suppress(OSError):
        os.unlink(path)


def is_spend_limit_error(text: str) -> bool:
    return bool(_SPEND_LIMIT_RE.search(text or ""))


def cooldown_dir(state_dir: str | None = None) -> str:
    if state_dir is not None:
        return state_dir
    return os.path.expanduser(os.path.join("~", ".holon", "preflight", "cooldowns"))


def marker_path(agent: str, state_dir: str | None = None) -> str:
    safe_id = _SAFE_ID_RE.sub("_", normalise_agent_id(agent)).strip("_") or "unknown"
    return os.path.join(cooldown_dir(state_dir), f"{safe_id}.json")


def _parse_iso_ttl(output: str, current_time: float) -> float | None:
    for match in _ISO_RESET_TIMESTAMP_RE.finditer(output):
        try:
            delta = datetime.fromisoformat(match.group(1).replace("Z", "+00:00")).timestamp() - current_time
        except ValueError:
            continue
        if delta > 0:
            return delta
    return None


def _parse_duration_ttl(output: str) -> float | None:
    if match := _RETRY_AFTER_SECONDS_RE.search(output):
        value = float(match.group(1))
        if value > 0:
            return value
    if match := _RETRY_IN_DURATION_RE.search(output):
        value = float(match.group(1)) * _UNIT_SECONDS[match.group(2)[0].lower()]
        if value > 0:
            return value
    return None


def extract_cooldown_ttl(
    output: str, now: float | None = None, default_ttl: float = DEFAULT_COOLDOWN_TTL_SECONDS
) -> float:
    text = output or ""
    parsed = _parse_duration_ttl(text)
    if parsed is None:
        parsed = _parse_iso_ttl(text, _current_time(now))
    return _clamp_ttl(default_ttl if parsed is None else parsed)


def _decode_marker(payload: Any) -> CooldownMarker:
    if not isinstance(payload, dict):
        raise ValueError("cooldown marker must be a JSON object")
    recorded_at = float(payload["recorded_at"])
    expires_at = float(payload["expires_at"])
    if not (math.isfinite(recorded_at) and math.isfinite(expires_at)):
        raise ValueError("cooldown timestamps must be finite")
    return CooldownMarker(
        agent_id=str(payload["agent_id"]),
        agent=str(payload["agent"]),
        model=str(payload["model"]),
        reason=str(payload["reason"]),
        recorded_at=recorded_at,
        expires_at=expires_at,
        output_excerpt=str(payload.get("output_excerpt", "")),
    )


def read_active_cooldown(
    agent: str, *, now: float | None = None, state_dir: str | None = None
) -> CooldownMarker | None:
    path = marker_path(agent, state_dir)
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        _remove_quietly(path)
        return None
    except OSError:
        return None
    try:
        marker = _decode_marker(payload)
    except (KeyError, TypeError, ValueError):
        _remove_quietly(path)
        return None
    if marker.expires_at <= _current_time(now):
        _remove_quietly(path)
        return None
    return marker


def _ensure_cooldown_dir(directory: str) -> None:
    os.makedirs(directory, mode=_OWNER_ONLY_DIR_MODE, exist_ok=True)
    if os.stat(directory).st_mode & _GROUP_AND_OTHER_PERMS_MASK:
        os.chmod(directory, _OWNER_ONLY_DIR_MODE)


def _write_marker_atomic(path: str, marker: CooldownMarker) -> None:
    directory = os.path.dirname(path)
    _ensure_cooldown_dir(directory)
    fd, tmp_path = tempfile.mkstemp(prefix=f".{marker.agent_id}-", suffix=".tmp", dir=directory)
    try:
        os.fchmod(fd, _OWNER_ONLY_FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(marker.to_dict(), fh, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_path, path)
    except BaseException:
        _remove_quietly(tmp_path)
        raise


def record_cooldown(
    result: PreflightResult,
    *,
    now: float | None = None,
    ttl_seconds: float | None = None,
    state_dir: str | None = None,
) -> CooldownMarker | None:
    if result.skipped or result.status.value != "quota_exhausted":
        return None
    current_time = _current_time(now)
    ttl = (
        _clamp_ttl(ttl_seconds)
        if ttl_seconds is not None
        else extract_cooldown_ttl(result.output_excerpt, now=current_time)
    )
    reason = "spend_limit" if is_spend_limit_error(result.output_excerpt) else "quota_exhausted"
    marker = CooldownMarker(
        agent_id=normalise_agent_id(result.agent),
        agent=result.agent,
        model=result.model,
        reason=reason,
        recorded_at=current_time,
        expires_at=current_time + ttl,
        output_excerpt=result.output_excerpt,
    )
    path = marker_path(result.agent, state_dir)
    try:
        _write_marker_atomic(path, marker)
    except OSError as exc:
        logger.warning("Could not write pre-flight cooldown marker %s: %s", path, exc)
        return None
    return marker


def clear_cooldown(agent: str, *, state_dir: str | None = None) -> None:
    _remove_quietly(marker_path(agent, state_dir))


def build_short_circuit_result(
    marker: CooldownMarker, agent: str, model: str, image: str, *, now: float | None = None
) -> PreflightResult:
    # why: lazy import avoids a circular dependency between preflight and preflight_cooldown.
    from sandbox_executor.preflight import PreflightResult, PreflightStatus

    remaining = max(1, math.ceil(marker.remaining_seconds(now)))
    reset_iso = _format_utc(marker.expires_at)
    hint = (
        f"Provider '{marker.agent_id}' is in pre-flight cooldown ({marker.reason}) for {remaining}s until {reset_iso}. "
        "Wait for the cooldown to expire, choose another runner with --agent or HOLON_AGENT, or use --skip-preflight "
        "to bypass."
    )
    return PreflightResult(
        status=PreflightStatus.QUOTA_EXHAUSTED,
        agent=agent,
        model=model,
        image=image,
        exit_code=None,
        output_excerpt=marker.output_excerpt,
        hint=hint,
        skipped=False,
    )


def short_circuit_result(
    agent: str, model: str, image: str, *, now: float | None = None, state_dir: str | None = None
) -> PreflightResult | None:
    marker = read_active_cooldown(agent, now=now, state_dir=state_dir)
    if marker is None:
        return None
    return build_short_circuit_result(marker, agent, model, image, now=now)


def sync_probe_result(result: PreflightResult, *, now: float | None = None, state_dir: str | None = None) -> None:
    if result.skipped:
        return
    if result.status.value == "quota_exhausted":
        record_cooldown(result, now=now, state_dir=state_dir)
    elif result.status.value == "ok":
        clear_cooldown(result.agent, state_dir=state_dir)
