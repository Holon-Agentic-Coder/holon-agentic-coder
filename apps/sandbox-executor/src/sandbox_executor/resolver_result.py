"""The Stage 4 resolver result contract between the containerised resolver and the host review loop.

The resolver entrypoint (``entrypoint/review_loop.py --resolve``) always writes one JSON result carrying the
``"resolver": true`` marker, its dispositions, the head it left, the verification flags, the error, the exit code it is
about to exit with and a bounded, redacted ``output_tail``. The host flow reads that file back after the dispatch. This
module owns both halves, so the writer and the reader cannot drift: the bounded tail, the halt message that combines an
error with the exit code and the tail, and the fail-closed reading of a container result. A container that exits
non-zero, or leaves no resolver result behind (the file still holds the host's own input payload), is always a failed
pass, never a pass with no changes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sandbox_executor.redaction import redact_agent_secrets

# Key the resolver entrypoint sets to True in every result it writes; the host's input payload never carries it.
RESOLVER_RESULT_MARKER = "resolver"
OUTPUT_TAIL_MAX_CHARS = 2000


def bounded_output_tail(text: str | None, limit: int = OUTPUT_TAIL_MAX_CHARS) -> str:
    """Redact ``text`` first, then keep at most its last ``limit`` characters."""
    redacted = redact_agent_secrets(text or "").strip()
    return redacted if len(redacted) <= limit else "…" + redacted[-limit:]


def has_resolver_result(data: Mapping[str, Any]) -> bool:
    """True only when ``data`` was written by the resolver entrypoint (it carries the resolver marker)."""
    return data.get(RESOLVER_RESULT_MARKER) is True


def _exit_code(raw: Any) -> int | None:
    """Return ``raw`` as an exit code, or None when it is absent or not an integer."""
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def resolver_error_text(error: Any, exit_code: Any = None, output_tail: Any = None) -> str | None:
    """Build the redacted halt message of a failed resolver pass, or None when the pass did not fail.

    A pass failed when it reports an error or a non-zero exit code. The message names the exit code and ends with
    the bounded output tail, so the review loop's halt reason explains the failure on its own.
    """
    code = _exit_code(exit_code)
    if not error and not code:
        return None
    message = redact_agent_secrets(str(error)) if error else f"Resolver container exited with code {code}"
    if code and f"code {code}" not in message:
        message += f" (resolver exit code {code})"
    tail = bounded_output_tail(str(output_tail)) if output_tail else ""
    if tail and tail not in message:
        message += f"; output tail: {tail}"
    return message


@dataclass(frozen=True)
class ContainerResolverResult:
    """The fields of one container resolver result, read fail-closed; dispositions are still raw mappings."""

    raw_dispositions: Any
    head_sha: str
    pushed: bool
    lint_passed: bool
    tests_passed: bool
    error: str | None


def read_container_resolver_result(
    data: Mapping[str, Any],
    exit_code: int,
    fallback_head_sha: str,
) -> ContainerResolverResult:
    """Read the result file of a resolver container dispatch that exited with ``exit_code``.

    No resolver result (no marker and either a non-zero exit or no dispositions) fails with the exit code and nothing
    pushed or verified. A result from a container that exited non-zero fails with the entrypoint's error, the exit
    code and the output tail, even when it carries dispositions. Only a clean exit keeps the payload as reported.
    """
    if not has_resolver_result(data) and (exit_code != 0 or "dispositions" not in data):
        how = "exited cleanly" if exit_code == 0 else f"exited with code {exit_code}"
        return ContainerResolverResult(
            raw_dispositions=[],
            head_sha=fallback_head_sha,
            pushed=False,
            lint_passed=False,
            tests_passed=False,
            error=f"Resolver container {how} and produced no resolver result",
        )
    return ContainerResolverResult(
        raw_dispositions=data.get("dispositions"),
        head_sha=str(data.get("head_sha") or fallback_head_sha or "").strip(),
        pushed=bool(data.get("pushed", False)),
        lint_passed=bool(data.get("lint_passed", True)),
        tests_passed=bool(data.get("tests_passed", True)),
        error=resolver_error_text(data.get("error"), exit_code, data.get("output_tail")),
    )
