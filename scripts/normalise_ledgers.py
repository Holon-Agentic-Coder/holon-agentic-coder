#!/usr/bin/env python3
"""Sort the canonical ledgers by ``created_at`` and spell every ``created_at`` in the canonical UTC ``Z`` form.

Write mode (the default) is the maintainer-authorised one-time reorder of intent
``I-1795694671-ledgers-sorted-chronologically``, an explicit exception to the append-only ledger rule: rows are sorted
by ``created_at`` parsed as a UTC instant (stable on the existing row order), and ``created_at`` is rewritten
textually inside the raw line, so no other byte of a row changes. A row without ``created_at`` stays right after the
row it followed. ``--check`` is the permanent guard: it exits 1 when a ledger is out of order or holds a
non-canonical ``created_at``.

Usage:
    uv run python scripts/normalise_ledgers.py [--check] [LEDGER ...]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEDGERS = tuple(
    REPO_ROOT / "holon-knowledge" / "ledger" / f"{name}.jsonl" for name in ("executions", "intents", "plans")
)

_CANONICAL = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
_FRACTION = re.compile(r"T\d{2}:\d{2}:\d{2}\.(\d+)")
_EARLIEST = datetime.min.replace(tzinfo=UTC)
_UTC_OFFSET = "+00:00"


def parse_instant(value: object) -> datetime | None:
    """Return ``value`` as a UTC datetime (``Z`` read as UTC, naive read as UTC), or None when it is not one."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", _UTC_OFFSET))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def canonical_created_at(value: object) -> str | None:
    """Return the canonical ``...Z`` spelling of ``created_at`` keeping its fractional digits; None if unparseable."""
    instant = parse_instant(value)
    if instant is None or not isinstance(value, str):
        return None
    if _CANONICAL.match(value):
        return value
    if value.endswith(_UTC_OFFSET) and _CANONICAL.match(value[: -len(_UTC_OFFSET)] + "Z"):
        return value[: -len(_UTC_OFFSET)] + "Z"
    fraction = _FRACTION.search(value)
    digits = min(len(fraction.group(1)), 6) if fraction else 0
    rendered = instant.strftime("%Y-%m-%dT%H:%M:%S")
    if digits:
        rendered += f".{instant.microsecond:06d}"[: digits + 1]
    return rendered + "Z"


def _created_at(line: str) -> object:
    row = json.loads(line)
    return row.get("created_at") if isinstance(row, dict) else None


def _respell(line: str) -> str:
    """Return ``line`` with its ``created_at`` value replaced by the canonical spelling, every other byte unchanged."""
    value = _created_at(line)
    canonical = canonical_created_at(value)
    if canonical is None or canonical == value:
        return line
    pattern = re.compile(r'("created_at"\s*:\s*")' + re.escape(json.dumps(value)[1:-1]) + '"')
    respelled, count = pattern.subn(lambda match: match.group(1) + canonical + '"', line)
    if count != 1:
        raise ValueError(f"created_at {value!r} occurs {count} times in ledger line: {line[:100]}")
    return respelled


def _sort_keys(lines: list[str]) -> list[tuple[datetime, int]]:
    """Return one ``(instant, index)`` key per line; an untimed line inherits the instant of the line before it."""
    keys: list[tuple[datetime, int]] = []
    previous = _EARLIEST
    for index, line in enumerate(lines):
        previous = parse_instant(_created_at(line)) or previous
        keys.append((previous, index))
    return keys


def normalise_lines(lines: list[str]) -> list[str]:
    """Return the non-blank ledger lines (without newlines) respelled and sorted by UTC ``created_at``, stably."""
    respelled = [_respell(line) for line in lines if line.strip()]
    keys = _sort_keys(respelled)
    return [respelled[index] for _instant, index in sorted(keys)]


def ledger_violations(lines: list[str]) -> list[str]:
    """Return one message per non-canonical ``created_at`` and per timed row earlier than a row before it."""
    violations: list[str] = []
    latest = _EARLIEST
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = _created_at(line)
        except json.JSONDecodeError:
            violations.append(f"line {number}: not a JSON object")
            continue
        if value is None:
            continue
        if canonical_created_at(value) != value:
            violations.append(f"line {number}: non-canonical created_at {value!r}")
        instant = parse_instant(value)
        if instant is not None and instant < latest:
            violations.append(f"line {number}: created_at {value!r} is earlier than a row before it")
        latest = max(latest, instant or latest)
    return violations


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _check(paths: list[Path]) -> int:
    failed = False
    for path in paths:
        for violation in ledger_violations(_read_lines(path)):
            print(f"{path}: {violation}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


def _normalise_file(path: Path) -> None:
    lines = [line for line in _read_lines(path) if line.strip()]
    respelled = [_respell(line) for line in lines]
    normalised = normalise_lines(lines)
    moved = sum(1 for before, after in zip(respelled, normalised, strict=True) if before != after)
    changed = sum(1 for before, after in zip(lines, respelled, strict=True) if before != after)
    temp_path = path.with_name(path.name + ".normalising")
    temp_path.write_text("".join(f"{line}\n" for line in normalised), encoding="utf-8")
    os.replace(temp_path, path)
    print(f"{path}: {len(normalised)} rows, {moved} rows changed position, {changed} created_at spellings changed")


def main(argv: list[str] | None = None) -> int:
    """Normalise (default) or ``--check`` the given ledgers, defaulting to the three canonical ledgers."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report disorder and non-canonical created_at; exit 1")
    parser.add_argument("ledgers", nargs="*", type=Path, help="ledger files (default: the three canonical ledgers)")
    args = parser.parse_args(argv)
    paths = args.ledgers or list(DEFAULT_LEDGERS)
    if args.check:
        return _check(paths)
    for path in paths:
        _normalise_file(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
