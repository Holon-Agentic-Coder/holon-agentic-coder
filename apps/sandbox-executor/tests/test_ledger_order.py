"""Instant parsing and instant-ordered merging of ledger rows (``sandbox_executor.ledger.order``)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sandbox_executor.ledger.order import merge_by_instant, parse_created_at


def _ids(rows: list[dict]) -> list[str]:
    return [row["id"] for row in rows]


def test_z_and_plus_zero_offset_are_the_same_instant() -> None:
    assert parse_created_at("2026-11-25T10:00:00Z") == parse_created_at("2026-11-25T10:00:00+00:00")


def test_other_offsets_are_converted_to_utc() -> None:
    assert parse_created_at("2026-11-25T11:00:00+01:00") == parse_created_at("2026-11-25T10:00:00Z")
    assert parse_created_at("2026-11-25T11:00:00+01:00").tzinfo is UTC


def test_naive_values_are_utc_and_fractional_z_parses() -> None:
    assert parse_created_at("2026-11-25T10:00:00") == datetime(2026, 11, 25, 10, tzinfo=UTC)
    assert parse_created_at("2026-11-25T10:00:00.250Z") == datetime(2026, 11, 25, 10, 0, 0, 250000, tzinfo=UTC)


@pytest.mark.parametrize("value", [None, 1764064800, "", "not a date", "2026-13-40T99:00:00Z", ["2026-11-25"]])
def test_garbage_and_non_strings_are_none(value: object) -> None:
    assert parse_created_at(value) is None


def test_a_z_fragment_row_lands_between_offset_canonical_rows_by_instant_not_by_string() -> None:
    canonical = [
        {"id": "c1", "created_at": "2026-11-25T10:30:00+01:00"},
        {"id": "c2", "created_at": "2026-11-25T10:15:00+00:00"},
    ]
    fragment = [{"id": "f1", "created_at": "2026-11-25T10:00:00Z"}]
    assert canonical[0]["created_at"] > "2026-11-25T10:00:00Z"
    assert _ids(merge_by_instant(canonical, fragment)) == ["c1", "f1", "c2"]


def test_an_offset_fragment_row_is_placed_by_its_utc_instant() -> None:
    canonical = [{"id": "c1", "created_at": "2026-11-25T10:30:00Z"}, {"id": "c2", "created_at": "2026-11-25T12:00:00Z"}]
    fragment = [{"id": "f1", "created_at": "2026-11-25T11:00:00+01:00"}]
    assert _ids(merge_by_instant(canonical, fragment)) == ["f1", "c1", "c2"]


def test_on_a_tie_canonical_comes_first_then_fragment_input_order() -> None:
    canonical = [{"id": "c1", "created_at": "2026-11-25T10:00:00+00:00"}]
    fragment = [{"id": "f1", "created_at": "2026-11-25T10:00:00Z"}, {"id": "f2", "created_at": "2026-11-25T10:00:00Z"}]
    assert _ids(merge_by_instant(canonical, fragment)) == ["c1", "f1", "f2"]


def test_fragment_rows_in_one_slot_are_ordered_by_instant() -> None:
    canonical = [{"id": "c1", "created_at": "2026-11-25T09:00:00Z"}]
    fragment = [{"id": "late", "created_at": "2026-11-25T12:00Z"}, {"id": "early", "created_at": "2026-11-25T10:00Z"}]
    assert _ids(merge_by_instant(canonical, fragment)) == ["c1", "early", "late"]


def test_untimestamped_fragment_rows_go_last_in_source_order() -> None:
    canonical = [{"id": "c1", "created_at": "2026-11-25T10:00:00Z"}]
    fragment = [{"id": "u1"}, {"id": "f1", "created_at": "2026-11-25T09:00:00Z"}, {"id": "u2", "created_at": "x"}]
    assert _ids(merge_by_instant(canonical, fragment)) == ["f1", "c1", "u1", "u2"]


def test_canonical_order_is_untouched_even_when_canonical_rows_are_out_of_order() -> None:
    canonical = [
        {"id": "c1", "created_at": "2026-11-25T12:00:00Z"},
        {"id": "c2", "created_at": "2026-11-25T08:00:00Z"},
        {"id": "c3"},
        {"id": "c4", "created_at": "2026-11-25T14:00:00Z"},
    ]
    fragment = [{"id": "f1", "created_at": "2026-11-25T09:00:00Z"}, {"id": "f2", "created_at": "2026-11-25T13:00:00Z"}]
    merged = merge_by_instant(canonical, fragment)
    assert [row for row in merged if row["id"].startswith("c")] == canonical
    assert _ids(merged) == ["f1", "c1", "c2", "c3", "f2", "c4"]


def test_no_fragment_rows_returns_the_canonical_rows() -> None:
    canonical = [{"id": "c2", "created_at": "2026-11-25T12:00:00Z"}, {"id": "c1", "created_at": "2026-11-25T08:00:00Z"}]
    assert merge_by_instant(canonical, []) == canonical
