"""Failure detail of a failed pre-push test run: named failed tests, a head-and-tail excerpt and the full output file.

The full output is redacted once, before extraction, truncation and writing, so a secret cut at a boundary cannot
slip past the redaction patterns. The file lives under the git-ignored ``.holon/`` directory of the workspace.
"""

import os
import re

from sandbox_executor.knowledge import CredentialRedactor

MESSAGE_MAX_CHARS = 2000
MAX_NAMED_FAILURES = 10
NAMES_MAX_CHARS = 600
HEAD_CHARS = 300
VERIFICATION_DIR = os.path.join(".holon", "review", "verification")

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_FAILED_LINE = re.compile(r"^(?:\[gw\d+\]\s*)?(?:FAILED|ERROR)\s+(\S+)")


def failed_test_ids(output: str) -> list[str]:
    """Return the distinct failed node ids named by ``FAILED``/``ERROR`` summary lines, in order of appearance."""
    ids: dict[str, None] = {}
    for line in _ANSI.sub("", output).splitlines():
        match = _FAILED_LINE.match(line.strip())
        if match:
            ids.setdefault(match.group(1).rstrip(":"), None)
    return list(ids)


def names_block(ids: list[str]) -> str:
    """Format ``failed: a, b (+K more)``, capped by count and characters; empty when there are no ids."""
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
    """Write ``text`` to a fixed per-step file under ``.holon/``; return its workspace-relative path or None."""
    relative = os.path.join(VERIFICATION_DIR, f"{step}.log")
    try:
        os.makedirs(os.path.join(workspace, VERIFICATION_DIR), exist_ok=True)
        with open(os.path.join(workspace, relative), "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:
        return None
    return relative


def head_and_tail(text: str, budget: int) -> str:
    """Return ``text`` unchanged when it fits ``budget``, else a short head and the tail around an omission marker."""
    if len(text) <= budget:
        return text
    head = text[: min(HEAD_CHARS, budget // 3)]
    marker_room = len(f" ... [{len(text)} characters omitted] ... ")
    tail_budget = max(budget - len(head) - marker_room, 0)
    marker = f" ... [{len(text) - len(head) - tail_budget} characters omitted] ... "
    return head + marker + (text[-tail_budget:] if tail_budget else "")


def build_test_failure_message(returncode: int, raw_output: str, workspace: str, step: str = "test") -> str:
    """Build ``Resolver pre-push <step> failed (exit code N); failed: ...; full output: <path>: <excerpt>``."""
    text = CredentialRedactor.redact_text(raw_output)
    path = write_full_output(workspace, step, text)
    parts = [f"Resolver pre-push {step} failed (exit code {returncode})"]
    names = names_block(failed_test_ids(text))
    if names:
        parts.append(names)
    parts.append(f"full output: {path}" if path else "full output file could not be written")
    prefix = "; ".join(parts) + ": "
    return prefix + head_and_tail(text, max(MESSAGE_MAX_CHARS - len(prefix), 0))


def with_short_summary(env: dict[str, str]) -> dict[str, str]:
    """Return ``env`` with ``-rf`` appended to ``PYTEST_ADDOPTS`` unless a ``-r`` option already reports failures."""
    addopts = env.get("PYTEST_ADDOPTS", "")
    for match in re.finditer(r"(?:^|\s)-r([A-Za-z]*)", addopts):
        if set(match.group(1)) & set("faA"):
            return env
    return {**env, "PYTEST_ADDOPTS": f"{addopts} -rf".strip()}
