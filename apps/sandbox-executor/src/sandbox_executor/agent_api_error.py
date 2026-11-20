"""Recognise an Anthropic (or other provider) API failure in an agent's captured stdout or stderr.

A planner run once received an API 400 (``system.2.cache_control.ttl: a ttl='1h' cache_control block must not come
after a ttl='5m' cache_control block``). The claude CLI printed it as a single ``API Error: 400 {...}`` line, and the
planner's stdout fallback wrote that line to the plan path as if it were the plan; the metrics check then hid the real
error. :func:`detect_api_error` lets a caller tell such output from a plan.

Detection is anchored, because a valid plan may quote the ``API Error:`` marker (a plan about this very incident does).
Only the first non-empty line is examined, unless the whole text is short (at most :data:`SHORT_TEXT_MAX_LINES`
non-empty lines) and has no ``# Plan for`` header, in which case every line is. On an examined line, any of these
counts:

- a line starting with the ``API Error:`` marker (case-sensitive, as the claude CLI prints it);
- an HTTP status line with a 4xx or 5xx code at line start (``HTTP/1.1 400 Bad Request``, ``HTTP/2 529``);
- a JSON body whose top level is an error object (``{"type": "error", "error": {...}}`` or an ``error`` object with
  ``type`` and ``message``), or the claude CLI result envelope with ``is_error: true`` whose ``result`` starts with
  ``API Error:``. A JSON body spanning the whole text counts too.

The module is pure and holds no credentials logic: redacting the returned message is the caller's job.
"""

from __future__ import annotations

import json
import re
from typing import Any

API_ERROR_MARKER = "API Error:"
MESSAGE_MAX_CHARS = 2000
SHORT_TEXT_MAX_LINES = 20
_CONTINUATION_LINES = 4
_PLAN_HEADER = "# Plan for"

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_HTTP_ERROR_STATUS = re.compile(r"^HTTP/\d(?:\.\d)?\s+[45]\d\d\b.*$")


def detect_api_error(*texts: str) -> str | None:
    """Return the first API error found in ``texts`` (checked in order) as one trimmed message, or None.

    Args:
        texts: Captured agent outputs, typically each turn's stdout then stderr. ``None`` or empty texts are skipped.

    Returns:
        The error message, clamped to :data:`MESSAGE_MAX_CHARS` characters, or None when no text holds an API error.
    """
    for text in texts:
        if not text:
            continue
        lines = [line.rstrip() for line in _ANSI_ESCAPE.sub("", text).splitlines()]
        examined = _examined_lines(lines)
        message = _marker_error(lines, examined) or _http_status_error(lines, examined) or _json_error(lines, examined)
        if message:
            return message.strip()[:MESSAGE_MAX_CHARS]
    return None


def _examined_lines(lines: list[str]) -> list[int]:
    """Indexes of the lines the anchoring rule lets a matcher look at (see the module docstring)."""
    non_empty = [index for index, line in enumerate(lines) if line.strip()]
    short = len(non_empty) <= SHORT_TEXT_MAX_LINES and not any(line.startswith(_PLAN_HEADER) for line in lines)
    return non_empty if short else non_empty[:1]


def _marker_error(lines: list[str], examined: list[int]) -> str | None:
    """The ``API Error:`` line plus its paragraph's continuation lines."""
    for index in examined:
        if lines[index].lstrip().startswith(API_ERROR_MARKER):
            return "\n".join(_paragraph(lines, index))
    return None


def _paragraph(lines: list[str], start: int) -> list[str]:
    """The line at ``start`` and up to a few following non-empty lines of the same paragraph."""
    paragraph = [lines[start].strip()]
    for line in lines[start + 1 : start + 1 + _CONTINUATION_LINES]:
        if not line.strip():
            break
        paragraph.append(line.strip())
    return paragraph


def _http_status_error(lines: list[str], examined: list[int]) -> str | None:
    """The first examined line that is an HTTP status line with a 4xx or 5xx code."""
    for index in examined:
        if _HTTP_ERROR_STATUS.match(lines[index]):
            return lines[index]
    return None


def _json_error(lines: list[str], examined: list[int]) -> str | None:
    """A compact message from an error JSON body: the whole text, or one examined line of it."""
    candidates = ["\n".join(lines).strip(), *(lines[index].strip() for index in examined)]
    for candidate in candidates:
        if not candidate.startswith("{"):
            continue
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        message = _error_from_json(data)
        if message:
            return message
    return None


def _error_from_json(data: Any) -> str | None:
    """Describe a top-level error object, or a claude CLI ``is_error`` envelope carrying an API error."""
    if not isinstance(data, dict):
        return None
    if data.get("is_error") is True and str(data.get("result") or "").startswith(API_ERROR_MARKER):
        return str(data["result"])
    error = data.get("error")
    if not isinstance(error, dict):
        return None
    if data.get("type") == "error" or ("type" in error and "message" in error):
        return f"API error {error.get('type', 'unknown')}: {error.get('message', '')}"
    return None
