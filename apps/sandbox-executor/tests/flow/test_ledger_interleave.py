from __future__ import annotations

import difflib
import json
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.flow.ledger_interleave import interleave_ledger_rows, resolve_ledger_only_conflicts
from sandbox_executor.flow.ledger_reconcile import ConflictResolutionError

from tests.flow.helpers import _git


def _row(name: str, created_at: str | None = None) -> str:
    row: dict[str, str] = {"id": name}
    if created_at is not None:
        row["created_at"] = created_at
    return json.dumps(row)


BASE = [_row("b1", "2026-11-01T00:00:00Z"), _row("b2", "2026-11-02T00:00:00Z")]


def _ids(lines: list[str]) -> list[str]:
    return [json.loads(line)["id"] for line in lines]


def _is_ordered_subsequence(kept: list[str], merged: list[str]) -> bool:
    remaining = iter(merged)
    return all(line in remaining for line in kept)


def _only_additions(before: list[str], after: list[str]) -> bool:
    return not any(line.startswith("- ") for line in difflib.ndiff(before, after))


def test_ours_is_kept_in_place_and_theirs_rows_are_inserted_on_created_at():
    ours = [*BASE, _row("o1", "2026-11-03T00:00:00Z"), _row("o2", "2026-11-05T00:00:00Z")]
    theirs = [*BASE, _row("t1", "2026-11-04T00:00:00Z"), _row("t2", "2026-11-06T00:00:00+00:00")]

    merged = interleave_ledger_rows(BASE, ours, theirs)

    assert _is_ordered_subsequence(ours, merged)
    assert _ids(merged) == ["b1", "b2", "o1", "t1", "o2", "t2"]


def test_an_incoming_row_older_than_the_base_tail_is_inserted_not_appended():
    ours = [*BASE, _row("o1", "2026-11-03T00:00:00Z")]
    theirs = [*BASE, _row("t-old", "2026-11-01T12:00:00Z")]

    merged = interleave_ledger_rows(BASE, ours, theirs)

    assert _ids(merged) == ["b1", "t-old", "b2", "o1"]
    assert [line for line in merged if line in ours] == ours
    assert _only_additions(ours, merged)


def test_sorted_sides_merge_sorted_and_as_additions_only():
    ours = [*BASE, _row("o1", "2026-11-03T00:00:00Z"), _row("o2", "2026-11-05T00:00:00.000000+00:00")]
    theirs = [*BASE, _row("t0", "2026-11-01T06:00:00Z"), _row("t1", "2026-11-04T00:00:00Z"), _row("t3", "2026-11-07")]

    merged = interleave_ledger_rows(BASE, ours, theirs)

    assert _ids(merged) == ["b1", "t0", "b2", "o1", "t1", "o2", "t3"]
    assert _is_ordered_subsequence(ours, merged)
    assert _is_ordered_subsequence(theirs[len(BASE) :], merged)
    assert _only_additions(ours, merged)


def test_each_side_keeps_its_own_order_and_ours_wins_a_tie():
    ours = [*BASE, _row("o-late", "2026-11-09T00:00:00Z"), _row("o-early", "2026-11-03T00:00:00Z")]
    theirs = [*BASE, _row("t-tie", "2026-11-09T00:00:00Z")]

    merged = interleave_ledger_rows(BASE, ours, theirs)

    assert [json.loads(line)["id"] for line in merged[2:]] == ["o-late", "o-early", "t-tie"]


def test_rows_without_created_at_follow_the_timed_rows_in_side_order():
    ours = [*BASE, _row("o-untimed"), _row("o1", "2026-11-03T00:00:00Z")]
    theirs = [*BASE, _row("t1", "2026-11-04T00:00:00Z"), "not json", _row("t2", "2026-11-05T00:00:00Z")]

    merged = interleave_ledger_rows(BASE, ours, theirs)

    assert merged[2:] == [ours[2], ours[3], theirs[2], theirs[3], theirs[4]]


def test_a_row_both_sides_appended_is_kept_once():
    shared = _row("shared", "2026-11-03T00:00:00Z")
    merged = interleave_ledger_rows(BASE, [*BASE, shared], [*BASE, shared, _row("t1", "2026-11-04T00:00:00Z")])
    assert merged.count(shared) == 1
    assert len(merged) == 4


def test_a_row_appended_by_one_side_is_kept_even_when_it_repeats_a_base_or_own_row():
    again = _row("again", "2026-11-03T00:00:00Z")
    ours = [*BASE, BASE[0], again, again]
    theirs = [*BASE, again]

    merged = interleave_ledger_rows(BASE, ours, theirs)

    assert merged[:2] == BASE
    assert merged[2:] == [BASE[0], again, again]


@pytest.mark.parametrize(
    "rewritten",
    [[BASE[1], BASE[0]], [BASE[0]], [BASE[0], _row("b2", "2026-11-02T00:00:01Z")]],
    ids=["reordered", "truncated", "rewritten"],
)
def test_a_side_that_changed_a_base_row_is_refused(rewritten):
    with pytest.raises(ConflictResolutionError, match="not append-only"):
        interleave_ledger_rows(BASE, [*BASE, _row("o1")], rewritten)


def _conflicting_repo(tmp_path: Path, extra_conflict: bool = False) -> Path:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True)
    ledger = repo / "holon-knowledge" / "ledger" / "executions.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("".join(f"{line}\n" for line in BASE), encoding="utf-8")
    (repo / "src.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "theirs")
    with ledger.open("a", encoding="utf-8") as f:
        f.write(_row("t1", "2026-11-04T00:00:00Z") + "\n")
    if extra_conflict:
        (repo / "src.txt").write_text("theirs\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "theirs")
    _git(repo, "checkout", "-q", "main")
    with ledger.open("a", encoding="utf-8") as f:
        f.write(_row("o1", "2026-11-03T00:00:00Z") + "\n")
    if extra_conflict:
        (repo / "src.txt").write_text("ours\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "ours")
    merge = subprocess.run(["git", "merge", "--no-ff", "--no-edit", "theirs"], cwd=repo, capture_output=True)
    assert merge.returncode != 0
    return repo


def test_a_real_conflicting_executions_ledger_is_resolved_and_staged(tmp_path):
    repo = _conflicting_repo(tmp_path)

    assert resolve_ledger_only_conflicts(str(repo)) is True

    content = (repo / "holon-knowledge" / "ledger" / "executions.jsonl").read_text(encoding="utf-8")
    assert content.splitlines() == [*BASE, _row("o1", "2026-11-03T00:00:00Z"), _row("t1", "2026-11-04T00:00:00Z")]
    assert _git(repo, "diff", "--name-only", "--diff-filter=U") == ""


def test_a_non_ledger_conflict_is_left_for_the_caller_to_abort(tmp_path):
    repo = _conflicting_repo(tmp_path, extra_conflict=True)

    assert resolve_ledger_only_conflicts(str(repo)) is False
    assert "executions.jsonl" in _git(repo, "diff", "--name-only", "--diff-filter=U")


def test_no_conflict_at_all_is_not_reported_as_resolved(tmp_path):
    repo = tmp_path / "clean"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True)
    assert resolve_ledger_only_conflicts(str(repo)) is False
