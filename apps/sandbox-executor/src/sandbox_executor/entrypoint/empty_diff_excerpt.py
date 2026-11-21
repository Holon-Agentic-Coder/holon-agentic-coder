"""Bounded, redacted excerpts of agent output for empty_diff diagnostics."""

from __future__ import annotations

import sys

from sandbox_executor.redaction import redact_agent_secrets

EMPTY_DIFF_EXCERPT_CHARS: int = 500


def format_empty_diff_excerpt(agent_output_text: str, agent_output_bytes: int) -> str | None:
    """Format a bounded, redacted tail excerpt of agent output for empty_diff diagnostics.

    Returns None if agent_output_text is empty or whitespace-only. Otherwise, extracts
    up to the last EMPTY_DIFF_EXCERPT_CHARS characters, redacts any secrets defensively,
    and returns a header with byte count followed by the excerpt.
    """
    if not agent_output_text or not agent_output_text.strip():
        return None

    tail = agent_output_text[-EMPTY_DIFF_EXCERPT_CHARS:]
    redacted_tail = redact_agent_secrets(tail)
    tail_bytes = len(redacted_tail.encode("utf-8"))
    return f"Agent output tail (last {tail_bytes} of {agent_output_bytes} bytes, redacted):\n{redacted_tail}"


def report_empty_diff_output(
    failure_reason: str | None,
    agent_output_text: str,
    agent_output_bytes: int,
) -> None:
    """Print bounded, redacted agent output excerpt to stderr when failure_reason is empty_diff."""
    from sandbox_executor.git import EMPTY_DIFF_FAILURE_REASON

    if failure_reason == EMPTY_DIFF_FAILURE_REASON:
        excerpt = format_empty_diff_excerpt(agent_output_text, agent_output_bytes)
        if excerpt:
            print(excerpt, file=sys.stderr)
