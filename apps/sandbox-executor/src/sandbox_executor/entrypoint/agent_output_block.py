import os
import sys

from sandbox_executor.redaction import _MAX_REDACT_INPUT_LEN, redact_agent_secrets, redact_env_literals

_DEFAULT_AGENT_LOG_BYTE_BUDGET: int = 65536
# why: capping marker reserve at half the budget trades <256B accuracy rather than the never-mid-line guarantee
_TRUNCATION_MARKER_RESERVE: int = 128
# why: reaching past the cut keeps preceding key lines in regex scope; cost knob only, front-drop enforces safety
_ANCHOR_CONTEXT_BYTES: int = 4096
# why: reserving _ANCHOR_CONTEXT_BYTES under _MAX_REDACT_INPUT_LEN avoids raw redactor cuts that sever secrets
_AGENT_LOG_BYTE_CEILING: int = _MAX_REDACT_INPUT_LEN - _ANCHOR_CONTEXT_BYTES
_NO_COMPLETE_LINE_CAUSE: str = "no complete line within budget"
_ANCHOR_CONTEXT_DROPPED_CAUSE: str = "anchor context dropped"


def get_agent_log_byte_budget() -> int:
    raw_env_budget = os.getenv("HOLON_AGENT_LOG_BYTES")
    if raw_env_budget:
        try:
            parsed = int(raw_env_budget)
            if parsed > 0:
                if parsed > _AGENT_LOG_BYTE_CEILING:
                    print(
                        f"Warning: HOLON_AGENT_LOG_BYTES={parsed} exceeds the {_AGENT_LOG_BYTE_CEILING} "
                        f"byte redaction limit (the {_MAX_REDACT_INPUT_LEN} byte redaction input cap minus "
                        f"the {_ANCHOR_CONTEXT_BYTES} bytes of anchor context the byte cut needs); bounding "
                        "to the limit instead, because output beyond it is not redacted faithfully.",
                        file=sys.stderr,
                    )
                    return _AGENT_LOG_BYTE_CEILING
                return parsed
        except (ValueError, TypeError):
            pass
    return _DEFAULT_AGENT_LOG_BYTE_BUDGET


def _marker_line(dropped_bytes: int, tail_len: int, reason: str = "") -> str:
    return f"[Agent output truncated: {dropped_bytes} bytes dropped; showing tail {tail_len} bytes{reason}]\n"


def _marker_reason(*causes: str) -> str:
    return "".join(f"; {cause}" for cause in causes if cause)


def _tail_budget_for(max_bytes: int) -> int:
    marker_reserve = min(_TRUNCATION_MARKER_RESERVE, max_bytes // 2)
    return max_bytes - marker_reserve


def _bound_tail(encoded: bytes, max_bytes: int) -> tuple[bytes, int, str]:
    tail_budget = _tail_budget_for(max_bytes)
    tail_bytes = encoded[-tail_budget:] if tail_budget > 0 else b""
    dropped_bytes = len(encoded) - len(tail_bytes)
    newline_at = tail_bytes.find(b"\n")
    if 0 <= newline_at < len(tail_bytes) - 1:
        dropped_bytes += newline_at + 1
        return tail_bytes[newline_at + 1 :], dropped_bytes, ""
    return b"", dropped_bytes + len(tail_bytes), _NO_COMPLETE_LINE_CAUSE


def _drop_unanchorable_front(redacted_region: str) -> tuple[str, int]:
    leading_len = len(redacted_region) - len(redacted_region.lstrip())
    newline_at = redacted_region.find("\n", leading_len)
    if newline_at < 0:
        return "", len(redacted_region.encode("utf-8"))
    front = redacted_region[: newline_at + 1]
    return redacted_region[len(front) :], len(front.encode("utf-8"))


def _trim_leading_lines_to_budget(
    encoded_tail: bytes,
    tail_budget: int,
    causes: list[str],
) -> tuple[bytes, int]:
    pos = 0
    while len(encoded_tail) - pos > tail_budget:
        newline_at = encoded_tail.find(b"\n", pos)
        if newline_at < 0 or newline_at == len(encoded_tail) - 1:
            pos = len(encoded_tail)
            if _NO_COMPLETE_LINE_CAUSE not in causes:
                causes.append(_NO_COMPLETE_LINE_CAUSE)
            break
        pos = newline_at + 1
    return encoded_tail[pos:], pos


def prepare_agent_output_block(
    raw_output: str,
    max_bytes: int = _DEFAULT_AGENT_LOG_BYTE_BUDGET,
) -> tuple[str, bool, int]:
    if max_bytes <= 0:
        max_bytes = _DEFAULT_AGENT_LOG_BYTE_BUDGET
    # why: clamping keeps the full redaction region inside what redactors process faithfully without dropping output
    if max_bytes > _AGENT_LOG_BYTE_CEILING:
        max_bytes = _AGENT_LOG_BYTE_CEILING

    swept = redact_env_literals(raw_output)
    encoded = swept.encode("utf-8")
    if len(encoded) <= max_bytes:
        # why: redact swept (never raw_output); sweeping shrinks values, avoiding _MAX_REDACT_INPUT_LEN cuts
        redacted = redact_agent_secrets(swept)
        if len(redacted.encode("utf-8")) <= max_bytes:
            return redacted, False, 0

    tail_budget = _tail_budget_for(max_bytes)
    redacted_region, dropped_bytes, causes = _redact_cut_region(encoded, tail_budget)
    # why: dropping whole leading redacted lines avoids refit loops and never commits partial or unredacted lines
    encoded_tail, trimmed_bytes = _trim_leading_lines_to_budget(
        redacted_region.encode("utf-8"),
        tail_budget,
        causes,
    )
    dropped_bytes += trimmed_bytes

    tail = encoded_tail.decode("utf-8", errors="replace")
    marker = _marker_line(dropped_bytes, len(encoded_tail), _marker_reason(*causes))
    return marker + tail, True, dropped_bytes


def _redact_cut_region(encoded: bytes, tail_budget: int) -> tuple[str, int, list[str]]:
    cut_offset = max(0, len(encoded) - tail_budget)
    context_start = max(0, cut_offset - _ANCHOR_CONTEXT_BYTES)
    context_break = encoded.rfind(b"\n", 0, context_start)
    ideal_start = 0 if context_break < 0 else context_break + 1
    # why: _MAX_REDACT_INPUT_LEN wins over context to avoid raw cuts; _drop_unanchorable_front preserves safety
    region_start = max(ideal_start, len(encoded) - _MAX_REDACT_INPUT_LEN)
    causes: list[str] = []
    if region_start > ideal_start:
        causes.append(_ANCHOR_CONTEXT_DROPPED_CAUSE)

    dropped_bytes = region_start
    region = encoded[region_start:].decode("utf-8", errors="replace")
    redacted_region = redact_agent_secrets(region)
    # why: a non-zero-start region's first line cannot be anchored internally, so drop it to avoid unredacted output
    if region_start:
        redacted_region, front_bytes = _drop_unanchorable_front(redacted_region)
        dropped_bytes += front_bytes
        if not redacted_region:
            causes.append(_NO_COMPLETE_LINE_CAUSE)
    return redacted_region, dropped_bytes, causes


def sanitize_agent_output(
    raw_output: str,
    max_bytes: int = _DEFAULT_AGENT_LOG_BYTE_BUDGET,
) -> tuple[str, bool, int]:
    if max_bytes <= 0:
        max_bytes = _DEFAULT_AGENT_LOG_BYTE_BUDGET

    encoded = raw_output.encode("utf-8")
    if len(encoded) <= max_bytes:
        return raw_output, False, 0

    tail_bytes, dropped_bytes, cause = _bound_tail(encoded, max_bytes)
    decoded_tail = tail_bytes.decode("utf-8", errors="replace")
    marker = _marker_line(dropped_bytes, len(tail_bytes), _marker_reason(cause))
    return marker + decoded_tail, True, dropped_bytes
