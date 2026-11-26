#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from scripts.self_explaining_comments import Finding, find_comments_and_docstrings
    from scripts.self_explaining_diff import (
        ChangedFile,
        changed_files_against_merge_base,
        merge_base,
        staged_changed_files,
    )
except ImportError:
    from self_explaining_comments import Finding, find_comments_and_docstrings
    from self_explaining_diff import ChangedFile, changed_files_against_merge_base, merge_base, staged_changed_files

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
NO_MERGE_BASE_NOTICE = "no merge base with origin/main; self-explaining check skipped"
VIOLATION_RULE = "code is self-explaining; only '# why:' reasons and machine directives are allowed"


def touches_added_line(finding: Finding, changed_file: ChangedFile) -> bool:
    return any(line in changed_file.added_lines for line in range(finding.first_line, finding.last_line + 1))


def violations_in(changed_file: ChangedFile) -> list[str]:
    return [
        f"{changed_file.path}:{finding.first_line}: added {finding.kind} ({VIOLATION_RULE})"
        for finding in find_comments_and_docstrings(changed_file.path, changed_file.text)
        if touches_added_line(finding, changed_file)
    ]


def parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fail when a change adds a docstring or an explanatory comment.")
    parser.add_argument("--staged", action="store_true", help="Compare the index with HEAD.")
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT, help="Repository root.")
    return parser.parse_args(argv)


def changed_files_to_check(root: Path, staged: bool) -> list[ChangedFile] | None:
    if staged:
        return staged_changed_files(root)
    base = merge_base(root)
    return None if base is None else changed_files_against_merge_base(root, base)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    changed_files = changed_files_to_check(arguments.root, arguments.staged)
    if changed_files is None:
        print(NO_MERGE_BASE_NOTICE)
        return 0
    violations = [violation for changed_file in changed_files for violation in violations_in(changed_file)]
    for violation in violations:
        print(violation, file=sys.stderr)
    if violations:
        print(f"{len(violations)} added docstring(s) or comment(s) found.", file=sys.stderr)
        return 1
    print(f"Self-explaining check passed ({len(changed_files)} changed file(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
