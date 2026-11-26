"""The ledger normaliser sorts by parsed UTC ``created_at``, respells it as ``Z`` and changes nothing else."""

from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

import pytest

from scripts.normalise_ledgers import (
    DEFAULT_LEDGERS,
    canonical_created_at,
    ledger_violations,
    main,
    normalise_lines,
    parse_instant,
)

_EXECUTION_ROW = (
    '{"execution_id": "E-1", "plan_branch": "I-1-x/P-1-a/_", "agent": "claude-agent", "status": "success", '
    '"summary": "Plan \\"executed\\" ok", "created_at": "%s", "metrics": {"cost_actual": 2.0}}'
)
_INTENT_ROW = '{"slug": "s%d", "description": "mentions +00:00 in text", "created_at": "%s"}'
_UNTIMED_ROW = '{"action_id": "SYNC-%d", "action": "target_sync", "outcome": "success"}'


def _fixture_lines() -> list[str]:
    lines = [
        _EXECUTION_ROW.replace("E-1", "E-a") % "2026-03-01T10:00:00.617715+00:00",
        _INTENT_ROW % (1, "2026-03-01T09:00:00.000Z"),
        _UNTIMED_ROW % 1,
        _EXECUTION_ROW.replace("E-1", "E-b") % "2026-03-01T11:00:00.000001Z",
        _INTENT_ROW % (2, "2026-03-01T10:00:00.617Z"),
        _INTENT_ROW % (3, "2026-03-01T10:00:00.617Z"),
        _EXECUTION_ROW.replace("E-1", "E-c") % "2026-02-28T23:59:59+00:00",
        _INTENT_ROW % (4, "2026-03-01T20:00:00.5+10:00"),
        _EXECUTION_ROW.replace("E-1", "E-d") % "2026-03-01T08:00:00.123456",
    ]
    random.Random(1795694671).shuffle(lines)
    return lines


def _parsed_rows(lines: list[str]) -> Counter[str]:
    rows = []
    for line in lines:
        row = json.loads(line)
        if "created_at" in row:
            row["created_at"] = parse_instant(row["created_at"]).isoformat()
        rows.append(json.dumps(row, sort_keys=True))
    return Counter(rows)


def _without_created_at_value(line: str) -> str:
    value = json.loads(line).get("created_at")
    return line if value is None else line.replace(f'"created_at": "{value}"', '"created_at": ""')


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-03-01T10:00:00.617715+00:00", "2026-03-01T10:00:00.617715Z"),
        ("2026-03-01T10:00:00.617Z", "2026-03-01T10:00:00.617Z"),
        ("2026-03-01T10:00:00Z", "2026-03-01T10:00:00Z"),
        ("2026-03-01T10:00:00.123456", "2026-03-01T10:00:00.123456Z"),
        ("2026-03-01T20:00:00.5+10:00", "2026-03-01T10:00:00.5Z"),
        ("2026-03-02T01:30:00-02:00", "2026-03-02T03:30:00Z"),
        ("not a timestamp", None),
        (None, None),
        (17, None),
    ],
)
def test_canonical_created_at_spells_utc_with_z_and_keeps_the_fraction(value: object, expected: str | None) -> None:
    assert canonical_created_at(value) == expected


def test_normalising_keeps_the_parsed_row_multiset_and_changes_only_created_at() -> None:
    lines = _fixture_lines()
    normalised = normalise_lines(lines)

    assert len(normalised) == len(lines)
    assert _parsed_rows(normalised) == _parsed_rows(lines)
    assert Counter(map(_without_created_at_value, normalised)) == Counter(map(_without_created_at_value, lines))
    assert ledger_violations(normalised) == []


def test_normalised_rows_are_in_instant_order_with_ties_and_untimed_rows_kept_in_place() -> None:
    lines = [
        _INTENT_ROW % (1, "2026-03-01T10:00:00.000Z"),
        _UNTIMED_ROW % 1,
        _INTENT_ROW % (2, "2026-03-01T09:00:00.000+00:00"),
        _INTENT_ROW % (3, "2026-03-01T10:00:00Z"),
        _INTENT_ROW % (4, "2026-03-01T08:00:00.000Z"),
        _UNTIMED_ROW % 2,
    ]

    normalised = normalise_lines(lines)

    order = [json.loads(line).get("slug") or json.loads(line)["action_id"] for line in normalised]
    assert order == ["s4", "SYNC-2", "s2", "s1", "SYNC-1", "s3"]
    instants = [parse_instant(json.loads(line).get("created_at")) for line in normalised]
    timed = [instant for instant in instants if instant is not None]
    assert timed == sorted(timed)
    assert ledger_violations(normalised) == []


def test_normalising_is_idempotent() -> None:
    once = normalise_lines(_fixture_lines())

    assert normalise_lines(once) == once
    assert ledger_violations(once) == []


def test_a_created_at_text_inside_another_field_is_left_alone() -> None:
    line = '{"note": "created_at 2026-03-01T10:00:00+00:00", "created_at": "2026-03-01T10:00:00+00:00"}'

    assert normalise_lines([line]) == [
        '{"note": "created_at 2026-03-01T10:00:00+00:00", "created_at": "2026-03-01T10:00:00Z"}'
    ]


def test_ledger_violations_name_disorder_and_offset_spelling() -> None:
    lines = [_INTENT_ROW % (1, "2026-03-01T10:00:00.000Z"), _INTENT_ROW % (2, "2026-03-01T09:00:00.000+00:00")]

    violations = ledger_violations(lines)

    assert len(violations) == 2
    assert all(message.startswith("line 2:") for message in violations)


def test_check_mode_fails_for_unsorted_or_offset_ledgers_and_passes_once_normalised(tmp_path: Path) -> None:
    unsorted = tmp_path / "unsorted.jsonl"
    unsorted.write_text(
        "".join(
            f"{line}\n"
            for line in [_INTENT_ROW % (1, "2026-03-02T00:00:00Z"), _INTENT_ROW % (2, "2026-03-01T00:00:00Z")]
        )
    )
    offset = tmp_path / "offset.jsonl"
    offset.write_text(_EXECUTION_ROW % "2026-03-01T10:00:00.617715+00:00" + "\n")
    fixture = tmp_path / "fixture.jsonl"
    fixture.write_text("".join(f"{line}\n" for line in _fixture_lines()))

    assert main(["--check", str(unsorted)]) == 1
    assert main(["--check", str(offset)]) == 1
    assert main(["--check", str(fixture)]) == 1

    assert main([str(unsorted), str(offset), str(fixture)]) == 0

    assert main(["--check", str(unsorted), str(offset), str(fixture)]) == 0
    assert fixture.read_text() == "".join(f"{line}\n" for line in normalise_lines(_fixture_lines()))


def test_the_committed_canonical_ledgers_pass_the_check() -> None:
    assert all(path.is_file() for path in DEFAULT_LEDGERS)
    assert main(["--check"]) == 0
