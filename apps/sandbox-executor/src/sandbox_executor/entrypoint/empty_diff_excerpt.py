"""Bounded, redacted excerpts of agent output for empty_diff diagnostics."""

from __future__ import annotations

import sys

from sandbox_executor.git import EMPTY_DIFF_FAILURE_REASON
from sandbox_executor.redaction import redact_agent_secrets, redact_credential_shapes

EMPTY_DIFF_EXCERPT_CHARS: int = 500


def format_empty_diff_excerpt(agent_output_text: str, agent_output_bytes: int) -> str | None:
    """Format a bounded, redacted tail excerpt of agent output for empty_diff diagnostics.

    Returns None if agent_output_text is empty or whitespace-only. Otherwise, sweeps the whole text for credential
    shapes before cutting the last EMPTY_DIFF_EXCERPT_CHARS characters, so a token straddling the cut cannot leak its
    suffix, then redacts the excerpt again with the key-name rules and returns it under a header.
    """
    if not agent_output_text or not agent_output_text.strip():
        return None

    tail = redact_agent_secrets(redact_credential_shapes(agent_output_text)[-EMPTY_DIFF_EXCERPT_CHARS:])
    return f"Agent output tail (last {len(tail)} chars of redacted output, {agent_output_bytes} bytes total):\n{tail}"


def report_empty_diff_output(
    failure_reason: str | None,
    agent_output_text: str,
    agent_output_bytes: int,
) -> None:
    """Print bounded, redacted agent output excerpt to stderr when failure_reason is empty_diff."""
    if failure_reason == EMPTY_DIFF_FAILURE_REASON:
        excerpt = format_empty_diff_excerpt(agent_output_text, agent_output_bytes)
        if excerpt:
            print(excerpt, file=sys.stderr)
