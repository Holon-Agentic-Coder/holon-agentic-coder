from __future__ import annotations

import sys

from sandbox_executor.git import EMPTY_DIFF_FAILURE_REASON
from sandbox_executor.redaction import redact_agent_secrets, redact_credential_shapes

EMPTY_DIFF_EXCERPT_CHARS: int = 500
EMPTY_DIFF_REDACT_WINDOW_CHARS: int = 50_000


def format_empty_diff_excerpt(agent_output_text: str, agent_output_bytes: int) -> str | None:
    if not agent_output_text or not agent_output_text.strip():
        return None

    credential_swept_window = redact_credential_shapes(agent_output_text)[-EMPTY_DIFF_REDACT_WINDOW_CHARS:]
    redacted_tail_excerpt = redact_agent_secrets(credential_swept_window)[-EMPTY_DIFF_EXCERPT_CHARS:]
    return (
        f"Agent output tail (last {len(redacted_tail_excerpt)} chars of redacted output, "
        f"{agent_output_bytes} bytes total):\n{redacted_tail_excerpt}"
    )


def report_empty_diff_output(
    failure_reason: str | None,
    agent_output_text: str,
    agent_output_bytes: int,
) -> None:
    if failure_reason == EMPTY_DIFF_FAILURE_REASON:
        excerpt = format_empty_diff_excerpt(agent_output_text, agent_output_bytes)
        if excerpt:
            print(excerpt, file=sys.stderr)
