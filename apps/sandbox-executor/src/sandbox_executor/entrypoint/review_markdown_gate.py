"""Markdown formatting gate of the Stage 4 resolver's pre-push verification.

CI's hygiene job runs ``lint-md``, a Prettier check over every Markdown file, which ``uv run task lint`` does not. This
module runs the same Prettier check on only the Markdown files the resolver changed, formats them when they fail, and
re-checks, so the formatting lands in the same fix commit. Unrelated Markdown files are never passed to Prettier.
"""

import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from sandbox_executor.entrypoint.review_verification import MISSING_TOOL_EXIT_CODE, MISSING_TOOL_MARKER

#: The same unpinned spec as ``lint-md`` and ``lint-docs``, so the gate and CI agree on the Prettier release.
PRETTIER_COMMAND = ["npx", "prettier"]
MISSING_FORMATTER_HINT = (
    "Markdown checks need Node with npx and a reachable Prettier (network access, or Prettier baked into the "
    "review-loop image)"
)

#: Runs a command in the repository root and returns its result; the resolver supplies its own ``run_cmd``.
RunCommand = Callable[[list[str]], subprocess.CompletedProcess[str]]

_WARN_LINE = re.compile(r"^\[warn\]\s+(?P<path>.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class MarkdownGateOutcome:
    """Result of the gate: the files it covered, the files it formatted and the failure, if any."""

    checked: tuple[str, ...] = ()
    formatted: tuple[str, ...] = ()
    error: str | None = None

    @property
    def skipped(self) -> bool:
        """True when the resolver changed no Markdown, so no Prettier process was started."""
        return not self.checked


def changed_markdown_files(repo_dir: str, run: RunCommand, pathspec: list[str]) -> list[str]:
    """List existing changed ``.md`` files from ``git status --porcelain -z`` within ``pathspec``."""
    result = run(["git", "status", "--porcelain", "-z", "--untracked-files=all", *pathspec])
    if result.returncode != 0:
        return []
    entries = (result.stdout or "").split("\0")
    files: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        if "R" in status or "C" in status:
            index += 1  # the original path follows a rename or copy entry
        if "D" not in status and path.endswith(".md") and os.path.isfile(os.path.join(repo_dir, path)):
            files.append(path)
    return files


def _failed_files(output: str, candidates: list[str]) -> list[str]:
    named = [m.group("path") for m in _WARN_LINE.finditer(output)]
    failed = [path for path in candidates if path in named]
    return failed or candidates


def _failure(step: str, result: subprocess.CompletedProcess[str], files: list[str]) -> str:
    output = "\n".join(part for part in (result.stderr, result.stdout) if part).strip()
    if result.returncode == MISSING_TOOL_EXIT_CODE and MISSING_TOOL_MARKER in output:
        return f"{output}; {MISSING_FORMATTER_HINT}"
    named = ", ".join(_failed_files(output, files))
    return f"Prettier {step} failed (exit code {result.returncode}) for: {named}"


def run_markdown_gate(repo_dir: str, run: RunCommand, pathspec: list[str]) -> MarkdownGateOutcome:
    """Check changed Markdown with Prettier; on failure format those files and re-check once."""
    files = changed_markdown_files(repo_dir, run, pathspec)
    if not files:
        return MarkdownGateOutcome()
    checked = tuple(files)
    check = run([*PRETTIER_COMMAND, "--check", "--", *files])
    if check.returncode == 0:
        return MarkdownGateOutcome(checked=checked)
    if check.returncode == MISSING_TOOL_EXIT_CODE:
        return MarkdownGateOutcome(checked=checked, error=_failure("check", check, files))
    failing = _failed_files("\n".join(filter(None, (check.stderr, check.stdout))), files)
    write = run([*PRETTIER_COMMAND, "--write", "--", *failing])
    if write.returncode != 0:
        return MarkdownGateOutcome(checked=checked, error=_failure("write", write, failing))
    recheck = run([*PRETTIER_COMMAND, "--check", "--", *files])
    if recheck.returncode != 0:
        return MarkdownGateOutcome(checked=checked, error=_failure("check", recheck, files))
    return MarkdownGateOutcome(checked=checked, formatted=tuple(failing))
