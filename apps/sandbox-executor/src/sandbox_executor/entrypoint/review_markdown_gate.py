import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from sandbox_executor.entrypoint.review_verification import MISSING_TOOL_EXIT_CODE, MISSING_TOOL_MARKER

PRETTIER_COMMAND = ["npx", "prettier"]
MISSING_FORMATTER_HINT = (
    "Markdown checks need Node with npx and a reachable Prettier (network access, or Prettier baked into the "
    "review-loop image)"
)

RunCommand = Callable[[list[str]], subprocess.CompletedProcess[str]]

_WARN_LINE = re.compile(r"^\[warn\]\s+(?P<path>.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class MarkdownGateOutcome:
    checked: tuple[str, ...] = ()
    formatted: tuple[str, ...] = ()
    error: str | None = None

    @property
    def skipped(self) -> bool:
        return not self.checked


def _has_original_path_entry(status: str) -> bool:
    return "R" in status or "C" in status


def changed_markdown_files(repo_dir: str, run: RunCommand, pathspec: list[str]) -> list[str]:
    result = run(["git", "status", "--porcelain", "-z", "--untracked-files=all", *pathspec])
    if result.returncode != 0:
        detail = "\n".join(part for part in (result.stderr, result.stdout) if part).strip()
        raise RuntimeError(f"git status failed (exit code {result.returncode}): {detail}")
    entries = (result.stdout or "").split("\0")
    files: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        if _has_original_path_entry(status):
            index += 1
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
    listing_error: str | None = None
    try:
        files = changed_markdown_files(repo_dir, run, pathspec)
    except RuntimeError as exc:
        files, listing_error = [], str(exc)
    if not files:
        return MarkdownGateOutcome(error=listing_error)
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
