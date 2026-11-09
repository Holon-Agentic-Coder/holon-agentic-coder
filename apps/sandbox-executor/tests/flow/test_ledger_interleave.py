"""Tests for ledger-only merge conflict resolution: base prefix kept, appended rows interleaved on ``created_at``."""

from __future__ import annotations

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


def test_base_prefix_is_kept_byte_for_byte_and_tails_interleave_on_created_at():
    ours = [*BASE, _row("o1", "2026-11-03T00:00:00Z"), _row("o2", "2026-11-05T00:00:00Z")]
    theirs = [*BASE, _row("t1", "2026-11-04T00:00:00Z"), _row("t2", "2026-11-06T00:00:00+00:00")]

    merged = interleave_ledger_rows(BASE, ours, theirs)

    assert merged[:2] == BASE
    assert [json.loads(line)["id"] for line in merged[2:]] == ["o1", "t1", "o2", "t2"]


def test_each_side_keeps_its_own_order_and_ours_wins_a_tie():
    # Ours appended out of time order: interleaving never reorders a side's own rows.
    ours = [*BASE, _row("o-late", "2026-11-09T00:00:00Z"), _row("o-early", "2026-11-03T00:00:00Z")]
    theirs = [*BASE, _row("t-tie", "2026-11-09T00:00:00Z")]

    merged = interleave_ledger_rows(BASE, ours, theirs)

    assert [json.loads(line)["id"] for line in merged[2:]] == ["o-late", "o-early", "t-tie"]


def test_rows_without_created_at_follow_the_timed_rows_in_side_order():
    ours = [*BASE, _row("o-untimed"), _row("o1", "2026-11-03T00:00:00Z")]
    theirs = [*BASE, _row("t1", "2026-11-04T00:00:00Z"), "not json", _row("t2", "2026-11-05T00:00:00Z")]

    merged = interleave_ledger_rows(BASE, ours, theirs)

    # An untimed head waits for the other side's timed rows; neither side's own order changes.
    assert merged[2:] == [theirs[2], ours[2], ours[3], theirs[3], theirs[4]]


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
    """A repository with a merge in progress whose executions.jsonl (and optionally src/app.py) conflict."""
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
    # Nothing was resolved behind the caller's back.
    assert "executions.jsonl" in _git(repo, "diff", "--name-only", "--diff-filter=U")


def test_no_conflict_at_all_is_not_reported_as_resolved(tmp_path):
    repo = tmp_path / "clean"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True)
    assert resolve_ledger_only_conflicts(str(repo)) is False
