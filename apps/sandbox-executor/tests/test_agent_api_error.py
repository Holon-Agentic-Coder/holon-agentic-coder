"""Tests for the runner-agnostic agent API error detector."""

from __future__ import annotations

import json

import pytest
from sandbox_executor.agent_api_error import MESSAGE_MAX_CHARS, detect_api_error

INCIDENT_LINE = (
    'API Error: 400 {"type":"error","error":{"type":"invalid_request_error","message":"system.2.cache_control.ttl: '
    "a ttl='1h' cache_control block must not come after a ttl='5m' cache_control block\"}}"
)

QUOTING_PLAN = "\n".join(
    [
        "# Plan for I-1-api-error",
        "",
        "## Overall Plan Metrics",
        "",
        "| metric | value |",
        "| p_success_pred | 0.8 |",
        "",
        "## Strategy",
        "",
        "The planner printed:",
        "",
        INCIDENT_LINE,
        "HTTP/1.1 400 Bad Request",
        '{"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}',
        *[f"- detail line {index}" for index in range(30)],
    ]
)


@pytest.mark.parametrize(
    ("texts", "expected"),
    [
        ((INCIDENT_LINE,), "system.2.cache_control.ttl"),
        (("\x1b[31m" + INCIDENT_LINE + "\x1b[0m\n",), "cache_control block must not come after"),
        (("HTTP/2 529\ncontent-type: application/json\n",), "HTTP/2 529"),
        (("HTTP/1.1 400 Bad Request",), "400 Bad Request"),
        (
            ('{"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}',),
            "API error overloaded_error: Overloaded",
        ),
        (
            ('{\n  "error": {"type": "rate_limit_error", "message": "slow down"}\n}',),
            "API error rate_limit_error: slow down",
        ),
        (
            (json.dumps({"type": "result", "is_error": True, "result": INCIDENT_LINE}),),
            "API Error: 400",
        ),
        (("", "Starting turn...\n" + INCIDENT_LINE + "\n"), "system.2.cache_control.ttl"),
        ((None, INCIDENT_LINE), "API Error: 400"),
    ],
)
def test_detects_api_errors(texts: tuple[str, ...], expected: str) -> None:
    message = detect_api_error(*texts)
    assert message is not None
    assert expected in message


def test_marker_returns_its_paragraph_only() -> None:
    message = detect_api_error("API Error: 500 Internal\nretry later\n\nunrelated text")
    assert message == "API Error: 500 Internal\nretry later"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   \n\n",
        QUOTING_PLAN,
        "I wrote the plan to plans/I-1/P-1.md.\nAll sections are present.",
        "The run hit an api error: nothing serious, retried.",
        '{"type": "message", "content": []}',
        '{"error": "a bare string is not an API error object"}',
        "HTTP/1.1 200 OK",
    ],
)
def test_ignores_plans_and_chatter(text: str) -> None:
    assert detect_api_error(text) is None


def test_long_text_counts_marker_on_first_line_only() -> None:
    chatter = "\n".join(f"line {index}" for index in range(40))
    assert detect_api_error(f"{chatter}\n{INCIDENT_LINE}") is None
    assert detect_api_error(f"{INCIDENT_LINE}\n{chatter}") is not None


def test_message_is_clamped() -> None:
    message = detect_api_error("API Error: 400 " + "x" * (MESSAGE_MAX_CHARS * 2))
    assert message is not None
    assert len(message) == MESSAGE_MAX_CHARS
