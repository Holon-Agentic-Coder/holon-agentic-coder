#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ATTRIBUTION_PREFIXES = (
    "co-authored-by",
    "co-developed-by",
    "generated-by",
    "assisted-by",
    "generated with [claude code",
)
# why: Claude Code writes its footer as "🤖 Generated with [Claude Code](...)", so the emoji must not hide the prefix.
ROBOT_EMOJI = "\N{ROBOT FACE}"
RULE = "attribution trailers are not allowed in commit messages"
RECORD_SEPARATOR = "\x1e"
FIELD_SEPARATOR = "\x1f"


def is_comment_line(line: str) -> bool:
    return line.startswith("#")


def is_attribution_line(line: str) -> bool:
    text = line.lstrip()
    if text.startswith(ROBOT_EMOJI):
        text = text.removeprefix(ROBOT_EMOJI).lstrip()
    return text.lower().startswith(ATTRIBUTION_PREFIXES)


def find_attribution_lines(message: str) -> list[str]:
    return [line for line in message.splitlines() if not is_comment_line(line) and is_attribution_line(line)]


def commit_messages(revision_range: str) -> list[tuple[str, str]]:
    log = subprocess.run(
        ["git", "log", f"--format=%h{FIELD_SEPARATOR}%B{RECORD_SEPARATOR}", revision_range],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    records = [record.strip("\n") for record in log.split(RECORD_SEPARATOR)]
    return [tuple(record.split(FIELD_SEPARATOR, 1)) for record in records if record]


def offences_in_file(path: Path) -> list[tuple[str, str]]:
    return [(str(path), line) for line in find_attribution_lines(path.read_text(encoding="utf-8"))]


def offences_in_range(revision_range: str) -> list[tuple[str, str]]:
    return [
        (short_sha, line)
        for short_sha, message in commit_messages(revision_range)
        for line in find_attribution_lines(message)
    ]


def report(offences: list[tuple[str, str]]) -> int:
    for where, line in offences:
        print(f"{where}: {line}", file=sys.stderr)
    if offences:
        print(RULE, file=sys.stderr)
        return 1
    return 0


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reject attribution trailers in commit messages.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--message-file", type=Path, metavar="PATH")
    mode.add_argument("--range", dest="revision_range", metavar="RANGE")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.message_file is not None:
        return report(offences_in_file(args.message_file))
    try:
        return report(offences_in_range(args.revision_range))
    except subprocess.CalledProcessError as failure:
        print(failure.stderr, file=sys.stderr, end="")
        return 2


if __name__ == "__main__":
    sys.exit(main())
