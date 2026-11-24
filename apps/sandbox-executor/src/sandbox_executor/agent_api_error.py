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
    non_empty = [index for index, line in enumerate(lines) if line.strip()]
    scan_all_lines = len(non_empty) <= SHORT_TEXT_MAX_LINES and not any(line.startswith(_PLAN_HEADER) for line in lines)
    return non_empty if scan_all_lines else non_empty[:1]


def _marker_error(lines: list[str], examined: list[int]) -> str | None:
    for index in examined:
        if lines[index].lstrip().startswith(API_ERROR_MARKER):
            return "\n".join(_paragraph(lines, index))
    return None


def _paragraph(lines: list[str], start: int) -> list[str]:
    paragraph = [lines[start].strip()]
    for line in lines[start + 1 : start + 1 + _CONTINUATION_LINES]:
        if not line.strip():
            break
        paragraph.append(line.strip())
    return paragraph


def _http_status_error(lines: list[str], examined: list[int]) -> str | None:
    for index in examined:
        if _HTTP_ERROR_STATUS.match(lines[index]):
            return lines[index]
    return None


def _json_error(lines: list[str], examined: list[int]) -> str | None:
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
