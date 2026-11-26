"""Review request construction and scoped conflict-resolution diff building for Stage 4."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sandbox_executor.git import EMPTY_DIFF_EXCLUDES, run_git
from sandbox_executor.redaction import redact_agent_secrets

# Reviewer turns are single passes, so the loop-level placeholders get neutral text.
_SINGLE_PASS = "n/a (single reviewer turn)"
_PLACEHOLDER_NAMES = (
    "repo_dir",
    "target",
    "target_branch",
    "ensemble_size",
    "max_iterations",
    "iteration",
    "pr_title",
    "pr_description",
    "change_description",
    "diff",
    "change_diff",
    "sha",
)
# One pass over the template, so text substituted in (a diff that contains ``{diff}``) is never expanded again.
_PLACEHOLDER = re.compile(r"\{(" + "|".join(_PLACEHOLDER_NAMES) + r")\}")


@dataclass(frozen=True)
class ReviewRequest:
    """What every reviewer is asked to judge; nothing in it comes from another reviewer."""

    repo_dir: str
    head_sha: str
    base_ref: str
    pr_title: str
    pr_body: str
    diff_text: str
    iteration: int


def build_scoped_diff(
    repo_dir: str,
    base_ref: str,
    head_sha: str,
    review_paths: Sequence[str] | None = None,
) -> tuple[str, list[str]]:
    """Return ``(diff_text, active_scope_paths)`` between ``base_ref`` and ``head_sha``.

    Excludes ledgers, plans, and execution notes. When ``review_paths`` is non-empty, scopes the diff to those paths
    and falls back to the full PR diff if the scoped diff is empty.
    """
    excludes = [f":(exclude){prefix}" for prefix in EMPTY_DIFF_EXCLUDES]
    scoped = [str(p).strip() for p in (review_paths or ()) if str(p).strip()]
    pathspec = ["--", *scoped, *excludes] if scoped else ["--", ".", *excludes]
    try:
        proc = run_git(["diff", f"{base_ref}...{head_sha}", *pathspec], cwd=repo_dir, check=True, timeout=120)
        diff_text = proc.stdout or ""
        if scoped and not diff_text.strip():
            fallback = run_git(
                ["diff", f"{base_ref}...{head_sha}", "--", ".", *excludes], cwd=repo_dir, check=True, timeout=120
            )
            diff_text, scoped = fallback.stdout or "", []
    except TimeoutError as exc:
        raise RuntimeError(f"git diff timed out: {redact_agent_secrets(str(exc))}") from exc
    return diff_text, scoped


def format_scoped_body(body: str, scoped_paths: Sequence[str]) -> str:
    """Append the conflict-resolution re-review scope notice to ``body`` when ``scoped_paths`` is non-empty."""
    if not scoped_paths:
        return body
    bullets = "\n".join(f"- `{p}`" for p in scoped_paths)
    return f"{body}\n\n## Scoped Conflict-Resolution Re-Review\n\nReviewing resolved paths:\n{bullets}".strip()


@dataclass(frozen=True)
class ScopedDelta:
    """What a scoped delta review shows besides the diff; the file contents are already redacted and bounded."""

    base: str
    head: str
    context_files: tuple[tuple[str, str], ...] = ()
    addressed: tuple[Mapping[str, Any], ...] = ()
    omitted: tuple[str, ...] = ()


def _fenced(text: str) -> str:
    """``text`` in a code fence longer than any backtick run inside it."""
    fence = "`" * max([3, *(len(run) + 1 for run in re.findall(r"`+", text))])
    return f"{fence}\n{text.rstrip(chr(10))}\n{fence}"


def _addressed_line(row: Mapping[str, Any]) -> str:
    location = f"{row.get('path')}:{row.get('line')}" if row.get("line") else str(row.get("path") or "-")
    return f"- `{row.get('id')}` ({row.get('severity')}, `{location}`): {row.get('summary')}"


def format_delta_body(body: str, delta: ScopedDelta) -> str:
    """Append the scoped delta review notice, the addressed findings and the modified files' full content."""
    parts = [
        body,
        "## Scoped Delta Review",
        f"Only the change `{delta.base}..{delta.head}` is under review: an earlier pass in which every reviewer "
        "completed reviewed everything before it. Verify that the addressed findings are fixed and judge the change "
        "itself; do not re-review unchanged code.",
        "### Addressed findings",
        "\n".join(_addressed_line(row) for row in delta.addressed) or "None recorded.",
        "### Full content of modified files",
        *(f"`{path}`:\n\n{_fenced(text)}" for path, text in delta.context_files),
    ]
    if delta.omitted:
        parts.append("Not shown (context limit): " + ", ".join(f"`{path}`" for path in delta.omitted))
    return "\n\n".join(part for part in parts if part).strip()


def _placeholder_values(request: ReviewRequest) -> dict[str, str]:
    """Value for each allow-listed template placeholder."""
    return {
        "repo_dir": request.repo_dir,
        "target": request.head_sha,
        "target_branch": request.base_ref,
        "ensemble_size": _SINGLE_PASS,
        "max_iterations": _SINGLE_PASS,
        "iteration": str(request.iteration),
        "pr_title": request.pr_title,
        "pr_description": request.pr_body,
        "change_description": request.pr_body,
        "diff": request.diff_text,
        "change_diff": request.diff_text,
        "sha": request.head_sha,
    }


def _missing_inputs(template_text: str, request: ReviewRequest) -> str:
    """The change description and diff, for templates that do not already include them."""
    parts = []
    if "{pr_description}" not in template_text and "{change_description}" not in template_text:
        parts.append(f"Change description:\n\n```markdown\n{request.pr_body}\n```\n\n")
    if "{diff}" not in template_text and "{change_diff}" not in template_text:
        parts.append(f"Change diff:\n\n```diff\n{request.diff_text}\n```\n\n")
    return "".join(parts)
