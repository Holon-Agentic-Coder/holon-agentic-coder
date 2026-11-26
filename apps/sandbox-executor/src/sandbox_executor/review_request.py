"""Review request construction and scoped conflict-resolution diff building for Stage 4.

Every review diff passes through ``bound_review_diff`` before a reviewer prompt is built: bulk deletions and generated
files are summarised by count and sample, other file sections are inlined whole up to ``INLINED_DIFF_MAX_BYTES``, and
the sections left out are listed by parent directory. A diff that needs none of this is returned unchanged.
"""

from __future__ import annotations

import fnmatch
import posixpath
import re
from collections import Counter
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

# A directory holding at least this many deleted files is a bulk deletion, summarised rather than inlined.
BULK_DELETION_MIN_FILES = 25
SUMMARY_SAMPLE_SIZE = 5
GENERATED_PATTERNS = ("*.lock", "package-lock.json", "*.min.js", "*.map")
INLINED_DIFF_MAX_BYTES = 400_000
# Each summary listing names at most this many directories, so the summary itself stays bounded.
_SUMMARY_MAX_DIRECTORIES = 200
_SUMMARY_HEADING = "# Review diff summary (not inlined)"
_SUMMARY_RULE = (
    f"# Bulk deletions (a directory with at least {BULK_DELETION_MIN_FILES} deleted files) and generated files "
    f"({', '.join(GENERATED_PATTERNS)}) are not inlined; judge a bulk deletion by its deleted-file count and sample "
    "paths, and a generated file by its count and sample."
)
_CAP_RULE = (
    f"# The inlined diff is capped at {INLINED_DIFF_MAX_BYTES} bytes; these whole file sections were left out "
    "(no section is cut mid-way):"
)


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
    and falls back to the full PR diff if the scoped diff is empty. The returned diff is bounded by
    ``bound_review_diff``.
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
    return bound_review_diff(diff_text), scoped


@dataclass(frozen=True)
class _DiffSection:
    """One ``diff --git`` file section, verbatim, with the path it is grouped and sampled by."""

    path: str
    text: str
    deleted: bool


def _diff_lines(text: str) -> list[str]:
    """``text`` split after each newline only, so joining the lines gives ``text`` back exactly."""
    parts = text.split("\n")
    return [f"{part}\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def _display_path(raw: str, prefixed: bool) -> str:
    """A path from a diff header line, best-effort unquoted and without its ``a/``/``b/`` prefix."""
    path = raw.rstrip("\n").rstrip("\t")
    if len(path) > 1 and path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    return path[2:] if prefixed and path[:2] in ("a/", "b/") else path


def _header_path(header_line: str) -> str:
    """The path of a ``diff --git a/X b/X`` line, for sections without ``---``/``+++`` lines."""
    rest = header_line[len("diff --git ") :].rstrip("\n")
    if rest.startswith('"'):
        return _display_path(rest[: rest.find('"', 1) + 1], prefixed=True)
    return _display_path(rest[: (len(rest) - 1) // 2], prefixed=True)


def _section(lines: list[str]) -> _DiffSection:
    """The section of ``lines``; its path prefers ``+++``, then ``---``, then ``rename to``, then the header."""
    header = []
    for line in lines[1:]:
        if line.startswith("@@"):
            break
        header.append(line.rstrip("\n"))
    found: dict[str, str] = {}
    for line in header:
        for prefix in ("+++ ", "--- ", "rename to "):
            if line.startswith(prefix) and line[len(prefix) :] != "/dev/null":
                found.setdefault(prefix, line[len(prefix) :])
    if "+++ " in found or "--- " in found:
        path = _display_path(found.get("+++ ") or found["--- "], prefixed=True)
    elif "rename to " in found:
        path = _display_path(found["rename to "], prefixed=False)
    else:
        path = _header_path(lines[0])
    deleted = any(line.startswith("deleted file mode") for line in header)
    return _DiffSection(path, "".join(lines), deleted)


def _split_sections(diff_text: str) -> tuple[str, list[_DiffSection]]:
    """``(preamble, sections)`` of a unified diff; the preamble is whatever precedes the first section."""
    preamble: list[str] = []
    chunks: list[list[str]] = []
    for line in _diff_lines(diff_text):
        if line.startswith("diff --git "):
            chunks.append([line])
        elif chunks:
            chunks[-1].append(line)
        else:
            preamble.append(line)
    return "".join(preamble), [_section(chunk) for chunk in chunks]


def _is_generated(path: str) -> bool:
    name = posixpath.basename(path)
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in GENERATED_PATTERNS)


def _parent(path: str) -> str:
    return posixpath.dirname(path) or "."


def _within_cap(sections: list[_DiffSection]) -> tuple[list[_DiffSection], list[_DiffSection]]:
    """``(inlined, omitted)``: whole sections in git order while the inlined total stays within the cap."""
    inlined: list[_DiffSection] = []
    omitted: list[_DiffSection] = []
    total = 0
    for section in sections:
        size = len(section.text.encode("utf-8"))
        if total + size <= INLINED_DIFF_MAX_BYTES:
            inlined.append(section)
            total += size
        else:
            omitted.append(section)
    return inlined, omitted


def _grouped_lines(sections: list[_DiffSection], label: str, *, sample: bool) -> list[str]:
    """One summary line per parent directory: its file count and, when ``sample``, the first few paths."""
    groups: dict[str, list[str]] = {}
    for section in sections:
        groups.setdefault(_parent(section.path), []).append(section.path)
    lines = []
    for directory, paths in sorted(groups.items()):
        samples = f"; sample: {', '.join(paths[:SUMMARY_SAMPLE_SIZE])}" if sample else ""
        lines.append(f"#   {directory}/: {len(paths)} {label}{samples}")
    if len(lines) > _SUMMARY_MAX_DIRECTORIES:
        more = len(lines) - _SUMMARY_MAX_DIRECTORIES
        lines = [*lines[:_SUMMARY_MAX_DIRECTORIES], f"#   ... and {more} more directories"]
    return lines


def _summary_block(bulk: list[_DiffSection], generated: list[_DiffSection], omitted: list[_DiffSection]) -> str:
    """The plain-text summary of every section not inlined, as diff-comment lines after the inlined sections."""
    lines = [_SUMMARY_HEADING]
    if bulk or generated:
        lines.append(_SUMMARY_RULE)
    lines += _grouped_lines(bulk, "deleted files (bulk deletion)", sample=True)
    lines += _grouped_lines(generated, "generated files", sample=True)
    if omitted:
        lines += [_CAP_RULE, *_grouped_lines(omitted, "files not shown", sample=False)]
    return "\n".join(lines) + "\n"


def bound_review_diff(diff_text: str) -> str:
    """``diff_text`` bounded for a reviewer prompt; returned unchanged (the same object) when no rule applies.

    Generated files and deletions in a directory with at least ``BULK_DELETION_MIN_FILES`` deleted files are
    summarised by count and sample; every other section is inlined whole, in git order, while the inlined total stays
    within ``INLINED_DIFF_MAX_BYTES``, and the sections that do not fit are listed by parent directory.
    """
    preamble, sections = _split_sections(diff_text)
    generated = [section for section in sections if _is_generated(section.path)]
    rest = [section for section in sections if not _is_generated(section.path)]
    deletions = Counter(_parent(section.path) for section in rest if section.deleted)
    bulk_dirs = {directory for directory, count in deletions.items() if count >= BULK_DELETION_MIN_FILES}
    bulk: list[_DiffSection] = []
    ordinary: list[_DiffSection] = []
    for section in rest:
        (bulk if section.deleted and _parent(section.path) in bulk_dirs else ordinary).append(section)
    inlined, omitted = _within_cap(ordinary)
    if not (generated or bulk or omitted):
        return diff_text
    text = preamble + "".join(section.text for section in inlined)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + _summary_block(bulk, generated, omitted)


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
