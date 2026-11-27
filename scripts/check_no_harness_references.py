#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

try:
    from sandbox_executor.harness_reference_patterns import find_references
except ImportError:
    # why: CI runs this file with plain Python and no installed package, so the source tree is put on the path.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "sandbox-executor" / "src"))
    from sandbox_executor.harness_reference_patterns import find_references

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_UNREADABLE_EXIT_CODE = 2


class DiffUnreadable(Exception):
    pass


def _diff_text(base: str, head: str, cwd: str) -> str:
    if not base.strip() or not head.strip():
        raise DiffUnreadable("base or head revision is empty")
    command = ["git", "diff", "--no-color", "--no-ext-diff", "--unified=0", f"{base}...{head}"]
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, errors="replace", check=False)
    except OSError as exc:
        raise DiffUnreadable(str(exc)) from exc
    if result.returncode != 0:
        raise DiffUnreadable(result.stderr.strip())
    return result.stdout


def added_lines(base: str, head: str, cwd: str) -> Iterator[tuple[str, int, str]]:
    path, number, in_header = "", 0, False
    for line in _diff_text(base, head, cwd).splitlines():
        if line.startswith("diff --git "):
            in_header = True
        elif in_header and line.startswith("+++ "):
            path = line[4:].removeprefix("b/")
        elif hunk := _HUNK_RE.match(line):
            number, in_header = int(hunk.group(1)), False
        elif line.startswith("+") and not in_header:
            yield path, number, line[1:]
            number += 1


def scan(lines: Iterator[tuple[str, int, str]], body: str) -> list[str]:
    findings = [
        f"{path}:{number}: {', '.join(labels)}" for path, number, text in lines if (labels := find_references(text))
    ]
    if labels := find_references(body):
        findings.append(f"pull request body: {', '.join(labels)}")
    return findings


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fail when a pull request adds a control-plane harness reference.")
    parser.add_argument("--base", required=True, help="Base revision of the pull request")
    parser.add_argument("--head", required=True, help="Head revision of the pull request")
    parser.add_argument("--body-env", default="PR_BODY", help="Environment variable holding the pull request body")
    parser.add_argument("--repo", default=".", help="Repository to read the diff from")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        findings = scan(added_lines(args.base, args.head, args.repo), os.environ.get(args.body_env, ""))
    except DiffUnreadable as exc:
        print(f"cannot read the pull request diff: {exc}")
        return _UNREADABLE_EXIT_CODE
    for finding in findings:
        print(finding)
    if findings:
        print(f"{len(findings)} harness reference(s) found; name the control plane's paths and task ids nowhere here.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
