import contextlib
import os
import re

from sandbox_executor.entrypoint.pytest_summary import summary_excerpt
from sandbox_executor.knowledge import CredentialRedactor

MESSAGE_MAX_CHARS = 2000
MAX_NAMED_FAILURES = 10
NAMES_MAX_CHARS = 600
VERIFICATION_DIR = os.path.join(".holon", "review", "verification")

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_FAILED_LINE = re.compile(r"^(?:\[gw\d+\]\s*)?(?:FAILED|ERROR)\s+(.+?)(?:\s+-\s.*)?$")


def _is_pytest_node_id(candidate: str) -> bool:
    return "::" in candidate or ".py" in candidate


def failed_test_ids(output: str) -> list[str]:
    ids: dict[str, None] = {}
    for line in _ANSI.sub("", output).splitlines():
        match = _FAILED_LINE.match(line.strip())
        if match and _is_pytest_node_id(match.group(1)):
            ids.setdefault(match.group(1).rstrip(":"), None)
    return list(ids)


def names_block(ids: list[str]) -> str:
    shown: list[str] = []
    used = 0
    for test_id in ids[:MAX_NAMED_FAILURES]:
        used += len(test_id) + 2
        if shown and used > NAMES_MAX_CHARS:
            break
        shown.append(test_id)
    if not shown:
        return ""
    omitted = len(ids) - len(shown)
    return "failed: " + ", ".join(shown) + (f" (+{omitted} more)" if omitted else "")


def write_full_output(workspace: str, step: str, text: str) -> str | None:
    relative = os.path.join(VERIFICATION_DIR, f"{step}.log")
    try:
        os.makedirs(os.path.join(workspace, VERIFICATION_DIR), exist_ok=True)
        with open(os.path.join(workspace, relative), "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:
        return None
    return relative


def clear_full_output(workspace: str, step: str) -> None:
    with contextlib.suppress(OSError):
        os.remove(os.path.join(workspace, VERIFICATION_DIR, f"{step}.log"))


def build_test_failure_message(returncode: int, raw_output: str, workspace: str, step: str = "test") -> str:
    text = CredentialRedactor.redact_text(raw_output)
    path = write_full_output(workspace, step, text)
    parts = [f"Resolver pre-push {step} failed (exit code {returncode})"]
    names = names_block(failed_test_ids(text))
    if names:
        parts.append(names)
    parts.append(f"full output: {path}" if path else "full output file could not be written")
    prefix = "; ".join(parts) + ": "
    return prefix + summary_excerpt(text, max(MESSAGE_MAX_CHARS - len(prefix), 0))


def _reports_failures_and_errors(flag_chars: set[str]) -> bool:
    return "A" in flag_chars or {"f", "E"} <= flag_chars or {"a", "E"} <= flag_chars


def with_short_summary(env: dict[str, str]) -> dict[str, str]:
    addopts = env.get("PYTEST_ADDOPTS", "")
    for match in re.finditer(r"(?:^|\s)-r([A-Za-z]*)", addopts):
        if _reports_failures_and_errors(set(match.group(1))):
            return env
    return {**env, "PYTEST_ADDOPTS": f"{addopts} -rfE".strip()}
