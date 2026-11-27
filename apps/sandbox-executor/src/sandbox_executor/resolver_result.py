from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sandbox_executor.redaction import redact_agent_secrets

# why: the host's input payload never carries this key, so its absence means the resolver wrote no result.
RESOLVER_RESULT_MARKER = "resolver"
# why: half the 2,000-character error context bound, so a short error plus its tail survives the halt reason intact.
OUTPUT_TAIL_MAX_CHARS = 1000


def bounded_output_tail(text: str | None, limit: int = OUTPUT_TAIL_MAX_CHARS) -> str:
    redacted = redact_agent_secrets(text or "").strip()
    return redacted if len(redacted) <= limit else "…" + redacted[-limit:]


def has_resolver_result(data: Mapping[str, Any]) -> bool:
    return data.get(RESOLVER_RESULT_MARKER) is True


def _exit_code(raw: Any) -> int | None:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def resolver_error_text(error: Any, exit_code: Any = None, output_tail: Any = None) -> str | None:
    code = _exit_code(exit_code)
    if not error and not code:
        return None
    message = redact_agent_secrets(str(error)) if error else f"Resolver container exited with code {code}"
    if code and f"code {code}" not in message:
        message += f" (resolver exit code {code})"
    tail = bounded_output_tail(str(output_tail)) if output_tail else ""
    if tail and tail.removeprefix("…") not in message:
        message += f"; output tail: {tail}"
    return message


@dataclass(frozen=True)
class ContainerResolverResult:
    raw_dispositions: Any
    head_sha: str
    lint_passed: bool
    tests_passed: bool
    error: str | None
    rejected_patch: str | None = None
    rejected_patch_text: str = ""
    failed_tests: tuple[str, ...] = ()
    test_summary: str | None = None


def rejected_attempt_fields(data: Mapping[str, Any]) -> dict[str, Any]:
    patch = data.get("rejected_patch")
    tests = data.get("failed_tests")
    summary = data.get("test_summary")
    patch_text = data.get("rejected_patch_text")
    return {
        "rejected_patch": redact_agent_secrets(patch) if isinstance(patch, str) and patch else None,
        "rejected_patch_text": redact_agent_secrets(patch_text) if isinstance(patch_text, str) else "",
        "failed_tests": tuple(redact_agent_secrets(t) for t in tests if isinstance(t, str))
        if isinstance(tests, list)
        else (),
        "test_summary": redact_agent_secrets(summary) if isinstance(summary, str) and summary else None,
    }


# why: a non-zero exit or a missing resolver marker is always a failed pass, never a pass with no changes.
def read_container_resolver_result(
    data: Mapping[str, Any],
    exit_code: int,
    fallback_head_sha: str,
) -> ContainerResolverResult:
    if not has_resolver_result(data) and (exit_code != 0 or "dispositions" not in data):
        how = "exited cleanly" if exit_code == 0 else f"exited with code {exit_code}"
        return ContainerResolverResult(
            raw_dispositions=[],
            head_sha=fallback_head_sha,
            lint_passed=False,
            tests_passed=False,
            error=f"Resolver container {how} and produced no resolver result",
        )
    return ContainerResolverResult(
        raw_dispositions=data.get("dispositions"),
        head_sha=str(data.get("head_sha") or fallback_head_sha or "").strip(),
        lint_passed=bool(data.get("lint_passed", True)),
        tests_passed=bool(data.get("tests_passed", True)),
        error=resolver_error_text(data.get("error"), exit_code, data.get("output_tail")),
        **rejected_attempt_fields(data),
    )
