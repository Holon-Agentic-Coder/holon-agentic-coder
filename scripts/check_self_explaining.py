#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    from scripts.self_explaining_comments import Finding, find_comments_and_docstrings
    from scripts.self_explaining_diff import (
        ChangedFile,
        RemovedFile,
        changed_files_against_merge_base,
        changed_files_between,
        merge_base,
        removed_files,
        staged_changed_files,
    )
except ImportError:
    from self_explaining_comments import Finding, find_comments_and_docstrings
    from self_explaining_diff import (
        ChangedFile,
        RemovedFile,
        changed_files_against_merge_base,
        changed_files_between,
        merge_base,
        removed_files,
        staged_changed_files,
    )

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
MISSING_MERGE_BASE_ERROR = (
    "self-explaining check failed: no merge base with origin/main, and none could be fetched from origin; "
    "make sure an origin remote exists, fetch origin main with full history and rerun"
)
MISSING_MERGE_BASE_EXIT = 2
VIOLATION_RULE = "code is self-explaining; only '# why:' reasons and machine directives are allowed"
STEP_SUMMARY_VARIABLE = "GITHUB_STEP_SUMMARY"
STEP_SUMMARY_ROWS = (
    ("files_checked", "Changed files checked"),
    ("added_docstrings", "Added docstrings"),
    ("added_comments", "Added comments (not '# why:')"),
    ("removed_docstrings", "Removed docstrings"),
    ("removed_comments", "Removed comments (not '# why:')"),
)


@dataclass(frozen=True)
class SelfExplainingCounts:
    files_checked: int
    added_docstrings: int
    added_comments: int
    removed_docstrings: int
    removed_comments: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def spans_any_line(finding: Finding, line_numbers: frozenset[int]) -> bool:
    return any(line in line_numbers for line in range(finding.first_line, finding.last_line + 1))


def touches_added_line(finding: Finding, changed_file: ChangedFile) -> bool:
    return spans_any_line(finding, changed_file.added_lines)


def is_docstring(finding: Finding) -> bool:
    return finding.kind.endswith("docstring")


def added_findings(changed_file: ChangedFile) -> list[Finding]:
    return [
        finding
        for finding in find_comments_and_docstrings(changed_file.path, changed_file.text)
        if touches_added_line(finding, changed_file)
    ]


def removed_findings(removed_file: RemovedFile) -> list[Finding]:
    return [
        finding
        for finding in find_comments_and_docstrings(removed_file.path, removed_file.text)
        if spans_any_line(finding, removed_file.removed_lines)
    ]


def violations_in(changed_file: ChangedFile) -> list[str]:
    return [
        f"{changed_file.path}:{finding.first_line}: added {finding.kind} ({VIOLATION_RULE})"
        for finding in added_findings(changed_file)
    ]


def count_findings(changed_files: list[ChangedFile], removed: list[RemovedFile]) -> SelfExplainingCounts:
    added = [finding for changed_file in changed_files for finding in added_findings(changed_file)]
    gone = [finding for removed_file in removed for finding in removed_findings(removed_file)]
    added_docstrings = sum(1 for finding in added if is_docstring(finding))
    removed_docstrings = sum(1 for finding in gone if is_docstring(finding))
    return SelfExplainingCounts(
        files_checked=len(changed_files),
        added_docstrings=added_docstrings,
        added_comments=len(added) - added_docstrings,
        removed_docstrings=removed_docstrings,
        removed_comments=len(gone) - removed_docstrings,
    )


def step_summary_markdown(counts: SelfExplainingCounts, passed: bool) -> str:
    values = counts.to_dict()
    rows = [f"| {label} | {values[key]} |" for key, label in STEP_SUMMARY_ROWS]
    status = "passed" if passed else "failed"
    return "\n".join(
        ["### Self-explaining check", "", f"Status: {status}", "", "| Count | Value |", "| --- | --- |", *rows, ""]
    )


def append_step_summary(markdown: str) -> None:
    summary_path = os.environ.get(STEP_SUMMARY_VARIABLE)
    if not summary_path:
        return
    try:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(markdown)
    except OSError as error:
        print(f"self-explaining check: could not append the step summary: {error}", file=sys.stderr)


def violations_by_file(changed_files: list[ChangedFile]) -> dict[str, list[str]]:
    violations_by_path = {}
    for changed_file in changed_files:
        violations = violations_in(changed_file)
        if violations:
            violations_by_path[changed_file.path] = violations
    return violations_by_path


def report_violations(violations_by_path: dict[str, list[str]]) -> None:
    for violations in violations_by_path.values():
        for violation in violations:
            print(violation, file=sys.stderr)
    for path, violations in violations_by_path.items():
        print(f"{path}: {len(violations)} added docstring(s)/comment(s)", file=sys.stderr)
    total = sum(len(violations) for violations in violations_by_path.values())
    print(f"{total} added docstring(s) or comment(s) found.", file=sys.stderr)


def parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fail when a change adds a docstring or an explanatory comment.")
    parser.add_argument("--staged", action="store_true", help="Compare the index with HEAD.")
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT, help="Repository root.")
    parser.add_argument("--head", help="Compare the merge base of REF with origin/main against REF, commit to commit.")
    parser.add_argument("--counts-json", action="store_true", help="Print the counts as JSON and exit 0.")
    return parser.parse_args(argv)


def files_to_count(arguments: argparse.Namespace) -> tuple[list[ChangedFile], list[RemovedFile]] | None:
    root = arguments.root
    if arguments.staged:
        return staged_changed_files(root), removed_files(root, "HEAD", "--cached")
    if arguments.head:
        base = merge_base(root, head=arguments.head)
        if base is None:
            return None
        return changed_files_between(root, base, arguments.head), removed_files(root, base, arguments.head)
    base = merge_base(root)
    if base is None:
        return None
    return changed_files_against_merge_base(root, base), removed_files(root, base)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    files = files_to_count(arguments)
    if files is None:
        print(MISSING_MERGE_BASE_ERROR, file=sys.stderr)
        return MISSING_MERGE_BASE_EXIT
    changed_files, removed = files
    counts = count_findings(changed_files, removed)
    if arguments.counts_json:
        print(json.dumps(counts.to_dict()))
        return 0
    violations_by_path = violations_by_file(changed_files)
    append_step_summary(step_summary_markdown(counts, passed=not violations_by_path))
    if violations_by_path:
        report_violations(violations_by_path)
        return 1
    print(f"Self-explaining check passed ({len(changed_files)} changed file(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
